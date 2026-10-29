"""Temporary SQLite only, for generating a synthetic rehearsal input."""
from .test_settings import *  # noqa: F403
INSTALLED_APPS = [*INSTALLED_APPS, "staging"]
