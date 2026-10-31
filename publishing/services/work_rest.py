"""Elapsed-time work/rest phases. Call advance/reset under the user's DB lock."""
from datetime import timedelta
import logging

from django.db import OperationalError
from django.db.models import Q
from django.utils import timezone

from notifications.events import emit
from publishing.models import PublishingRun, PublishingSettings
from publishing.services.contention import bounded_transaction, retry_transition

logger = logging.getLogger(__name__)


def reset_work(run, config, now):
    if run.work_rest_phase == "rest":
        run.cycle_resume_pending = True
    run.phase_version += 1
    run.work_rest_phase = "work" if config.sleep_enabled else "off"
    run.phase_started_at = now if config.sleep_enabled else None
    run.phase_ends_at = now + timedelta(hours=config.work_hours) if config.sleep_enabled else None
    run.dispatch_token = None
    run.dispatch_expires_at = None


def advance_phase(run, config, now):
    if not config.sleep_enabled:
        if run.work_rest_phase != "off":
            reset_work(run, config, now)
        return
    if run.work_rest_phase == "off" or run.phase_ends_at is None:
        reset_work(run, config, now)
        return
    if now < run.phase_ends_at:
        return
    work = timedelta(hours=config.work_hours)
    rest = timedelta(hours=config.sleep_hours)
    anchor = run.phase_started_at if run.work_rest_phase == "work" else run.phase_started_at - work
    cycles, remainder = divmod(now - anchor, work + rest)
    sleeping = remainder >= work
    previous = run.work_rest_phase
    crossed = 2 * cycles + int(sleeping) - int(previous == "rest")
    run.phase_version += max(1, crossed)
    run.work_rest_phase = "rest" if sleeping else "work"
    run.phase_started_at = anchor + cycles * (work + rest) + (work if sleeping else timedelta())
    run.phase_ends_at = run.phase_started_at + (rest if sleeping else work)
    run.dispatch_token = None
    run.dispatch_expires_at = None
    run.cycle_resume_pending = True
    if run.work_rest_phase != previous:
        kind = "sleep_started" if sleeping else "sleep_ended"
        emit(run.user_id, kind, f"phase:{run.pk}:{run.phase_version}", run=run,
             phase_version=run.phase_version,
             payload={"until": run.phase_ends_at.isoformat(), "timezone": config.timezone})


@retry_transition
def synchronize_run(run_id, *, now=None):
    from publishing.services.runs import _lock_user
    now = now or timezone.now()
    owner = PublishingRun.objects.filter(pk=run_id).values_list("user__telegram_user_id", flat=True).first()
    if owner is None:
        return
    with bounded_transaction():
        user = _lock_user(owner)
        run = PublishingRun.objects.filter(pk=run_id, active=True, scheduler_version=2).first()
        if run is None or not user.is_publishing:
            return
        config = PublishingSettings.objects.get(user=user)
        advance_phase(run, config, now)
        account = run.account
        if (account and account.user_id == user.pk and account.status == "paused_flood_wait"
                and account.paused_until and account.paused_until <= now):
            emit(user.pk, "flood_ended", f"flood-ended:{account.pk}:{account.paused_until.isoformat()}", run=run)
            account.status = "connected"
            account.paused_until = None
            account.restriction_type = ""
            account.save(update_fields=["status", "paused_until", "restriction_type"])
        run.save()


def synchronize_due_phases(now, limit):
    due = PublishingRun.objects.filter(active=True, scheduler_version=2, user__is_publishing=True).filter(
        Q(user__publishing_settings__sleep_enabled=True) & (Q(work_rest_phase="off") | Q(phase_ends_at__lte=now))
        | Q(user__publishing_settings__sleep_enabled=False) & ~Q(work_rest_phase="off")
        | Q(account__status="paused_flood_wait", account__paused_until__lte=now)
    ).order_by("phase_ends_at", "created_at").values_list("pk", flat=True)[:limit]
    for run_id in list(due):
        try:
            synchronize_run(run_id, now=now)
        except OperationalError as error:
            logger.warning("Timed state transition deferred (%s)", type(error).__name__)
