import json

from django.core.management.base import BaseCommand, CommandError
from staging.probes import safety_probe


class Command(BaseCommand):
    help = "Check staging Redis/worker round trip and Telegram guard, without sending messages."

    def handle(self, *args, **options):
        from django.conf import settings
        if settings.SETTINGS_MODULE != "config.staging_settings":
            raise CommandError("Only staging settings are permitted")
        result = safety_probe.apply_async(queue="onepost-staging").get(timeout=15)
        expected = {"telegram_disabled": True, "database": "/staging-data/db.sqlite3", "queue": "onepost-staging"}
        if result != expected:
            raise CommandError("Staging safety probe mismatch")
        self.stdout.write(json.dumps(result))
