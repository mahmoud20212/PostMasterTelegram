"""Probe a real Redis -> worker round trip without publishing."""
from celery import shared_task


@shared_task(name="staging.safety_probe")
def safety_probe():
    from django.conf import settings
    import telethon
    try:
        telethon.TelegramClient(None, 0, "")
    except RuntimeError as error:
        if "disabled in local staging" not in str(error):
            raise
    else:
        raise RuntimeError("Telegram guard missing")
    return {"telegram_disabled": True, "database": str(settings.DATABASES["default"]["NAME"]),
            "queue": settings.CELERY_TASK_DEFAULT_QUEUE}
