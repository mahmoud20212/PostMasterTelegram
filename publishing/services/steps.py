"""Short, rollback-safe state transitions. No broker or Telegram I/O here."""
import hashlib
import random
from datetime import timedelta

from django.conf import settings as django_settings
from django.utils import timezone

from messages.models import SavedMessage
from publishing.models import PublishingAttempt, PublishingCooldown, PublishingRun, PublishingSettings, PublishLog
from publishing.services.contention import bounded_transaction, retry_transition
from publishing.services.planner import cycle_pairs
from publishing.services.work_rest import advance_phase
from notifications.events import emit
from publishing.services.policy import _normalise, _reset_if_needed
from publishing.services.runs import ELIGIBLE_GROUP_STATUSES, _lock_user, _subscription_allowed
from subscriptions.models import Subscription
from telegram_accounts.models import TelegramAccount
from telegram_groups.models import TelegramGroup


def fingerprint(ciphertext):
    return hashlib.sha256(ciphertext.encode()).hexdigest()


def _stop(run, user, now):
    run.active = False
    run.stopped_at = now
    run.dispatch_token = None
    run.dispatch_expires_at = None
    run.save()
    type(user).objects.filter(pk=user.pk).update(is_publishing=False)
    PublishingSettings.objects.filter(user=user).update(is_running=False)


def _policy(run, user, now):
    account = run.account
    if (not run.active or not user.is_publishing or user.status != "active"
            or account is None or account.user_id != user.pk
            or account.status != "connected" or not account.encrypted_session
            or (account.paused_until and account.paused_until > now)):
        return None
    subscription = Subscription.objects.select_related("plan_definition").filter(user=user).first()
    if subscription is None or not _subscription_allowed(subscription):
        return None
    return _normalise(subscription.plan_definition)


def _end_cycle(run, publishing_settings, rest_seconds, now):
    # One pass per cycle. Missed deadlines do not cause catch-up bursts.
    due = max(now, run.cycle_deadline or now) + timedelta(seconds=rest_seconds)
    run.cycle_plan = []
    run.cycle_position = 0
    run.cycle_deadline = None
    run.next_due_at = due
    publishing_settings.cycle_started_at = None
    publishing_settings.next_cycle_at = due
    publishing_settings.save(update_fields=["cycle_started_at", "next_cycle_at"])


