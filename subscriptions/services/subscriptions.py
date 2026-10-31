from datetime import timedelta
import time
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.db import OperationalError, transaction
from django.utils import timezone

from messages.models import SavedMessage
from subscriptions.models import PaymentTransaction, PlanDuration, Subscription, SubscriptionPlan
from telegram_groups.models import TelegramGroup
from users.models import User
from publishing.services.contention import bounded_transaction, is_sqlite_contention


class SubscriptionLimitExceeded(ValueError):
    def __init__(self, resource: str, limit: int):
        self.resource = resource
        self.limit = limit
        super().__init__(f"تم الوصول إلى حد {resource}: {limit}.")


def _limits_for_plan(plan: SubscriptionPlan) -> dict[str, int | None]:
    return {
        "accounts": plan.max_accounts,
        "groups": plan.max_groups,
        "messages": plan.max_messages,
    }


def _free_plan() -> SubscriptionPlan:
    plan, _ = SubscriptionPlan.objects.get_or_create(
        code="free",
        defaults={
            "name": "المجاني",
            "description": "الخطة الأساسية المجانية",
            "is_free": True,
            "max_accounts": 1,
            "max_groups": 3,
            "max_messages": 1,
            "min_interval_seconds": 30,
            "max_interval_seconds": 60,
            "daily_send_limit": 30,
            "cycle_rest_seconds": 3600,
        },
    )
    return plan


def _ensure_subscription(user: User) -> Subscription:
    free_plan = _free_plan()
    subscription, _ = Subscription.objects.get_or_create(
        user=user,
        defaults={
            "plan": free_plan.code,
            "plan_definition": free_plan,
            "status": "active",
            "limits_snapshot": _limits_for_plan(free_plan),
        },
    )
    if subscription.plan_definition_id is None:
        subscription.plan_definition = free_plan
        subscription.duration = None
        subscription.plan = free_plan.code
        subscription.limits_snapshot = _limits_for_plan(free_plan)
        subscription.save(update_fields=["plan_definition", "plan", "limits_snapshot"])
    return subscription


def _expire_subscription_if_needed(subscription: Subscription) -> Subscription:
    from publishing.services.runs import _lock_user
    with transaction.atomic():
        _lock_user(subscription.user.telegram_user_id)
        locked = Subscription.objects.select_for_update(of=("self",)).select_related("user", "plan_definition").get(pk=subscription.pk)
        return _expire_subscription_locked(locked)


def _expire_subscription_locked(subscription: Subscription) -> Subscription:
    if subscription.end_date and subscription.end_date <= timezone.now() and subscription.expired_at is None:
        expiry_key = f"subscription-expired:{subscription.pk}:{subscription.end_date.isoformat()}"
        free_plan = _free_plan()
        user = subscription.user
        user.is_publishing = False
        user.current_plan = free_plan.code
        user.save(update_fields=["is_publishing", "current_plan"])
        SavedMessage.objects.filter(user=user).delete()
        TelegramGroup.objects.filter(user=user).delete()
        subscription.plan_definition = free_plan
        subscription.plan = free_plan.code
        subscription.status = "active"
        subscription.start_date = None
        subscription.end_date = None
        subscription.limits_snapshot = _limits_for_plan(free_plan)
        subscription.expired_at = timezone.now()
        subscription.save(update_fields=[
            "plan_definition", "duration", "plan", "status", "start_date", "end_date",
            "limits_snapshot", "expired_at",
        ])
        from notifications.events import emit
        emit(user.pk, "subscription_expired", expiry_key)
    return subscription


@sync_to_async
def get_subscription(*, telegram_user_id: int) -> Subscription:
    with transaction.atomic():
        user = User.objects.select_for_update().get(telegram_user_id=telegram_user_id)
        subscription = _ensure_subscription(user)
        subscription = Subscription.objects.select_related("plan_definition", "user").get(pk=subscription.pk)
        return _expire_subscription_if_needed(subscription)


