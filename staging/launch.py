"""Fail-closed entrypoint for isolated staging containers."""
import os
import sys


def install_guards():
    if os.environ.get("ONEPOST_STAGING") != "1":
        raise RuntimeError("This entrypoint requires ONEPOST_STAGING=1")
    if os.environ.get("REDIS_URL") != "redis://staging-redis:6379/0":
        raise RuntimeError("Staging accepts only its isolated Redis service")
    forbidden = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_ENCRYPTION_KEY")
    if any(os.environ.get(key) for key in forbidden) or any(key.startswith("AWS_") for key in os.environ):
        raise RuntimeError("Real Telegram/AWS credentials must not be provided to staging")
    # Production settings call load_dotenv. Disable it before importing Django.
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    import telethon

    class DisabledTelegramClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Outbound Telegram is disabled in local staging; use a test mock")

    telethon.TelegramClient = DisabledTelegramClient


def main():
    install_guards()
    mode, *arguments = sys.argv[1:]
    if mode == "manage":
        from django.core.management import execute_from_command_line
        execute_from_command_line(["manage.py", *arguments])
    elif mode == "worker":
        from config.celery import app
        app.worker_main(["worker", *arguments])
    elif mode == "web" and not arguments:
        from gunicorn.app.wsgiapp import run
        sys.argv = ["gunicorn", "config.wsgi:application", "--bind=0.0.0.0:8000", "--workers=1", "--threads=1", "--timeout=30", "--access-logfile=-"]
        run()
    else:
        raise RuntimeError("Only manage, worker, and web are allowed; no Telegram bot")


if __name__ == "__main__":
    main()