@retry_transition
def claim_step(run_id, dispatch_token, *, now=None):
    now = now or timezone.now()
    owner = PublishingRun.objects.filter(pk=run_id).values_list("user__telegram_user_id", flat=True).first()
    if owner is None:
        return None
    with bounded_transaction():
        user = _lock_user(owner)
        run = PublishingRun.objects.select_related("account").filter(
            pk=run_id, user=user, active=True, scheduler_version=2,
            dispatch_token=dispatch_token,
        ).first()
        if (run is None or not user.is_publishing or not run.dispatch_expires_at
                or run.dispatch_expires_at <= now or run.next_due_at > now):
            return None
        # Across all generations, including a stopped run whose RPC is in flight.
        if PublishingAttempt.objects.filter(user=user, status__in=PublishingAttempt.OPEN_STATUSES).exists():
            return None
        publishing_settings = PublishingSettings.objects.get(user=user)
        advance_phase(run, publishing_settings, now)
        run.dispatch_token = None
        run.dispatch_expires_at = None
        if run.work_rest_phase == "rest":
            run.save()
            return None
        account = run.account
        if account and account.user_id == user.pk and account.status == "paused_flood_wait":
            if account.paused_until and account.paused_until > now:
                run.next_due_at = account.paused_until
                run.save()
                return None
            if account.paused_until:
                emit(user.pk, "flood_ended", f"flood-ended:{account.pk}:{account.paused_until.isoformat()}", run=run)
                account.status = "connected"
                account.paused_until = None
                account.restriction_type = ""
                account.save(update_fields=["status", "paused_until", "restriction_type"])
        policy = _policy(run, user, now)
        if policy is None:
            _stop(run, user, now)
            return None
        if run.cycle_resume_pending:
            run.cycle_plan = []
            run.cycle_position = 0
            run.cycle_deadline = None
            run.cycle_resume_pending = False
            publishing_settings.cycle_started_at = None
            publishing_settings.save(update_fields=["cycle_started_at"])
        if publishing_settings.next_cycle_at and publishing_settings.next_cycle_at > now:
            run.next_due_at = publishing_settings.next_cycle_at
            run.save()
            return None
        if run.cycle_plan and run.cycle_deadline <= now:
            _end_cycle(run, publishing_settings, policy["cycle_rest_seconds"], now)
            run.save()
            return None
        if not run.cycle_plan:
            messages = list(SavedMessage.objects.filter(user=user, is_active=True).order_by("created_at", "pk"))
            groups = list(TelegramGroup.objects.filter(user=user, is_selected=True, status__in=ELIGIBLE_GROUP_STATUSES).order_by("created_at", "pk"))
            if not messages or not groups:
                run.next_due_at = now + timedelta(seconds=60)
                run.save()
                return None
            run.cycle_plan = [
                {"message_id": messages[m].pk, "group_id": groups[g].pk,
                 "next_message_index": (m + 1) % len(messages),
                 "next_group_index": (g + 1) % len(groups)}
                for m, g in cycle_pairs(message_count=len(messages), group_count=len(groups),
                                        message_index=publishing_settings.current_message_index,
                                        group_index=publishing_settings.current_group_index)
            ]
            run.cycle_position = 0
            duration = timedelta(minutes=max(1, publishing_settings.cycle_duration_minutes))
            start = publishing_settings.cycle_started_at
            if start is None or start + duration <= now:
                start = now
            run.cycle_deadline = start + duration
            publishing_settings.cycle_started_at = start
            publishing_settings.next_cycle_at = None
            publishing_settings.save(update_fields=["cycle_started_at", "next_cycle_at"])
        item = run.cycle_plan[run.cycle_position]
        message = SavedMessage.objects.filter(pk=item["message_id"], user=user, is_active=True).first()
        group = TelegramGroup.objects.filter(pk=item["group_id"], user=user, is_selected=True, status__in=ELIGIBLE_GROUP_STATUSES).first()
        attempt = PublishingAttempt.objects.create(
            run=run, user=user, account=account, message=message, group=group,
            position=run.cycle_position, sequence=run.next_sequence,
            expires_at=now + timedelta(seconds=django_settings.PUBLISHING_LEASE_SECONDS),
            target_identifier=group.identifier if group else "",
            message_text=message.text if message else "", message_entities=message.entities if message else [],
            session_fingerprint=fingerprint(account.encrypted_session),
            next_message_index=item["next_message_index"], next_group_index=item["next_group_index"],
            interval_seconds=random.randint(policy["min_interval_seconds"], policy["max_interval_seconds"]),
            cycle_rest_seconds=policy["cycle_rest_seconds"],
        )
        run.next_sequence += 1
        run.save()
        cooldown = group and PublishingCooldown.objects.filter(account=account, group=group, until__gt=now).exists()
        return {"attempt_id": str(attempt.pk), "account_id": account.pk,
                "encrypted_session": account.encrypted_session,
                "target": attempt.target_identifier, "text": attempt.message_text,
                "entities": attempt.message_entities,
                "skip": "IneligibleTarget" if not message or not group else "SlowModeCooldown" if cooldown else ""}


@retry_transition
def begin_send(attempt_id, *, now=None):
    """Last permission gate; reservation and 'may have sent' marker commit together."""
    now = now or timezone.now()
    owner = PublishingAttempt.objects.filter(pk=attempt_id).values_list("user__telegram_user_id", flat=True).first()
    if owner is None:
        return False
    with bounded_transaction():
        user = _lock_user(owner)
        attempt = PublishingAttempt.objects.select_related("run__account").get(pk=attempt_id)
        if attempt.status != "claimed" or attempt.expires_at <= now:
            return False
        run = attempt.run
        policy = _policy(run, user, now)
        if policy is None:
            if run.active:
                _stop(run, user, now)
            return False
        publishing_settings = PublishingSettings.objects.get(user=user)
        advance_phase(run, publishing_settings, now)
        run.save()
        if run.work_rest_phase == "rest":
            return "defer"
        if (run.account_id != attempt.account_id
                or fingerprint(run.account.encrypted_session) != attempt.session_fingerprint
                or not TelegramGroup.objects.filter(pk=attempt.group_id, user=user, is_selected=True, status__in=ELIGIBLE_GROUP_STATUSES).exists()
                or not SavedMessage.objects.filter(pk=attempt.message_id, user=user, is_active=True).exists()):
            return False
        _reset_if_needed(user, now)
        limit = policy["daily_send_limit"]
        if limit is not None and user.daily_send_count >= limit:
            user.daily_limit_notified_at = now
            user.save(update_fields=["daily_send_count", "daily_send_reset_at", "daily_limit_notified_at"])
            emit(user.pk, "daily_limit", f"quota:{user.pk}:{user.daily_send_reset_at.isoformat()}", run=run)
            _stop(run, user, now)
            return False
        user.daily_send_count += 1
        user.save(update_fields=["daily_send_count", "daily_send_reset_at", "daily_limit_notified_at"])
        attempt.quota_reserved = True
        attempt.quota_reset_at = user.daily_send_reset_at
        attempt.status = "sending"
        attempt.sending_at = now
        attempt.save(update_fields=["quota_reserved", "quota_reset_at", "status", "sending_at"])
        return True