def _check_limit(*, telegram_user_id: int, resource: str) -> None:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    subscription = _expire_subscription_if_needed(_ensure_subscription(user))
    limit = (subscription.limits_snapshot or {}).get(resource)
    if limit is None:
        return
    counts = {
        "accounts": user.telegram_accounts.count(),
        "groups": user.telegram_groups.count(),
        "messages": user.saved_messages.count(),
    }
    if counts[resource] >= limit:
        raise SubscriptionLimitExceeded(resource, limit)


check_limit_sync = _check_limit
check_limit = sync_to_async(_check_limit)


@sync_to_async
def list_active_plans() -> list[SubscriptionPlan]:
    return list(SubscriptionPlan.objects.filter(is_active=True).prefetch_related("durations"))


@sync_to_async
def list_plan_durations(*, plan_id: int, payment_method: str | None = None) -> list[PlanDuration]:
    filters = {"plan_id": plan_id, "is_active": True}
    if payment_method:
        filters["payment_method"] = payment_method
    return list(PlanDuration.objects.filter(**filters))


def is_valid_payment_offer(duration: PlanDuration) -> bool:
    """Validate catalogue rows even if they bypassed Model.clean (e.g. imports)."""
    if not duration.is_active or duration.days <= 0 or duration.price <= 0:
        return False
    if duration.payment_method == "manual":
        return duration.currency == "USD"
    return (
        duration.payment_method == "telegram_stars"
        and duration.currency == "XTR"
        and duration.price == duration.price.to_integral_value()
    )


@sync_to_async
def mark_expired_subscriptions() -> int:
    total = 0
    expired = Subscription.objects.filter(
        end_date__isnull=False,
        end_date__lte=timezone.now(),
        expired_at__isnull=True,
    )
    for subscription in expired:
        with transaction.atomic():
            from publishing.services.runs import _lock_user
            _lock_user(subscription.user.telegram_user_id)
            locked = Subscription.objects.select_for_update().select_related("user").get(pk=subscription.pk)
            if locked.expired_at is None:
                _expire_subscription_locked(locked)
                total += 1
    return total


def converted_remaining_days(*, remaining_days: int, old_duration: PlanDuration, new_duration: PlanDuration) -> int:
    if remaining_days <= 0:
        return 0
    old_daily_value = old_duration.price / old_duration.days
    new_daily_price = new_duration.price / new_duration.days
    if new_daily_price <= 0:
        return 0
    return int((remaining_days * old_daily_value) / new_daily_price)


@sync_to_async
def create_pending_payment(*, telegram_user_id: int, duration_id: int, payment_method: str) -> PaymentTransaction:
    if payment_method == "manual":
        return _pending_manual_payment(telegram_user_id=telegram_user_id, duration_id=duration_id)
    user = User.objects.get(telegram_user_id=telegram_user_id)
    duration = PlanDuration.objects.select_related("plan").get(
        pk=duration_id, is_active=True, plan__is_active=True, plan__is_free=False,
    )
    if duration.payment_method != payment_method or not is_valid_payment_offer(duration):
        raise ValueError("Payment offer is unavailable.")
    payment = PaymentTransaction.objects.create(
        user=user,
        plan=duration.plan,
        duration=duration,
        payment_method=payment_method,
        amount=duration.price,
        currency=duration.currency,
        invoice_payload=f"subscription:{user.pk}:{duration.pk}:{uuid4().hex}",
        status=PaymentTransaction.PENDING,
    )
    return payment


def _pending_manual_payment(*, telegram_user_id: int, duration_id: int) -> PaymentTransaction:
    # A burst of clicks can exceed the short publishing-transition retry window on
    # SQLite. Retry only this fully rolled-back transaction, for at most 1.1s of
    # backoff. PostgreSQL still relies on the existing per-user database row lock.
    for attempt in range(8):
        try:
            return _pending_manual_payment_once(telegram_user_id=telegram_user_id, duration_id=duration_id)
        except OperationalError as error:
            if not is_sqlite_contention(error) or attempt == 7:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.2))


