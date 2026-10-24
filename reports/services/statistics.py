from asgiref.sync import sync_to_async

from messages.models import SavedMessage
from publishing.models import PublishingSettings
from telegram_accounts.models import TelegramAccount
from telegram_groups.models import TelegramGroup
from users.models import User


@sync_to_async
def get_statistics(*, telegram_user_id: int) -> dict:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    settings, _ = PublishingSettings.objects.get_or_create(user=user)
    messages = SavedMessage.objects.filter(user=user)
    groups = TelegramGroup.objects.filter(user=user)
    accounts = TelegramAccount.objects.filter(user=user)
    return {
        "publishing_status": "يعمل" if user.is_publishing else "متوقف",
        "messages": messages.count(),
        "active_messages": messages.filter(is_active=True).count(),
        "groups": groups.count(),
        "selected_groups": groups.filter(is_selected=True).count(),
        "accounts": accounts.count(),
        "connected_accounts": accounts.filter(status="connected").count(),
        "cycle_minutes": settings.cycle_duration_minutes,
        "work_hours": settings.work_hours,
        "sleep_hours": settings.sleep_hours,
        "sleep_enabled": settings.sleep_enabled,
    }
