import os

from .base import *

DEBUG = True

SECRET_KEY = os.getenv(
  "DJANGO_SECRET_KEY",
  "local-only-insecure-key",
)

ALLOWED_HOSTS = [
  "127.0.0.1",
  "localhost",
]

# DATABASES = {
#   "default": {
#     "ENGINE": "django.db.backends.sqlite3",
#     "NAME": BASE_DIR / "db.sqlite3",
#   }
# }

import dj_database_url

DATABASES = {
  "default": dj_database_url.config(
    default=os.getenv("DATABASE_URL"),
    conn_max_age=60,
    conn_health_checks=True,
  )
}

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

SECURE_HSTS_SECONDS = 0