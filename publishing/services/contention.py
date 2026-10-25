"""Retry only complete, rollback-safe DB transitions, never broker publication."""
import time
from functools import wraps

from django.db import OperationalError, connection


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
