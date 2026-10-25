import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import timedelta
from threading import Barrier
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync, sync_to_async
from django.db import close_old_connections, connections, transaction, IntegrityError
from django.test import TransactionTestCase
from django.utils import timezone

from messages.models import SavedMessage
from publishing.models import PublishingRun, PublishingSettings
from publishing.services import engine
from publishing.services.runs import (
    claim_run, create_run, finish_run, finish_run_async, run_is_active, run_policy,
)
from subscriptions.models import Subscription
from telegram_accounts.models import TelegramAccount
from telegram_accounts.services.accounts import select_publishing_account
from telegram_groups.models import TelegramGroup
from users.models import User


class SessionFixtures:
    def setUp(self):
        self.user, self.account, self.message, self.group = self.make_user(101)

    def make_user(self, identity):
        user = User.objects.create(telegram_user_id=identity)
        account = TelegramAccount.objects.create(
            user=user, phone_number=f"+1234567{identity}", status="connected",
            encrypted_session="encrypted-test-session", is_publishing_account=True,
        )
        message = SavedMessage.objects.create(user=user, text="test")
        group = TelegramGroup.objects.create(user=user, identifier=f"@group{identity}",
                                             canonical_identifier=f"username:group{identity}")
        return user, account, message, group

    def start(self, identity=101):
        run, _ = create_run(identity)
        self.assertIsNotNone(run)
        return run

    def fake_client(self):
        client = Mock()
        client.send_message = AsyncMock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.is_user_authorized = AsyncMock(return_value=True)

        @asynccontextmanager
        async def action(*args):
            yield

        client.action = action
        return client

    async def send_cycle(self, run, client):
        await engine._send_cycle(101, client, {}, timezone.now() + timedelta(minutes=5), run.pk, self.account)


