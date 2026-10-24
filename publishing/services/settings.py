from asgiref.sync import sync_to_async

from publishing.models import PublishingSettings
from users.models import User

DEFAULT_MESSAGE_INTERVAL_SECONDS = 15


@sync_to_async
def get_settings(*, telegram_user_id: int) -> PublishingSettings:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    settings, _ = PublishingSettings.objects.get_or_create(user=user)
    return settings


@sync_to_async
def update_interval(*, telegram_user_id: int, interval_seconds: int) -> PublishingSettings:
    settings = get_settings_sync(telegram_user_id)
    settings.interval_seconds = interval_seconds
    settings.save(update_fields=["interval_seconds"])
    return settings


@sync_to_async
def update_cycle_duration(*, telegram_user_id: int, minutes: int) -> PublishingSettings:
    settings = get_settings_sync(telegram_user_id)
    settings.cycle_duration_minutes = minutes
    settings.save(update_fields=["cycle_duration_minutes"])
    return settings


def get_settings_sync(telegram_user_id: int) -> PublishingSettings:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    settings, _ = PublishingSettings.objects.get_or_create(user=user)
    return settings


@sync_to_async
def toggle_sleep(*, telegram_user_id: int) -> PublishingSettings:
    settings = get_settings_sync(telegram_user_id)
    settings.sleep_enabled = not settings.sleep_enabled
    settings.save(update_fields=["sleep_enabled"])
    return settings


@sync_to_async
def update_sleep_period(*, telegram_user_id: int, start, end) -> PublishingSettings:
    settings = get_settings_sync(telegram_user_id)
    settings.sleep_start = start
    settings.sleep_end = end
    settings.sleep_enabled = True
    settings.save(update_fields=["sleep_start", "sleep_end", "sleep_enabled"])
    return settings


@sync_to_async
def update_work_hours(*, telegram_user_id: int, hours: int) -> PublishingSettings:
    settings = get_settings_sync(telegram_user_id)
    settings.work_hours = hours
    settings.save(update_fields=["work_hours"])
    return settings
