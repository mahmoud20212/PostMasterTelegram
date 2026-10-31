from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DefaultAccountMigrationTests(TransactionTestCase):
    def test_backfill_preserves_explicit_choice_and_session_ciphertext(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        target = [("telegram_accounts", "0004_telegramaccount_last_error_at_and_more"),
                  ("users", "0003_user_daily_limit_notified_at_user_daily_send_count_and_more")]
        try:
            executor.migrate(target)
            apps = executor.loader.project_state(target).apps
            User = apps.get_model("users", "User")
            Account = apps.get_model("telegram_accounts", "TelegramAccount")
            first_user = User.objects.create(telegram_user_id=81001)
            second_user = User.objects.create(telegram_user_id=81002)
            oldest = Account.objects.create(user_id=first_user.pk, phone_number="+970111111111", status="connected", encrypted_session="original-one")
            Account.objects.create(user_id=first_user.pk, phone_number="+970111111112", status="connected", encrypted_session="original-two")
            Account.objects.create(user_id=second_user.pk, phone_number="+970111111113", status="connected", encrypted_session="original-three")
            chosen = Account.objects.create(user_id=second_user.pk, phone_number="+970111111114", status="connected", encrypted_session="original-four", is_publishing_account=True)
            MigrationExecutor(connection).migrate(latest)
            from telegram_accounts.models import TelegramAccount
            self.assertEqual(TelegramAccount.objects.get(user_id=first_user.pk, is_publishing_account=True).pk, oldest.pk)
            self.assertEqual(TelegramAccount.objects.get(user_id=second_user.pk, is_publishing_account=True).pk, chosen.pk)
            self.assertEqual(list(TelegramAccount.objects.order_by("pk").values_list("encrypted_session", flat=True)),
                             ["original-one", "original-two", "original-three", "original-four"])
        finally:
            MigrationExecutor(connection).migrate(latest)

    def test_existing_conflicting_choices_abort_without_deleting_accounts(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        target = [("telegram_accounts", "0004_telegramaccount_last_error_at_and_more"),
                  ("users", "0003_user_daily_limit_notified_at_user_daily_send_count_and_more")]
        Account = None
        try:
            executor.migrate(target)
            apps = executor.loader.project_state(target).apps
            user = apps.get_model("users", "User").objects.create(telegram_user_id=82001)
            Account = apps.get_model("telegram_accounts", "TelegramAccount")
            for phone in ("+970222222221", "+970222222222"):
                Account.objects.create(user_id=user.pk, phone_number=phone, is_publishing_account=True,
                                       status="connected", encrypted_session="unchanged-ciphertext")
            with self.assertRaisesMessage(RuntimeError, "Multiple publishing accounts selected"):
                MigrationExecutor(connection).migrate(latest)
            self.assertEqual(Account.objects.count(), 2)
            self.assertEqual(Account.objects.filter(is_publishing_account=True).count(), 2)
        finally:
            # Resolve only this artificial test conflict so the schema can be restored.
            if Account is not None:
                Account.objects.update(is_publishing_account=False)
            MigrationExecutor(connection).migrate(latest)
