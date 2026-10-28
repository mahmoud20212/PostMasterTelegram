"""Docker-only settings; never import production data or credentials."""
import os

if os.environ.get("ONEPOST_STAGING") != "1":
    raise RuntimeError("Staging settings require the guarded staging entrypoint")
from staging.launch import install_guards
install_guards()

os.environ["DJANGO_DEBUG"] = "True"
os.environ["DJANGO_SECRET_KEY"] = "local-staging-only-not-a-production-secret"
os.environ["DJANGO_ALLOWED_HOSTS"] = "localhost,127.0.0.1,django"

from .settings import *  # noqa: E402,F403

INSTALLED_APPS = [*INSTALLED_APPS, "staging"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "/staging-data/db.sqlite3"}}
CELERY_BROKER_URL = "redis://staging-redis:6379/0"
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_DEFAULT_QUEUE = "onepost-staging"
CELERY_IMPORTS = ("staging.probes",)
CELERY_BEAT_SCHEDULE = {}
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "INFO"}}
