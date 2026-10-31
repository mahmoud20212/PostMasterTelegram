"""Durable due-run dispatch. Redis messages are hints, never the schedule."""
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import OperationalError
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from publishing.models import PublishingAttempt, PublishingRun
from publishing.services.contention import bounded_transaction, retry_transition
from publishing.services.execution_lock import execution_lock
from publishing.services.runs import _lock_user, finish_run
from publishing.services.steps import complete_attempt
from publishing.services.work_rest import synchronize_due_phases
from users.models import User

logger = logging.getLogger(__name__)


def recover_expired(*, now=None, limit=None):
    now = now or timezone.now()
    expired = list(PublishingAttempt.objects.filter(
        status__in=PublishingAttempt.OPEN_STATUSES, expires_at__lte=now,
    ).order_by("expires_at").values_list("pk", "user_id")[:limit or settings.PUBLISHING_DISPATCH_BATCH])
    recovered = 0
    for attempt_id, user_id in expired:
        # A lease alone cannot prove that a worker has ceased network activity.
        with execution_lock("user", user_id) as acquired:
            if acquired:
                try:
                    recovered += bool(complete_attempt(attempt_id, "interrupted", error_type="WorkerLost", now=now))
                except OperationalError as error:
                    logger.warning("Attempt recovery deferred (%s)", type(error).__name__)
    return recovered


@retry_transition
def reserve_dispatch(run_id, *, now):
    owner = PublishingRun.objects.filter(pk=run_id).values_list("user__telegram_user_id", flat=True).first()
    if owner is None:
        return None
    with bounded_transaction():
        user = _lock_user(owner)
        run = PublishingRun.objects.filter(pk=run_id, active=True, scheduler_version=2, next_due_at__lte=now).first()
        if run is None or not user.is_publishing:
            return None
        if run.dispatch_token and run.dispatch_expires_at and run.dispatch_expires_at > now:
            return None
        if PublishingAttempt.objects.filter(user=user, status__in=PublishingAttempt.OPEN_STATUSES).exists():
            return None
        token = uuid.uuid4()
        run.dispatch_token = token
        run.dispatch_expires_at = now + timedelta(seconds=settings.PUBLISHING_DISPATCH_LEASE_SECONDS)
        run.last_dispatched_at = now
        run.dispatch_error = ""
        run.save(update_fields=["dispatch_token", "dispatch_expires_at", "last_dispatched_at", "dispatch_error"])
        return str(token)


def dispatch_due(*, publish=None, now=None):
    """One bounded fair pass. Safe from Beat, a command, or duplicate ticks."""
    now = now or timezone.now()
    if publish is None:
        from publishing.tasks import publish_step
        publish = lambda run_id, token: publish_step.apply_async(
            args=[str(run_id), token], queue="publishing", retry=False,
        )
    with execution_lock("dispatcher", 0) as acquired:
        if not acquired:
            return 0
        synchronize_due_phases(now, settings.PUBLISHING_DISPATCH_BATCH)
        # Subscription expiry and legacy UI paths may clear the compatibility
        # user flag. Close that generation without touching a concurrent restart.
        stopped = list(PublishingRun.objects.filter(active=True, scheduler_version=2,
            user__is_publishing=False).values_list("pk", "user__telegram_user_id")[:settings.PUBLISHING_DISPATCH_BATCH])
        for run_id, owner in stopped:
            try:
                finish_run(owner, run_id)
            except OperationalError as error:
                logger.warning("Stopped-run reconciliation deferred (%s)", type(error).__name__)
        recover_expired(now=now)
        # One database snapshot: a queue -> claim transition must not disappear
        # between two independent counts and accidentally enlarge the backlog.
        outstanding = User.objects.annotate(
            has_attempt=Exists(PublishingAttempt.objects.filter(user_id=OuterRef("pk"), status__in=PublishingAttempt.OPEN_STATUSES)),
            has_dispatch=Exists(PublishingRun.objects.filter(user_id=OuterRef("pk"), active=True,
                dispatch_token__isnull=False, dispatch_expires_at__gt=now)),
        ).filter(Q(has_attempt=True) | Q(has_dispatch=True)).count()
        available = max(0, settings.PUBLISHING_MAX_PENDING - outstanding)
        limit = min(settings.PUBLISHING_DISPATCH_BATCH, available)
        if not limit:
            return 0
        busy_users = PublishingAttempt.objects.filter(status__in=PublishingAttempt.OPEN_STATUSES).values("user_id")
        due = PublishingRun.objects.filter(
            active=True, scheduler_version=2, user__is_publishing=True, next_due_at__lte=now,
        ).exclude(work_rest_phase="rest").filter(Q(dispatch_token=None) | Q(dispatch_expires_at__lte=now)).exclude(user_id__in=busy_users)
        # One turn per user, oldest serviced first, independent of group count.
        candidates = list(due.order_by(
            F("last_dispatched_at").asc(nulls_first=True), "next_due_at", "created_at", "pk",
        ).values_list("pk", flat=True)[:limit])
        dispatched = 0
        for run_id in candidates:
            try:
                token = reserve_dispatch(run_id, now=now)
            except OperationalError as error:
                logger.warning("Dispatch transition failed (%s)", type(error).__name__)
                continue
            if token is None:
                continue
            try:
                publish(str(run_id), token)
                dispatched += 1
            except Exception as error:
                # The broker may have accepted this message. Keep its token valid;
                # a worker can claim it, or expiry permits a fresh notification.
                PublishingRun.objects.filter(pk=run_id, dispatch_token=token).update(dispatch_error=type(error).__name__[:100])
                logger.warning("Publishing notification failed for run %s (%s)", run_id, type(error).__name__)
        return dispatched
