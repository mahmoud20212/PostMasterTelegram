from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from publishing.services.runs import finish_run, recover_legacy_user


class Command(BaseCommand):
    help = "Invalidate one orphan run only after the operator has stopped old execution. Does not enqueue work."

    def add_arguments(self, parser):
        parser.add_argument("telegram_user_id", type=int)
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--run-id", type=UUID)
        target.add_argument("--legacy", action="store_true")
        parser.add_argument("--confirm-execution-stopped", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm_execution_stopped"]:
            raise CommandError("Stop old bot/worker execution first, then pass --confirm-execution-stopped. This flag is an operator attestation, not a liveness check.")
        user_id = options["telegram_user_id"]
        changed = (recover_legacy_user(user_id) if options["legacy"]
                   else finish_run(user_id, options["run_id"]))
        if not changed:
            raise CommandError("No matching active run/legacy state. No other run was invalidated.")
        self.stdout.write(self.style.SUCCESS("Publishing state invalidated. No task was enqueued and no claim was reset."))
