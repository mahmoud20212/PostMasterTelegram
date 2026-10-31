from asgiref.sync import sync_to_async

from publishing.models import PublishingSettings
from users.models import User
from django.utils import timezone
from publishing.models import PublishingRun
from publishing.services.contention import bounded_transaction, retry_transition
from publishing.services.work_rest import reset_work

DEFAULT_MESSAGE_INTERVAL_SECONDS = 15


@sync_to_async
def get_settings(*, telegram_user_id: int) -> PublishingSettings:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    settings, _ = PublishingSettings.objects.get_or_create(user=user)
    return _with_phase(settings)


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
@retry_transition
def toggle_sleep(*, telegram_user_id: int) -> PublishingSettings:
    from publishing.services.runs import _lock_user
    with bounded_transaction():
        user = _lock_user(telegram_user_id)
        settings, _ = PublishingSettings.objects.get_or_create(user=user)
        settings.sleep_enabled = not settings.sleep_enabled
        settings.save(update_fields=["sleep_enabled"])
        _reset_active_run(user, settings)
        return _with_phase(settings)


@sync_to_async
def update_sleep_period(*, telegram_user_id: int, start, end) -> PublishingSettings:
    settings = get_settings_sync(telegram_user_id)
    settings.sleep_start = start
    settings.sleep_end = end
    settings.sleep_enabled = True
    settings.save(update_fields=["sleep_start", "sleep_end", "sleep_enabled"])
    return settings


@sync_to_async
@retry_transition
def update_work_hours(*, telegram_user_id: int, hours: int) -> PublishingSettings:
    if not 1 <= hours <= 24:
        raise ValueError("ساعات العمل يجب أن تكون بين 1 و24 ساعة.")
    from publishing.services.runs import _lock_user
    with bounded_transaction():
        user = _lock_user(telegram_user_id)
        settings, _ = PublishingSettings.objects.get_or_create(user=user)
        settings.work_hours = hours
        settings.save(update_fields=["work_hours"])
        _reset_active_run(user, settings)
        return _with_phase(settings)


def _with_phase(settings):
    run = PublishingRun.objects.filter(user_id=settings.user_id, active=True, user__is_publishing=True).first()
    settings.phase_name = run.work_rest_phase if run else "stopped"
    settings.next_phase_at = run.phase_ends_at if run else None
    return settings


def _reset_active_run(user, settings):
    run = PublishingRun.objects.filter(user=user, active=True, scheduler_version=2).first()
    if run and user.is_publishing:
        reset_work(run, settings, timezone.now())
        run.save()


@sync_to_async
@retry_transition
def update_work_sleep_hours(*, telegram_user_id: int, work_hours: int, sleep_hours: int):
    if not 1 <= work_hours <= 24 or not 1 <= sleep_hours <= 24:
        raise ValueError("ساعات العمل والراحة يجب أن تكون بين 1 و24 ساعة.")
    from publishing.services.runs import _lock_user
    with bounded_transaction():
        user = _lock_user(telegram_user_id)
        settings, _ = PublishingSettings.objects.get_or_create(user=user)
        settings.work_hours = work_hours
        settings.sleep_hours = sleep_hours
        settings.sleep_enabled = True
        settings.save(update_fields=["work_hours", "sleep_hours", "sleep_enabled"])
        _reset_active_run(user, settings)
        return _with_phase(settings)
