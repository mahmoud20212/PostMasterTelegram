from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from publishing.models import PublishingRun, PublishingSettings
from publishing.services.runs import _lock_user


class Command(BaseCommand):
    help = "Adopt active legacy runs only AFTER all old publishing workers have stopped."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-execution-stopped", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm_execution_stopped"]:
            raise CommandError("Stop all old bot/worker execution first, then pass --confirm-execution-stopped.")
        count = 0
        for run_id, telegram_user_id in PublishingRun.objects.filter(active=True, scheduler_version=1).values_list("pk", "user__telegram_user_id"):
            with transaction.atomic():
                user = _lock_user(telegram_user_id)
                publishing_settings = PublishingSettings.objects.filter(user=user).first()
                if not user.is_publishing or publishing_settings is None:
                    continue
                now = timezone.now()
                due = max(now, publishing_settings.next_cycle_at or now)
                count += PublishingRun.objects.filter(pk=run_id, active=True, scheduler_version=1).update(scheduler_version=2, next_due_at=due)
        self.stdout.write(f"Adopted {count} runs. No task was published and no session was changed.")
