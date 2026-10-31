"""Isolated tests: never use DATABASE_URL or an existing application database."""
import os
import uuid

from .base import *

SECRET_KEY = "onepost-isolated-tests"
DEBUG = False
LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"console": {"class": "logging.StreamHandler"}}, "root": {"handlers": ["console"], "level": "WARNING"}}
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
if os.getenv("ONEPOST_TEST_DATABASE_URL"):
    import dj_database_url
    DATABASES["default"] = dj_database_url.parse(os.environ["ONEPOST_TEST_DATABASE_URL"], conn_max_age=0)
    DATABASES["default"]["TEST"] = {"NAME": "onepost_test_" + uuid.uuid4().hex[:16]}
CELERY_TASK_ALWAYS_EAGER = False
