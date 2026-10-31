"""Bounded, duplicate-safe bot delivery; publishing never waits on this I/O."""
import asyncio
import logging
import os
import uuid
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from django.conf import settings
from django.utils import timezone

from notifications.events import render
from notifications.models import BotNotification
from publishing.services.contention import bounded_transaction, retry_transition
from publishing.services.execution_lock import execution_lock

logger = logging.getLogger(__name__)


def obsolete(event):
    if event.phase_version is None:
        return False
    return (event.run_id is None or not event.run.active or not event.user.is_publishing
            or event.run.phase_version != event.phase_version)


def dispatch_notifications(*, publish=None, now=None):
    now = now or timezone.now()
    if publish is None:
        from notifications.tasks import deliver
        publish = lambda event_id, token: deliver.apply_async(args=[event_id, token], queue="notifications", retry=False)
    with execution_lock("notification-dispatcher", 0) as acquired:
        if not acquired:
            return 0
        expired = list(BotNotification.objects.filter(status__in=["queued", "sending"], expires_at__lte=now).values_list("pk", "status")[:8])
        for event_id, status in expired:
            with execution_lock("notification", event_id) as free:
                if free:
                    BotNotification.objects.filter(pk=event_id, status=status, expires_at__lte=now).update(
                        status="unknown" if status == "sending" else "pending", token=None,
                        finished_at=now if status == "sending" else None,
                        error_type="WorkerLost" if status == "sending" else "")
        available = max(0, settings.NOTIFICATION_MAX_PENDING - BotNotification.objects.filter(status__in=["queued", "sending"]).count())
        busy = BotNotification.objects.filter(status__in=["queued", "sending"]).values("user_id")
        due = list(BotNotification.objects.filter(status="pending", available_at__lte=now).exclude(user_id__in=busy).order_by("available_at", "created_at").values_list("pk", "user_id")[:8])
        users = set()
        count = 0
        for event_id, user_id in due:
            if len(users) >= available:
                break
            if user_id in users:
                continue
            token = uuid.uuid4()
            if not BotNotification.objects.filter(pk=event_id, status="pending").update(
                status="queued", token=token, expires_at=now + timedelta(seconds=30)):
                continue
            users.add(user_id)
            try:
                publish(str(event_id), str(token))
                count += 1
            except Exception as error:
                # Publication is ambiguous: keep this token usable until expiry.
                BotNotification.objects.filter(pk=event_id, token=token).update(error_type=type(error).__name__[:100])
                logger.warning("Notification publication failed (%s)", type(error).__name__)
        return count


@retry_transition
def claim_notification(event_id, token):
    from publishing.services.runs import _lock_user
    owner = BotNotification.objects.filter(pk=event_id).values_list("user__telegram_user_id", flat=True).first()
    if owner is None:
        return None
    with bounded_transaction():
        _lock_user(owner)
        event = BotNotification.objects.select_related("user", "run__account").filter(pk=event_id, status="queued", token=token).first()
        now = timezone.now()
        if event is None or event.expires_at <= now:
            return None
        if event.phase_version is not None and event.run_id and event.run.active and event.user.is_publishing:
            from publishing.models import PublishingSettings
            from publishing.services.work_rest import advance_phase
            advance_phase(event.run, PublishingSettings.objects.get(user_id=event.user_id), now)
            event.run.save()
        if obsolete(event):
            event.status = "obsolete"
            event.finished_at = now
            event.save(update_fields=["status", "finished_at"])
            return None
        text = render(event)
        event.status = "sending"
        event.attempts += 1
        event.expires_at = now + timedelta(seconds=30)
        event.save(update_fields=["status", "attempts", "expires_at"])
        return event.user.telegram_user_id, text


async def _send(chat_id, text):
    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    try:
        return await bot.send_message(chat_id, text, request_timeout=settings.NOTIFICATION_SEND_TIMEOUT)
    finally:
        try:
            await asyncio.wait_for(bot.session.close(), timeout=2)
        except (Exception, asyncio.CancelledError):
            logger.warning("Notification session cleanup failed")


async def _bounded_send(chat_id, text):
    return await asyncio.wait_for(_send(chat_id, text), timeout=settings.NOTIFICATION_SEND_TIMEOUT + 2)


def deliver_notification(event_id, token):
    with execution_lock("notification", event_id) as acquired:
        if not acquired:
            return
        payload = claim_notification(event_id, token)
        if payload is None:
            return
        now = timezone.now()
        values = {"status": "sent", "finished_at": now, "error_type": ""}
        try:
            message = asyncio.run(_bounded_send(*payload))
            values["telegram_message_id"] = message.message_id
        except TelegramRetryAfter as error:
            attempt = BotNotification.objects.get(pk=event_id).attempts
            values.update(status="pending" if attempt < 3 else "failed",
                available_at=timezone.now() + timedelta(seconds=error.retry_after),
                error_type="TelegramRetryAfter", token=None)
        except (TelegramForbiddenError, TelegramBadRequest, ValueError, KeyError) as error:
            values.update(status="failed", error_type=type(error).__name__)
        except Exception as error:
            # Timeout, lost response or server error can conceal acceptance.
            values.update(status="unknown", error_type=type(error).__name__)
        values["finished_at"] = timezone.now() if values["status"] != "pending" else None
        BotNotification.objects.filter(pk=event_id, token=token, status="sending").update(**values)
