import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from publishing.services.scheduler import dispatch_due
from notifications.delivery import dispatch_notifications


class Command(BaseCommand):
    help = "Dispatch due publishing steps and recover expired attempts; no Telegram calls."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=2.0)

    def handle(self, *args, **options):
        import logging
        logger = logging.getLogger(__name__)
        try:
            while True:
                try:
                    close_old_connections()
                    count = dispatch_due()
                    if options["once"]:
                        self.stdout.write(f"Dispatched {count} publishing steps.")
                except Exception as error:
                    if options["once"]:
                        raise
                    logger.error("Publishing scheduler tick failed (%s)", type(error).__name__)
                try:
                    dispatch_notifications()
                except Exception as error:
                    if options["once"]:
                        raise
                    logger.error("Notification scheduler tick failed (%s)", type(error).__name__)
                if options["once"]:
                    return
                time.sleep(max(0.1, options["interval"]))
        except KeyboardInterrupt:
            return
