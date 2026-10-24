from asgiref.sync import sync_to_async

from messages.models import SavedMessage
from publishing.models import PublishingSettings
from telegram_accounts.models import TelegramAccount
from telegram_groups.models import TelegramGroup
from users.models import User


@sync_to_async
def get_dashboard_counts(*, telegram_user_id: int) -> dict[str, int]:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    settings, _ = PublishingSettings.objects.get_or_create(user=user)
    return {
        "messages": SavedMessage.objects.filter(user=user).count(),
        "groups": TelegramGroup.objects.filter(user=user).count(),
        "accounts": TelegramAccount.objects.filter(user=user).count(),
        "cycle_minutes": settings.cycle_duration_minutes,
        "sleep_enabled": settings.sleep_enabled,
    }
