"""Offline migration rehearsal only. No bot, worker or production connections."""
from .staging_settings import *  # noqa: F403
from config.database import database_config

if (os.environ.get("POSTGRES_HOST") != "staging-postgres"
        or os.environ.get("POSTGRES_DB") != "onepost_staging"):
    raise RuntimeError("PostgreSQL staging accepts only its isolated host/database")

DEBUG = False
DATABASES = {
    "default": database_config(os.environ, BASE_DIR),
    "source": {
        "ENGINE": "django.db.backends.sqlite3",
        # Only a consistent backup may be mounted here. immutable forbids WAL use.
        "NAME": "file:/migration-input/source.sqlite3?mode=ro&immutable=1",
        "OPTIONS": {"uri": True},
    },
}
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise RuntimeError("PostgreSQL staging must explicitly opt in")
DATABASES["default"]["TEST"] = {"NAME": "test_onepost_staging"}
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "ERROR"}}
