"""Isolated test database; never opens the application's db.sqlite3."""
import os
import tempfile

os.environ["DJANGO_DEBUG"] = "True"
os.environ["DJANGO_SECRET_KEY"] = "onepost-tests-only"

from .settings import *  # noqa: F403,E402

_test_directory = tempfile.TemporaryDirectory(prefix="onepost-tests-")
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.path.join(_test_directory.name, "unused.sqlite3"),
    "TEST": {"NAME": os.path.join(_test_directory.name, "test.sqlite3")},
    "OPTIONS": {"timeout": 10},
}}
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"null": {"class": "logging.NullHandler"}},
           "root": {"handlers": ["null"]}}
