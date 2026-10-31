import os

from django.core.exceptions import ImproperlyConfigured
import dj_database_url

from production.runtime import https_origin, public_ip

from .base import *

DEBUG = False

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")

if not SECRET_KEY:
  raise ImproperlyConfigured(
    "DJANGO_SECRET_KEY must be configured"
  )

try:
  PUBLIC_IP = public_ip(os.getenv("PUBLIC_IP", ""))
except ValueError as error:
  raise ImproperlyConfigured(str(error)) from None
ALLOWED_HOSTS = [f"[{PUBLIC_IP}]" if ":" in PUBLIC_IP else PUBLIC_IP]
CSRF_TRUSTED_ORIGINS = [https_origin(PUBLIC_IP)]

try:
  DATABASES = {
    "default": dj_database_url.parse(
      os.environ["DATABASE_URL"], conn_max_age=60, conn_health_checks=True,
    )
  }
except (KeyError, ValueError):
  raise ImproperlyConfigured("DATABASE_URL must be configured for PostgreSQL") from None
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
  raise ImproperlyConfigured("Production requires PostgreSQL")

STORAGES = {
  "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
  "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

SECURE_SSL_REDIRECT = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

SECURE_HSTS_SECONDS = int(
  os.getenv("DJANGO_HSTS_SECONDS", "0")
)

SECURE_HSTS_INCLUDE_SUBDOMAINS = (
  SECURE_HSTS_SECONDS > 0
)

SECURE_HSTS_PRELOAD = False

SECURE_PROXY_SSL_HEADER = (
  "HTTP_X_FORWARDED_PROTO",
  "https",
)

# Multiple containers must not rotate the same application log file.
LOGGING = {
  "version": 1,
  "disable_existing_loggers": False,
  "handlers": {"console": {"class": "logging.StreamHandler"}},
  "root": {"handlers": ["console"], "level": "INFO"},
}