def _pending_manual_payment_once(*, telegram_user_id: int, duration_id: int) -> PaymentTransaction:
    from publishing.services.runs import _lock_user
    with bounded_transaction():
        # First-write serialization also works on SQLite, where FOR UPDATE is a no-op.
        user = _lock_user(telegram_user_id)
        if user is None:
            raise User.DoesNotExist
        duration = PlanDuration.objects.select_related("plan").get(
            pk=duration_id, is_active=True, plan__is_active=True, plan__is_free=False,
            payment_method="manual", currency="USD",
        )
        if not is_valid_payment_offer(duration):
            raise ValueError("Payment offer is unavailable.")
        existing = PaymentTransaction.objects.select_related("plan", "duration").filter(
            user=user, plan_id=duration.plan_id, duration=duration,
            payment_method="manual", status=PaymentTransaction.PENDING,
        ).order_by("created_at", "pk").first()
        if existing is not None:
            return existing
        return PaymentTransaction.objects.create(
            user=user, plan=duration.plan, duration=duration, payment_method="manual",
            amount=duration.price, currency=duration.currency,
            invoice_payload=f"subscription:{user.pk}:{duration.pk}:{uuid4().hex}",
            status=PaymentTransaction.PENDING,
        )


@sync_to_async
def set_payment_invoice_id(payment_id: int, invoice_id: str) -> None:
    PaymentTransaction.objects.filter(pk=payment_id, status=PaymentTransaction.PENDING).update(invoice_id=invoice_id)


@sync_to_async
def fail_pending_payment(payment_id: int, error_message: str) -> None:
    PaymentTransaction.objects.filter(pk=payment_id, status=PaymentTransaction.PENDING).update(
        status=PaymentTransaction.FAILED, error_message=error_message,
    )


@sync_to_async
def activate_manual_payment(*, payment_id: int) -> Subscription:
    with transaction.atomic():
        payment = PaymentTransaction.objects.select_for_update().select_related("user", "duration").get(pk=payment_id)
        if payment.payment_method != "manual":
            raise ValueError("Only manual payments can be activated here.")
        if payment.status == PaymentTransaction.PAID:
            return _ensure_subscription(payment.user)
        if payment.status != PaymentTransaction.PENDING:
            raise ValueError("Payment is not pending.")
        payment.status = PaymentTransaction.PAID
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "paid_at"])
        return _activate_paid_subscription_locked(user=payment.user, duration=payment.duration, event_key=f"payment:{payment.pk}")


@sync_to_async
def validate_pending_payment(*, payload: str, telegram_user_id: int, amount: int, currency: str) -> bool:
    return PaymentTransaction.objects.filter(
        invoice_payload=payload,
        user__telegram_user_id=telegram_user_id,
        amount=amount,
        currency=currency,
        status=PaymentTransaction.PENDING,
    ).exists()


@sync_to_async
def finalize_stars_payment(*, payload: str, telegram_user_id: int, charge_id: str) -> tuple[PaymentTransaction, Subscription]:
    with transaction.atomic():
        payment = PaymentTransaction.objects.select_for_update().select_related("duration", "plan", "user").get(
            invoice_payload=payload,
            user__telegram_user_id=telegram_user_id,
        )
        if payment.status == PaymentTransaction.PAID:
            return payment, _ensure_subscription(payment.user)
        if payment.status != PaymentTransaction.PENDING:
            raise ValueError("Payment is not pending")
        payment.status = PaymentTransaction.PAID
        payment.provider_payment_id = charge_id
        payment.telegram_payment_charge_id = charge_id
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "provider_payment_id", "telegram_payment_charge_id", "paid_at"])
        subscription = _activate_paid_subscription_locked(user=payment.user, duration=payment.duration, event_key=f"payment:{payment.pk}")
        return payment, subscription


