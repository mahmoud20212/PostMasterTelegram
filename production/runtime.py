"""Read Compose secret files before importing Django, Celery or the bot."""
import base64
import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import quote

SECRET_NAMES = (
    "DJANGO_SECRET_KEY", "DB_PASSWORD", "REDIS_PASSWORD", "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_ENCRYPTION_KEY",
)


def public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("PUBLIC_IP must be an IP address") from None
    if not address.is_global:
        raise ValueError("PUBLIC_IP must be publicly routable")
    return str(address)


def https_origin(address):
    return f"https://[{address}]" if ":" in address else f"https://{address}"


def load_environment(environ=None):
    env = os.environ if environ is None else environ
    for name in SECRET_NAMES:
        path = env.get(f"{name}_FILE")
        if path:
            if env.get(name):
                raise ValueError(f"Configure only {name}_FILE, not both secret sources")
            try:
                value = Path(path).read_text(encoding="utf-8").strip()
            except OSError:
                raise ValueError(f"Cannot read {name}_FILE") from None
            env[name] = value
        if not env.get(name) or "\n" in env[name] or "\r" in env[name]:
            raise ValueError(f"Missing or invalid secret: {name}")
    if len(env["DJANGO_SECRET_KEY"]) < 50:
        raise ValueError("DJANGO_SECRET_KEY must contain at least 50 characters")
    try:
        key = base64.b64decode(env["TELEGRAM_SESSION_ENCRYPTION_KEY"], altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid TELEGRAM_SESSION_ENCRYPTION_KEY") from None
    if len(key) != 32:
        raise ValueError("Invalid TELEGRAM_SESSION_ENCRYPTION_KEY")
    if not env["TELEGRAM_API_ID"].isdigit() or int(env["TELEGRAM_API_ID"]) <= 0:
        raise ValueError("Invalid TELEGRAM_API_ID")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", env["TELEGRAM_API_HASH"]):
        raise ValueError("Invalid TELEGRAM_API_HASH")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", env["TELEGRAM_BOT_TOKEN"]):
        raise ValueError("Invalid TELEGRAM_BOT_TOKEN")
    for name in ("DB_PASSWORD", "REDIS_PASSWORD"):
        # The infrastructure startup scripts accept these generated hex secrets.
        if len(env[name]) < 32 or any(char not in "0123456789abcdef" for char in env[name]):
            raise ValueError(f"{name} must be a generated lowercase hex secret")
    address = public_ip(env.get("PUBLIC_IP", ""))
    env["PUBLIC_IP"] = address
    env["DATABASE_URL"] = f"postgresql://onepost:{quote(env['DB_PASSWORD'], safe='')}@db:5432/onepost"
    env["REDIS_URL"] = f"redis://:{quote(env['REDIS_PASSWORD'], safe='')}@redis:6379/0"
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.production"
    env["PUBLISHING_USE_BEAT"] = "false"
    return env
