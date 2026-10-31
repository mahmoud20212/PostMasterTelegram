import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")

ONEPOST_REQUIRED_GROUP_CHAT_ID = os.getenv("ONEPOST_REQUIRED_GROUP_CHAT_ID", "@onepost_group")
ONEPOST_REQUIRED_GROUP_URL = os.getenv("ONEPOST_REQUIRED_GROUP_URL", "https://t.me/onepost_group")

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

INSTALLED_APPS = [
  "django.contrib.admin",
  "django.contrib.auth",
  "django.contrib.contenttypes",
  "django.contrib.sessions",
  "django.contrib.messages",
  "django.contrib.staticfiles",

  "users",
  "messages.apps.MessagesConfig",
  "telegram_accounts",
  "telegram_groups",
  "publishing",
  "subscriptions",
  "reports",
  "notifications",
]

MIDDLEWARE = [
  "django.middleware.security.SecurityMiddleware",
  "whitenoise.middleware.WhiteNoiseMiddleware",
  "django.contrib.sessions.middleware.SessionMiddleware",
  "django.middleware.common.CommonMiddleware",
  "django.middleware.csrf.CsrfViewMiddleware",
  "django.contrib.auth.middleware.AuthenticationMiddleware",
  "django.contrib.messages.middleware.MessageMiddleware",
  "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
  {
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
      "context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
      ],
    },
  },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
      "standard": {
        "format": "{asctime} [{levelname}] {name}: {message}",
        "style": "{",
      },
    },
    "handlers": {
      "console": {
        "class": "logging.StreamHandler",
        "formatter": "standard",
      },
      "app_file": {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "filename": str(LOG_DIR / "app.log"),
        "when": "midnight",
        "backupCount": 14,
        "encoding": "utf-8",
        "formatter": "standard",
      },
    },
    "root": {
      "handlers": ["console", "app_file"],
      "level": "INFO",
    },
}

CELERY_BROKER_URL = os.getenv(
  "REDIS_URL",
  "redis://127.0.0.1:6379/0",
)

CELERY_RESULT_BACKEND = CELERY_BROKER_URL

CELERY_BROKER_CONNECTION_TIMEOUT = 3

CELERY_BROKER_TRANSPORT_OPTIONS = {
  "socket_connect_timeout": 3,
  "socket_timeout": 3,
  "retry_on_timeout": False,
  "max_retries": 1,
}

CELERY_TASK_PUBLISH_RETRY = True

CELERY_TASK_PUBLISH_RETRY_POLICY = {
  "max_retries": 1,
  "interval_start": 0.2,
  "interval_step": 0,
  "interval_max": 0.2,
}

CELERY_BEAT_SCHEDULE = {
  "expire-subscriptions-hourly": {
    "task": "subscriptions.expire_subscriptions",
    "schedule": 3600.0,
  },
}

# A separate scheduler command is the default, so scheduling cannot be starved
# by busy send workers. Beat may call the same bounded dispatcher if enabled.
PUBLISHING_STEP_TIMEOUT = max(1, int(os.getenv("PUBLISHING_STEP_TIMEOUT", "45")))
PUBLISHING_DISCONNECT_TIMEOUT = 5
PUBLISHING_LEASE_SECONDS = PUBLISHING_STEP_TIMEOUT + PUBLISHING_DISCONNECT_TIMEOUT + 30
PUBLISHING_DISPATCH_LEASE_SECONDS = 30
PUBLISHING_DISPATCH_BATCH = 8
PUBLISHING_MAX_PENDING = max(1, int(os.getenv("PUBLISHING_MAX_PENDING", "8")))
CELERY_TASK_ROUTES = {"publishing.publish_step": {"queue": "publishing"}}
CELERY_TASK_ROUTES["notifications.deliver"] = {"queue": "notifications"}
NOTIFICATION_SEND_TIMEOUT = 10
NOTIFICATION_MAX_PENDING = 4
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ANNOTATIONS = {
  "publishing.publish_step": {
    "soft_time_limit": PUBLISHING_STEP_TIMEOUT + PUBLISHING_DISCONNECT_TIMEOUT + 5,
    "time_limit": PUBLISHING_STEP_TIMEOUT + PUBLISHING_DISCONNECT_TIMEOUT + 10,
  },
}
if os.getenv("PUBLISHING_USE_BEAT", "false").lower() in {"true", "1", "yes"}:
  CELERY_BEAT_SCHEDULE["dispatch-publishing"] = {
    "task": "publishing.dispatch_due", "schedule": 2.0, "options": {"expires": 5},
  }