@transaction.atomic
def _activate_paid_subscription_locked(*, user: User, duration: PlanDuration, event_key=None) -> Subscription:
    from publishing.services.runs import _lock_user
    from notifications.events import emit
    from notifications.models import BotNotification
    from publishing.models import PublishingSettings
    user = _lock_user(user.telegram_user_id)
    subscription = _ensure_subscription(user)
    subscription = Subscription.objects.select_for_update(of=("self",)).select_related("plan_definition").get(pk=subscription.pk)
    if event_key and BotNotification.objects.filter(event_key=event_key).exists():
        return subscription
    now = timezone.now()
    active_end = subscription.end_date if subscription.end_date and subscription.end_date > now else None
    old_plan = subscription.plan_definition.name
    same_plan = subscription.plan_definition_id == duration.plan_id
    was_paid = not subscription.plan_definition.is_free and bool(active_end)
    credited_days = 0
    if active_end and subscription.plan_definition_id == duration.plan_id:
        start = subscription.start_date or now
        end = active_end + timedelta(days=duration.days)
    else:
        start = now
        if active_end and subscription.duration and subscription.plan_definition_id != duration.plan_id:
            credited_days = converted_remaining_days(
                remaining_days=(active_end - now).days,
                old_duration=subscription.duration,
                new_duration=duration,
            )
        end = now + timedelta(days=duration.days + credited_days)

    subscription.plan = duration.plan.code
    subscription.plan_definition = duration.plan
    subscription.duration = duration
    subscription.status = "active"
    subscription.start_date = start
    subscription.end_date = end
    subscription.expired_at = None
    subscription.limits_snapshot = _limits_for_plan(duration.plan)
    subscription.save(update_fields=[
        "plan", "plan_definition", "duration", "status", "start_date",
        "end_date", "expired_at", "limits_snapshot",
    ])
    user.current_plan = duration.plan.code
    user.save(update_fields=["current_plan"])
    kind = "subscription_renewed" if same_plan and was_paid else "subscription_changed" if was_paid else "subscription_activated"
    zone = PublishingSettings.objects.filter(user=user).values_list("timezone", flat=True).first() or "Asia/Gaza"
    emit(user.pk, kind, event_key or f"activation:{uuid4()}", payload={
        "plan": duration.plan.name, "old_plan": old_plan, "until": end.isoformat(), "timezone": zone})
    return subscription


@sync_to_async
def activate_paid_subscription(*, telegram_user_id: int, duration_id: int) -> Subscription:
    with transaction.atomic():
        user = User.objects.select_for_update().get(telegram_user_id=telegram_user_id)
        duration = PlanDuration.objects.select_related("plan").get(
            pk=duration_id,
            is_active=True,
            plan__is_active=True,
            plan__is_free=False,
        )
        return _activate_paid_subscription_locked(user=user, duration=duration)


@sync_to_async
def complete_demo_payment(*, telegram_user_id: int, duration_id: int, payment_method: str) -> tuple[PaymentTransaction, Subscription]:
    with transaction.atomic():
        user = User.objects.select_for_update().get(telegram_user_id=telegram_user_id)
        duration = PlanDuration.objects.select_related("plan").get(
            pk=duration_id,
            is_active=True,
            plan__is_active=True,
            plan__is_free=False,
        )
        payment = PaymentTransaction.objects.create(
            user=user,
            plan=duration.plan,
            duration=duration,
            payment_method=payment_method,
            amount=duration.price,
            currency=duration.currency,
            status=PaymentTransaction.PAID,
            provider_payment_id=f"demo-{timezone.now().timestamp()}",
            paid_at=timezone.now(),
        )
        subscription = _activate_paid_subscription_locked(user=user, duration=duration, event_key=f"payment:{payment.pk}")
        return payment, subscription