@retry_transition
def complete_attempt(attempt_id, outcome, *, error_type="", restriction_seconds=None,
                     telegram_message_id=None, group_rejected=False, now=None):
    now = now or timezone.now()
    owner = PublishingAttempt.objects.filter(pk=attempt_id).values_list("user__telegram_user_id", flat=True).first()
    if owner is None:
        return False
    with bounded_transaction():
        user = _lock_user(owner)
        attempt = PublishingAttempt.objects.select_related("run", "account").get(pk=attempt_id)
        if attempt.status not in PublishingAttempt.OPEN_STATUSES:
            return False
        if outcome == "interrupted":
            outcome = "unknown" if attempt.status == "sending" else "failed"
        if outcome not in {"success", "failed", "unknown", "skipped", "deferred"}:
            raise ValueError("Invalid publishing outcome")
        # A confirmed rejection refunds only its own quota window. Unknown counts.
        if attempt.quota_reserved and outcome in {"failed", "skipped", "deferred"}:
            if user.daily_send_reset_at == attempt.quota_reset_at:
                user.daily_send_count = max(0, user.daily_send_count - 1)
                user.save(update_fields=["daily_send_count"])
            attempt.quota_reserved = False
        attempt.status = outcome
        attempt.error_type = error_type[:100]
        attempt.finished_at = now
        attempt.telegram_message_id = telegram_message_id
        attempt.save(update_fields=["status", "error_type", "finished_at", "telegram_message_id", "quota_reserved"])
        PublishLog.objects.create(
            attempt=attempt, user=user, account_id=attempt.account_id,
            group_id=attempt.group_id, message_id=attempt.message_id, status="skipped" if outcome == "deferred" else outcome,
            error_type=attempt.error_type, restriction_seconds=restriction_seconds,
        )
        run = attempt.run
        if outcome == "deferred":
            return True
        account = attempt.account
        same_session = account and fingerprint(account.encrypted_session) == attempt.session_fingerprint
        if same_session and error_type in {"FloodWaitError", "PeerFloodError", "UnauthorizedSession", "AuthKeyUnregisteredError"}:
            account.status = {"FloodWaitError": "paused_flood_wait", "PeerFloodError": "restricted"}.get(error_type, "unauthorized")
            account.paused_until = now + timedelta(seconds=max(1, restriction_seconds or 0)) if error_type == "FloodWaitError" else None
            account.last_error = error_type
            account.last_error_at = now
            account.restriction_type = account.status
            account.save(update_fields=["status", "paused_until", "last_error", "last_error_at", "restriction_type"])
            kind = "flood_started" if error_type == "FloodWaitError" else "account_restricted" if error_type == "PeerFloodError" else "session_invalid"
            emit(user.pk, kind, f"account-event:{attempt.pk}", run=run,
                 payload={"until": account.paused_until.isoformat() if account.paused_until else None,
                          "timezone": PublishingSettings.objects.get(user=user).timezone})
        if error_type == "SlowModeWaitError" and account and attempt.group_id:
            PublishingCooldown.objects.update_or_create(account=account, group_id=attempt.group_id,
                defaults={"until": now + timedelta(seconds=max(1, restriction_seconds or 0))})
            from notifications.models import BotNotification
            if not BotNotification.objects.filter(user=user, kind="slow_mode", created_at__gt=now - timedelta(minutes=30)).exists():
                emit(user.pk, "slow_mode", f"slow:{attempt.pk}", run=run)
        if group_rejected and attempt.group_id:
            TelegramGroup.objects.filter(pk=attempt.group_id, user=user).update(can_publish=False, status="disabled", last_error=error_type)
        # Never let completion of an old generation mutate a replacement run.
        if run.next_sequence == attempt.sequence + 1 and run.cycle_position == attempt.position:
            run.cycle_position += 1
            if run.active and user.is_publishing:
                publishing_settings = PublishingSettings.objects.get(user=user)
                publishing_settings.current_message_index = attempt.next_message_index
                publishing_settings.current_group_index = attempt.next_group_index
                publishing_settings.save(update_fields=["current_message_index", "current_group_index"])
                if run.cycle_position >= len(run.cycle_plan) or run.cycle_deadline <= now:
                    _end_cycle(run, publishing_settings, attempt.cycle_rest_seconds, now)
                else:
                    run.next_due_at = min(now + timedelta(seconds=attempt.interval_seconds), run.cycle_deadline)
                if same_session and error_type == "FloodWaitError":
                    run.next_due_at = max(run.next_due_at, account.paused_until)
                elif same_session and error_type in {"PeerFloodError", "UnauthorizedSession", "AuthKeyUnregisteredError"}:
                    _stop(run, user, now)
            run.save()
        return True
