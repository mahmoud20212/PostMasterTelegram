"""Worker used ONLY by the isolated Docker smoke harness. No Telegram sends."""
import os
import re

from config.celery import app


def require_smoke():
    if not re.fullmatch(r"onepost-smoke-[0-9a-f]{12}", os.environ.get("ONEPOST_SMOKE_PROJECT", "")):
        raise RuntimeError("This command is restricted to the isolated smoke project")


@app.task(name="production.smoke_probe")
def probe():
    require_smoke()
    from django.db import connection
    from publishing.services.execution_lock import execution_lock
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")
        if cursor.fetchone() != (False, False, False):
            raise RuntimeError("The application database role is privileged")
    with execution_lock("smoke", 1) as acquired:
        if not acquired:
            raise RuntimeError("Cannot acquire PostgreSQL execution lock")
        with execution_lock("smoke", 1) as duplicate:
            if duplicate:
                raise RuntimeError("Duplicate execution lock was acquired")
    return "ok"
