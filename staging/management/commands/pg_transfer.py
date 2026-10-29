from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from staging.migration_tools import TransferError, transfer


class Command(BaseCommand):
    help = "Import or verify an offline SQLite snapshot against isolated PostgreSQL staging only."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm-empty-staging-target", action="store_true")

    def handle(self, *args, **options):
        if settings.SETTINGS_MODULE != "config.postgres_staging_settings":
            raise CommandError("This command is restricted to PostgreSQL staging settings")
        if options["apply"] and not options["confirm_empty_staging_target"]:
            raise CommandError("Explicit confirmation of an empty staging target is required")
        try:
            counts = transfer(apply=options["apply"])
        except TransferError as error:
            raise CommandError(str(error)) from None
        except Exception as error:
            # Database/fixture error messages may contain encrypted sessions or PII.
            raise CommandError(f"Transfer failed ({type(error).__name__}); no record values logged. Target transaction rolled back if active.") from None
        for label, count in counts.items():
            self.stdout.write(f"{label}: {count}")
        self.stdout.write("PASS: field values, PKs, links, constraints and sequences verified; no publishing started.")
