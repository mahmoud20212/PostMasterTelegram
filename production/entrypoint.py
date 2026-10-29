"""Load file secrets without putting their values in command lines or Compose."""
import os
from pathlib import Path
import sys


def main():
    if os.environ.get("DATABASE_BACKEND") != "postgresql" or os.environ.get("POSTGRES_HOST") != "fresh-postgres":
        raise SystemExit("Fresh production requires its dedicated PostgreSQL service")
    if os.environ.get("REDIS_URL") != "redis://fresh-redis:6379/0":
        raise SystemExit("Fresh production requires its dedicated Redis service")
    for name in ("POSTGRES_PASSWORD", "DJANGO_SECRET_KEY", "TELEGRAM_BOT_TOKEN",
                 "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_ENCRYPTION_KEY"):
        os.environ[name] = Path("/run/secrets", name.lower()).read_text().strip()
        if not os.environ[name]:
            raise SystemExit("Required secret file is empty")
    command = sys.argv[1:]
    if command and (command[0] == "celery" or command == ["python", "-m", "bot.main"]):
        if os.environ.get("ONEPOST_POLLING_HANDOFF") != "confirmed-exclusive-token":
            raise SystemExit("Operator must confirm exclusive token ownership before live services")
    if not command:
        raise SystemExit("An explicit command is required")
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
