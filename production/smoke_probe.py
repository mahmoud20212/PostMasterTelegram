"""Checks against a fresh, isolated smoke database. Never used by production startup."""
import argparse
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

from production.smoke_tasks import probe, require_smoke
from production.runtime import https_origin


def main():
    require_smoke()
    import django
    django.setup()
    from cryptography.fernet import Fernet
    from django.contrib.auth import get_user_model
    from django.test import Client
    from django.contrib.staticfiles.storage import staticfiles_storage
    from redis import Redis
    from telegram_accounts.models import TelegramAccount
    from users.models import User

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify"])
    args = parser.parse_args()
    assert not (Path("/app") / ".env").exists()
    assert not list(Path("/app").rglob("*.sqlite3"))
    cipher = Fernet(os.environ["TELEGRAM_SESSION_ENCRYPTION_KEY"].encode())
    broker = Redis.from_url(os.environ["REDIS_URL"], socket_timeout=3)
    if args.action == "prepare":
        admin = get_user_model().objects.create_superuser("smoke-admin", password="isolated-smoke-password")
        assert admin.is_superuser
        user = User.objects.create(telegram_user_id=199000000001)
        TelegramAccount.objects.create(user=user, phone_number="+199000000001", encrypted_session=cipher.encrypt(b"smoke-session").decode())
        broker.set("onepost:smoke:persistence", "ok")
    assert cipher.decrypt(TelegramAccount.objects.get(phone_number="+199000000001").encrypted_session.encode()) == b"smoke-session"
    assert broker.get("onepost:smoke:persistence") == b"ok"
    assert probe.delay().get(timeout=30) == "ok"
    address = os.environ["PUBLIC_IP"]
    host = f"[{address}]" if ":" in address else address
    client = Client(enforce_csrf_checks=True, HTTP_HOST=host)
    page = client.get("/admin/login/", secure=True)
    assert page.status_code == 200
    response = client.post("/admin/login/", {"username": "smoke-admin", "password": "isolated-smoke-password",
        "csrfmiddlewaretoken": client.cookies["csrftoken"].value, "next": "/admin/"}, secure=True,
        HTTP_ORIGIN=https_origin(address))
    assert response.status_code == 302
    assert client.cookies["sessionid"]["secure"]
    assert client.get(staticfiles_storage.url("admin/css/base.css"), secure=True).status_code == 200
    # Exercise the actual bot entry point, replacing both polling and the Bot HTTP client.
    from bot.main import main as bot_main
    with patch("bot.main.Bot") as bot, patch("bot.main.Dispatcher") as dispatcher:
        bot.return_value.__aenter__ = AsyncMock(return_value=bot.return_value)
        bot.return_value.__aexit__ = AsyncMock()
        dispatcher.return_value.start_polling = AsyncMock()
        asyncio.run(bot_main())
        dispatcher.return_value.start_polling.assert_awaited_once()
    print("Database role, persistence, encrypted session, Celery, admin, static files and mocked bot checks passed.")


if __name__ == "__main__":
    main()
