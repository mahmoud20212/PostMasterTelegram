from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DurableMigrationTests(TransactionTestCase):
    def test_additive_migration_preserves_legacy_data_and_does_not_adopt_running_workers(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([("publishing", "0007_publishingrun")])
            apps = executor.loader.project_state([("publishing", "0007_publishingrun")]).apps
            user = apps.get_model("users", "User").objects.create(telegram_user_id=7654321, is_publishing=True, daily_send_count=17)
            account = apps.get_model("telegram_accounts", "TelegramAccount").objects.create(user_id=user.pk,
                phone_number="+970123456789", status="connected", encrypted_session="preserve-byte-for-byte")
            old_settings = apps.get_model("publishing", "PublishingSettings").objects.create(user_id=user.pk,
                is_running=True, current_message_index=3, current_group_index=7, work_hours=12)
            old_run = apps.get_model("publishing", "PublishingRun").objects.create(user_id=user.pk, account_id=account.pk, task_claimed=True)
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from publishing.models import PublishingRun, PublishingSettings
            from telegram_accounts.models import TelegramAccount
            from users.models import User
            self.assertEqual(TelegramAccount.objects.get(pk=account.pk).encrypted_session, "preserve-byte-for-byte")
            settings = PublishingSettings.objects.get(pk=old_settings.pk)
            self.assertEqual((settings.current_message_index, settings.current_group_index, settings.work_hours), (3, 7, 12))
            self.assertTrue(settings.is_running)
            self.assertEqual(User.objects.get(pk=user.pk).daily_send_count, 17)
            run = PublishingRun.objects.get(pk=old_run.pk)
            self.assertTrue(run.active)
            self.assertTrue(run.task_claimed)
            self.assertEqual(run.scheduler_version, 1)
            self.assertIsNone(run.next_due_at)
        finally:
            MigrationExecutor(connection).migrate(latest)
