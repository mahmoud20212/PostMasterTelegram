"""Only used by the separate fresh PostgreSQL deployment."""
from .settings import *  # noqa: F403

if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":  # noqa: F405
    raise RuntimeError("Fresh production requires PostgreSQL")
STATIC_ROOT = "/tmp/staticfiles"
# Container log driver bounds disk usage; avoid competing rotating file writers.
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
