from asgiref.sync import sync_to_async
from django.utils import timezone

from messages.models import SavedMessage
from publishing.models import PublishingRun, PublishingSettings
from subscriptions.models import PaymentTransaction, Subscription
from telegram_accounts.models import TelegramAccount
from telegram_groups.models import TelegramGroup
from users.models import User


@sync_to_async
def get_statistics(*, telegram_user_id: int) -> dict:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    settings, _ = PublishingSettings.objects.get_or_create(user=user)
    subscription = Subscription.objects.select_related("plan_definition").filter(user=user).first()
    plan = subscription.plan_definition if subscription else None
    messages = SavedMessage.objects.filter(user=user)
    groups = TelegramGroup.objects.filter(user=user)
    accounts = TelegramAccount.objects.filter(user=user)
    now = timezone.now()
    daily_limit = plan.daily_send_limit if plan else None
    daily_count = user.daily_send_count
    pending_payments = PaymentTransaction.objects.filter(user=user, status=PaymentTransaction.PENDING)
    last_payment = PaymentTransaction.objects.filter(user=user).order_by("-created_at").first()
    publishing_account = accounts.filter(is_publishing_account=True).first()
    run = PublishingRun.objects.filter(user=user, active=True).first()
    publishing_status = "في فترة الراحة" if user.is_publishing and run and run.work_rest_phase == "rest" else "يعمل" if user.is_publishing else "متوقف"
    return {
        "publishing_status": publishing_status,
        "messages": messages.count(),
        "active_messages": messages.filter(is_active=True).count(),
        "groups": groups.count(),
        "selected_groups": groups.filter(is_selected=True).count(),
        "active_groups": groups.filter(is_selected=True, status__in=["pending", "active"]).count(),
        "stopped_groups": groups.filter(is_selected=False).count(),
        "error_groups": groups.exclude(last_error="").count(),
        "accounts": accounts.count(),
        "connected_accounts": accounts.filter(status="connected").count(),
        "paused_accounts": accounts.filter(status__in=["paused_flood_wait", "restricted", "disabled"]).count(),
        "publishing_account": publishing_account.phone_number if publishing_account else "غير محدد",
        "cycle_minutes": settings.cycle_duration_minutes,
        "work_hours": settings.work_hours,
        "sleep_hours": settings.sleep_hours,
        "cycle_rest_seconds": plan.cycle_rest_seconds if plan else 0,
        "next_cycle_at": settings.next_cycle_at,
        "daily_count": daily_count,
        "daily_limit": daily_limit,
        "daily_remaining": None if daily_limit is None else max(0, daily_limit - daily_count),
        "sleep_enabled": settings.sleep_enabled,
        "plan_name": plan.name if plan else (subscription.plan if subscription else "مجاني"),
        "subscription_status": subscription.status if subscription else "active",
        "subscription_end": subscription.end_date if subscription else None,
        "pending_payments": pending_payments.count(),
        "last_payment": last_payment,
        "now": now,
    }
