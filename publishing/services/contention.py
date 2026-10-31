"""Retry only complete, rollback-safe DB transitions, never broker publication."""
import time
from contextlib import contextmanager
from functools import wraps

from django.conf import settings
from django.db import OperationalError, connection
from django.db import transaction


def is_sqlite_contention(error):
    return connection.vendor == "sqlite" and any(
        text in str(error).lower() for text in ("database is locked", "database table is locked", "database is busy")
    )


def retry_transition(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        for attempt in range(3):
            try:
                return function(*args, **kwargs)
            except OperationalError as error:
                if not is_sqlite_contention(error) or attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
    return wrapped


@contextmanager
def bounded_transaction():
    """Keep a contended row from indefinitely occupying a publishing step."""
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('lock_timeout', %s, true)",
                               [f"{getattr(settings, 'PUBLISHING_DB_LOCK_TIMEOUT_MS', 1000)}ms"])
                cursor.execute("SELECT set_config('statement_timeout', %s, true)",
                               [f"{getattr(settings, 'PUBLISHING_DB_STATEMENT_TIMEOUT_MS', 5000)}ms"])
        yield