class SessionSafetyTests(SessionFixtures, TransactionTestCase):
    def test_concurrent_start_uses_separate_database_connections(self):
        barrier = Barrier(2)

        def start():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return create_run(101)[0] is not None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: start(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(PublishingRun.objects.filter(user=self.user, active=True).count(), 1)

    def test_database_constraint_rejects_second_active_run(self):
        self.start()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PublishingRun.objects.create(user=self.user, account=self.account)

    def test_stop_start_invalidates_old_run_and_old_writes(self):
        old = self.start()
        finish_run(101)
        new = self.start()
        self.assertNotEqual(old.pk, new.pk)
        self.assertFalse(async_to_sync(run_is_active)(101, old.pk))
        finish_run(101, old.pk)
        async_to_sync(engine.advance_cycle_indices)(telegram_user_id=101, run_id=old.pk, message_index=99, group_index=99)
        self.assertTrue(async_to_sync(run_is_active)(101, new.pk))
        self.assertEqual(PublishingSettings.objects.get(user=self.user).current_message_index, 0)
        client = self.fake_client()
        async_to_sync(self.send_cycle)(old, client)
        client.send_message.assert_not_awaited()

    def test_duplicate_task_claim_and_legacy_task_are_noops(self):
        run = self.start()
        self.assertTrue(async_to_sync(claim_run)(101, run.pk))
        with patch.object(engine, "TelegramClient") as factory:
            async_to_sync(engine._run)(101, str(run.pk))
            async_to_sync(engine._run)(101)
        factory.assert_not_called()
        self.assertTrue(async_to_sync(run_is_active)(101, run.pk))

    def test_concurrent_claim_has_one_owner(self):
        run = self.start()
        barrier = Barrier(2)

        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                # Bypass the async adapter to exercise two actual DB connections.
                return claim_run.func(101, run.pk)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: claim(), range(2)))
        self.assertEqual(sorted(results), [False, True])

    def test_account_selection_applies_to_next_run(self):
        run = self.start()
        other = TelegramAccount.objects.create(user=self.user, phone_number="+1987654321",
                                              status="connected", encrypted_session="other-session")
        async_to_sync(select_publishing_account)(telegram_user_id=101, account_id=other.pk)
        snapshot = async_to_sync(engine.get_publish_snapshot)(101, run.pk)
        self.assertEqual(snapshot["account"].pk, self.account.pk)
        finish_run(101)
        self.assertEqual(self.start().account_id, other.pk)

    def test_stop_does_not_affect_another_user(self):
        self.make_user(202)
        first, second = self.start(), self.start(202)
        finish_run(101)
        self.assertFalse(async_to_sync(run_is_active)(101, first.pk))
        self.assertTrue(async_to_sync(run_is_active)(202, second.pk))

    def test_stop_during_typing_prevents_send(self):
        run, client = self.start(), self.fake_client()

        async def stop(_):
            await finish_run_async(101)

        with patch.object(engine.asyncio, "sleep", new=stop):
            async_to_sync(self.send_cycle)(run, client)
        client.send_message.assert_not_awaited()

    def test_stop_start_during_typing_cannot_attach_old_sender_to_new_run(self):
        run, client = self.start(), self.fake_client()
        new_runs = []

        async def restart(_):
            await finish_run_async(101)
            new, _ = await sync_to_async(create_run)(101)
            new_runs.append(new)

        with patch.object(engine.asyncio, "sleep", new=restart):
            async_to_sync(self.send_cycle)(run, client)
        client.send_message.assert_not_awaited()
        self.assertTrue(async_to_sync(run_is_active)(101, new_runs[0].pk))

    def test_two_users_can_execute_send_cycles_independently(self):
        _, other_account, _, _ = self.make_user(202)
        first, second = self.start(), self.start(202)
        client_a, client_b = self.fake_client(), self.fake_client()

        async def send_both():
            await asyncio.gather(
                self.send_cycle(first, client_a),
                engine._send_cycle(202, client_b, {}, timezone.now() + timedelta(minutes=5), second.pk, other_account),
            )

        with patch.object(engine.asyncio, "sleep", new=AsyncMock()):
            async_to_sync(send_both)()
        client_a.send_message.assert_awaited_once()
        client_b.send_message.assert_awaited_once()

    def test_subscription_expiry_during_typing_prevents_send(self):
        run, client = self.start(), self.fake_client()

        async def expire(_):
            await sync_to_async(Subscription.objects.filter(user=self.user).update)(end_date=timezone.now() - timedelta(seconds=1))

        with patch.object(engine.asyncio, "sleep", new=expire):
            async_to_sync(self.send_cycle)(run, client)
        client.send_message.assert_not_awaited()
        self.assertFalse(async_to_sync(run_is_active)(101, run.pk))

    def test_account_restriction_during_typing_prevents_send(self):
        run, client = self.start(), self.fake_client()

        async def restrict(_):
            await sync_to_async(TelegramAccount.objects.filter(pk=self.account.pk).update)(status="unauthorized")

        with patch.object(engine.asyncio, "sleep", new=restrict):
            async_to_sync(self.send_cycle)(run, client)
        client.send_message.assert_not_awaited()

    def test_disabled_groups_excluded_but_pending_eligible(self):
        run = self.start()
        for status in ("disabled", "unauthorized"):
            TelegramGroup.objects.filter(pk=self.group.pk).update(status=status)
            self.assertEqual(async_to_sync(engine.get_publish_snapshot)(101, run.pk)["groups"], [])
        TelegramGroup.objects.filter(pk=self.group.pk).update(status="pending")
        self.assertEqual(len(async_to_sync(engine.get_publish_snapshot)(101, run.pk)["groups"]), 1)

    def test_deleted_account_or_replaced_session_invalidates_permission(self):
        run = self.start()
        TelegramAccount.objects.filter(pk=self.account.pk).update(encrypted_session="replacement")
        self.assertIsNone(async_to_sync(run_policy)(101, run.pk, self.account.pk, self.account.encrypted_session))
        self.account.delete()
        self.assertIsNone(async_to_sync(run_policy)(101, run.pk, self.account.pk))

    def test_broker_failure_cleans_up_and_dispatch_includes_run_id(self):
        with patch("publishing.tasks.publish_user.delay", side_effect=RuntimeError("offline")) as delay:
            started, _ = async_to_sync(engine.start_publishing)(101)
        self.assertFalse(started)
        run = PublishingRun.objects.get(user=self.user)
        delay.assert_called_once_with(101, str(run.pk))
        self.assertFalse(run.active)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_publishing)

    def test_old_dispatch_failure_does_not_stop_new_run(self):
        new_runs = []

        def fail_after_restart(*args):
            finish_run(101)
            new_runs.append(create_run(101)[0])
            raise RuntimeError("offline")

        with patch("publishing.tasks.publish_user.delay", side_effect=fail_after_restart):
            self.assertFalse(async_to_sync(engine.start_publishing)(101)[0])
        self.assertTrue(async_to_sync(run_is_active)(101, new_runs[0].pk))

    def test_configuration_failure_cleans_up_claimed_run(self):
        run = self.start()
        with patch.object(engine.os, "getenv", return_value=None), patch.object(engine, "TelegramClient") as factory:
            with self.assertRaises(RuntimeError):
                async_to_sync(engine._run)(101, str(run.pk))
        factory.assert_not_called()
        self.assertFalse(async_to_sync(run_is_active)(101, run.pk))

    def test_connection_failure_disconnects_and_cleans_up(self):
        run, client = self.start(), self.fake_client()
        client.connect.side_effect = RuntimeError("connection failed")
        with patch.object(engine.os, "getenv", return_value="test"), \
                patch("cryptography.fernet.Fernet") as cipher, \
                patch.object(engine, "StringSession"), \
                patch.object(engine, "TelegramClient", return_value=client):
            cipher.return_value.decrypt.return_value = b"session"
            # API ID is parsed before the mocked Telegram constructor.
            with patch.object(engine.os, "getenv", side_effect=lambda name: "123" if name == "TELEGRAM_API_ID" else "test"):
                with self.assertRaises(RuntimeError):
                    async_to_sync(engine._run)(101, str(run.pk))
        client.disconnect.assert_awaited_once()
        self.assertFalse(async_to_sync(run_is_active)(101, run.pk))

    def test_successful_attempt_uses_mock_telegram_only(self):
        run, client = self.start(), self.fake_client()
        with patch.object(engine.asyncio, "sleep", new=AsyncMock()):
            async_to_sync(self.send_cycle)(run, client)
        client.send_message.assert_awaited_once_with(self.group.identifier, "test", formatting_entities=[])

    def test_cancellation_and_disconnect_failure_still_invalidate_run(self):
        run, client = self.start(), self.fake_client()
        client.connect.side_effect = asyncio.CancelledError()
        client.disconnect.side_effect = RuntimeError("disconnect failed")
        with patch.object(engine.os, "getenv", side_effect=lambda name: "123" if name == "TELEGRAM_API_ID" else "test"), \
                patch("cryptography.fernet.Fernet") as cipher, \
                patch.object(engine, "StringSession"), \
                patch.object(engine, "TelegramClient", return_value=client):
            cipher.return_value.decrypt.return_value = b"session"
            with self.assertRaises(asyncio.CancelledError):
                async_to_sync(engine._run)(101, str(run.pk))
        client.disconnect.assert_awaited_once()
        self.assertFalse(async_to_sync(run_is_active)(101, run.pk))
