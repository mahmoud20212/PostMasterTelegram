"""Generate a NEW deployment configuration; never reads the local .env file."""
import argparse
import base64
from getpass import getpass
import os
from pathlib import Path
import re
import secrets

from production.runtime import public_ip


def create_configuration(root, address, email, bot_token, api_id, api_hash):
    root = Path(root).resolve()
    address = public_ip(address)
    if not re.fullmatch(r"[^\s@=]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email) or email.endswith("@example.com"):
        raise ValueError("Provide a real ACME email address")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", bot_token):
        raise ValueError("Invalid bot token format")
    if not api_id.isdigit() or int(api_id) <= 0 or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise ValueError("Provide a valid Telegram API ID and API hash")
    env_path = root / ".env.production"
    directory = root / ".production" / "secrets"
    if env_path.exists() or directory.exists():
        raise FileExistsError("Production configuration/secrets already exist; nothing was overwritten")
    values = {
        "django_secret_key": secrets.token_urlsafe(64),
        "db_password": secrets.token_hex(32),
        "db_admin_password": secrets.token_hex(32),
        "redis_password": secrets.token_hex(32),
        "session_key": base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
        "telegram_bot_token": bot_token,
        "telegram_api_id": api_id,
        "telegram_api_hash": api_hash,
    }
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.parent.chmod(0o700)
    directory.mkdir(mode=0o700)
    for name, value in values.items():
        path = directory / name
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(value + "\n")
        # Compose file-backed secrets preserve host modes. A private 0700 parent
        # protects them on the host; individual mounts must be readable by the
        # non-root application and by PostgreSQL/Redis's distinct service UIDs.
        path.chmod(0o444)
    with env_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(f"PUBLIC_IP={address}\nACME_EMAIL={email}\nAPP_IMAGE=onepost:production\n"
            f"ONEPOST_HTTP_BIND={'[::]' if ':' in address else '0.0.0.0'}\n"
            "CELERY_CONCURRENCY=2\nONEPOST_SECRETS_DIR=./.production/secrets\n"
            "ONEPOST_REQUIRED_GROUP_CHAT_ID=@onepost_group\n"
            "ONEPOST_REQUIRED_GROUP_URL=https://t.me/onepost_group\n")
    env_path.chmod(0o600)
    return env_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True, help="The VPS's stable public IP")
    parser.add_argument("--email", required=True, help="ACME account notification email")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    # Check before asking for credentials; never print or copy credentials from .env.
    if (root / ".env.production").exists() or (root / ".production/secrets").exists():
        parser.error("Production configuration already exists; refusing to overwrite it")
    try:
        path = create_configuration(root, args.ip, args.email,
            getpass("Current Telegram bot token: ").strip(), input("Telegram API ID: ").strip(),
            getpass("Telegram API hash: ").strip())
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(f"Created {path.name} and private secret files. Back up the session key securely.")
    print("No containers were started and no existing database was changed.")


if __name__ == "__main__":
    main()
