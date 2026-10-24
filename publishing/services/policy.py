from datetime import timedelta

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from subscriptions.services.subscriptions import _ensure_subscription, _expire_subscription_if_needed
from users.models import User

MIN_INTERVAL_SECONDS = 30


def _normalise(plan) -> dict:
    minimum = max(MIN_INTERVAL_SECONDS, int(plan.min_interval_seconds or MIN_INTERVAL_SECONDS))
    maximum = max(minimum, int(plan.max_interval_seconds or minimum))
    return {
        "min_interval_seconds": minimum,
        "max_interval_seconds": maximum,
        "daily_send_limit": plan.daily_send_limit,
        "cycle_rest_seconds": max(0, int(plan.cycle_rest_seconds or 0)),
    }


@sync_to_async
def get_policy(telegram_user_id: int) -> dict:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    subscription = _expire_subscription_if_needed(_ensure_subscription(user))
    return _normalise(subscription.plan_definition)


def _reset_if_needed(user: User, now):
    if user.daily_send_reset_at is None or user.daily_send_reset_at <= now:
        user.daily_send_count = 0
        user.daily_send_reset_at = now + timedelta(days=1)
        user.daily_limit_notified_at = None


@sync_to_async
def reserve_send_slot(telegram_user_id: int, limit: int | None) -> bool:
    with transaction.atomic():
        user = User.objects.select_for_update().get(telegram_user_id=telegram_user_id)
        now = timezone.now()
        _reset_if_needed(user, now)
        if limit is not None and user.daily_send_count >= limit:
            user.save(update_fields=["daily_send_count", "daily_send_reset_at", "daily_limit_notified_at"])
            return False
        user.daily_send_count += 1
        user.save(update_fields=["daily_send_count", "daily_send_reset_at", "daily_limit_notified_at"])
        return True


@sync_to_async
def release_send_slot(telegram_user_id: int) -> None:
    with transaction.atomic():
        user = User.objects.select_for_update().get(telegram_user_id=telegram_user_id)
        user.daily_send_count = max(0, user.daily_send_count - 1)
        user.save(update_fields=["daily_send_count"])


@sync_to_async
def mark_limit_notified(telegram_user_id: int) -> None:
    User.objects.filter(telegram_user_id=telegram_user_id).update(daily_limit_notified_at=timezone.now())
