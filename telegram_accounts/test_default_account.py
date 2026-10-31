import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless

from asgiref.sync import SyncToAsync
from django.db import IntegrityError, connection, connections, transaction
from django.test import TransactionTestCase

from telegram_accounts.models import TelegramAccount
from telegram_accounts.services.accounts import save_connected_account, select_publishing_account
from users.models import User


class DefaultAccountTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create(telegram_user_id=80001)

    def tearDown(self):
        SyncToAsync.single_thread_executor.submit(connections.close_all).result(timeout=10)

    def pending(self, phone):
        return TelegramAccount.objects.create(user=self.user, phone_number=phone, status="pending")

    def connect(self, identity=100):
        asyncio.run(save_connected_account(telegram_user_id=self.user.telegram_user_id,
            telegram_account_id=identity, username="mock", encrypted_session="ciphertext-preserved"))

    def test_first_successful_account_is_selected_and_later_account_does_not_replace_it(self):
        first = self.pending("+970111111111")
        self.assertFalse(first.is_publishing_account)
        self.connect()
        first.refresh_from_db()
        self.assertTrue(first.is_publishing_account)
        self.assertEqual(first.encrypted_session, "ciphertext-preserved")
        second = self.pending("+970222222222")
        self.connect(200)
        second.refresh_from_db()
        self.assertFalse(second.is_publishing_account)

    def test_manual_selection_survives_new_connection_and_is_owner_scoped(self):
        first = self.pending("+970111111111")
        self.connect()
        second = self.pending("+970222222222")
        self.connect(200)
        asyncio.run(select_publishing_account(telegram_user_id=self.user.telegram_user_id, account_id=second.pk))
        self.pending("+970333333333")
        self.connect(300)
        self.assertEqual(TelegramAccount.objects.get(is_publishing_account=True).pk, second.pk)
        other = User.objects.create(telegram_user_id=90002)
        with self.assertRaises(TelegramAccount.DoesNotExist):
            asyncio.run(select_publishing_account(telegram_user_id=other.telegram_user_id, account_id=first.pk))

    def test_database_rejects_two_selected_accounts(self):
        first = self.pending("+970111111111")
        self.connect()
        with self.assertRaises(IntegrityError), transaction.atomic():
            TelegramAccount.objects.create(user=self.user, phone_number="+970444444444", is_publishing_account=True)

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrent account selection")
    def test_competing_manual_choices_leave_one_selected_account(self):
        first = self.pending("+970111111111")
        self.connect()
        second = self.pending("+970222222222")
        self.connect(200)
        def choose(account_id):
            try:
                # Bypass only the async bridge so each thread has its own DB connection.
                select_publishing_account.__wrapped__(telegram_user_id=self.user.telegram_user_id, account_id=account_id)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(choose, [first.pk, second.pk]))
        self.assertEqual(TelegramAccount.objects.filter(user=self.user, is_publishing_account=True).count(), 1)
