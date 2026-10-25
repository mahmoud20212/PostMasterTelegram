import asyncio
import io
import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from threading import Event
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from celery import Celery
from celery.contrib.testing.worker import start_worker
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone
from telethon.errors import FloodWaitError, SlowModeWaitError

from publishing.models import PublishingRun, PublishingSettings
from publishing.services import engine
from publishing.services.runs import claim_run, finish_run, run_is_active
from publishing.services.contention import retry_transition
from publishing.tasks import publish_user
from publishing.test_session_safety import SessionFixtures
from subscriptions.models import Subscription
from users.models import User


class ProductionReadinessTests(SessionFixtures, TransactionTestCase):
    def recover(self, user=101, **kwargs):
        call_command("recover_publishing_run", user, stdout=io.StringIO(), **kwargs)

    def test_stalled_broker_does_not_block_stop_or_another_user(self):
        self.make_user(202)
        entered, release = Event(), Event()

        def broker(user, run_id):
            if user == 101:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("test broker was not released")
                raise OSError("offline")

        async def scenario():
            pending = asyncio.create_task(engine.start_publishing(101))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.assertEqual(await asyncio.wait_for(engine.stop_publishing(101), 1), "تم إيقاف النشر.")
                self.assertTrue((await asyncio.wait_for(engine.start_publishing(202), 1))[0])
            finally:
                release.set()
                self.assertFalse((await pending)[0])

        with patch("publishing.tasks.publish_user.delay", side_effect=broker):
            async_to_sync(scenario)()

    def test_lost_broker_response_invalidates_accepted_task(self):
        accepted = []

        def broker(*args):
            accepted.append(args)
            raise OSError("accepted but reply lost")

        with patch("publishing.tasks.publish_user.delay", side_effect=broker):
            self.assertFalse(async_to_sync(engine.start_publishing)(101)[0])
        with patch.object(engine, "TelegramClient") as factory:
            async_to_sync(engine._run)(*accepted[0])
        factory.assert_not_called()
        self.assertFalse(PublishingRun.objects.get(user=self.user).active)

    def test_recovery_requires_operator_confirmation(self):
        run = self.start()
        with self.assertRaises(CommandError):
            self.recover(run_id=run.pk)
        self.assertTrue(async_to_sync(run_is_active)(101, run.pk))

    def test_recover_crash_before_enqueue(self):
        run = self.start()
        self.recover(run_id=run.pk, confirm_execution_stopped=True)
        self.assertFalse(async_to_sync(run_is_active)(101, run.pk))
        self.assertNotEqual(self.start().pk, run.pk)

    def test_recover_crash_after_claim_never_resets_claim(self):
        run = self.start()
        self.assertTrue(async_to_sync(claim_run)(101, run.pk))
        self.recover(run_id=run.pk, confirm_execution_stopped=True)
        run.refresh_from_db()
        self.assertTrue(run.task_claimed)
        self.assertFalse(run.active)
        self.assertFalse(async_to_sync(claim_run)(101, run.pk))

    def test_stale_recovery_cannot_invalidate_new_or_other_users_run(self):
        old = self.start()
        finish_run(101)
        new = self.start()
        self.make_user(202)
        other = self.start(202)
        for user, target in ((101, old.pk), (202, new.pk)):
            with self.assertRaises(CommandError):
                self.recover(user, run_id=target, confirm_execution_stopped=True)
        self.assertTrue(async_to_sync(run_is_active)(101, new.pk))
        self.assertTrue(async_to_sync(run_is_active)(202, other.pk))
        self.assertFalse(async_to_sync(claim_run)(202, new.pk))

    def test_legacy_recovery_preserves_records_and_refuses_new_active_run(self):
        User.objects.filter(pk=self.user.pk).update(is_publishing=True)
        PublishingSettings.objects.create(user=self.user, is_running=True)
        self.recover(legacy=True, confirm_execution_stopped=True)
        self.assertEqual(self.user.saved_messages.count(), 1)
        self.assertEqual(self.user.telegram_accounts.count(), 1)
        new = self.start()
        with self.assertRaises(CommandError):
            self.recover(legacy=True, confirm_execution_stopped=True)
        self.assertTrue(async_to_sync(run_is_active)(101, new.pk))

    @contextmanager
    def write_lock(self):
        if connection.vendor != "sqlite" or "memory" in connection.settings_dict["NAME"]:
            self.skipTest("requires file-backed SQLite test settings")
        with connection.cursor() as cursor:
            original_timeout = cursor.execute("PRAGMA busy_timeout").fetchone()[0]
            cursor.execute("PRAGMA busy_timeout=20")
        holder = sqlite3.connect(connection.settings_dict["NAME"], timeout=0.02)
        try:
            holder.execute("BEGIN IMMEDIATE")
            yield
        finally:
            holder.rollback()
            holder.close()
            with connection.cursor() as cursor:
                cursor.execute(f"PRAGMA busy_timeout={original_timeout}")

    def test_start_contention_reports_failure_without_enqueue(self):
        with self.write_lock(), patch("publishing.tasks.publish_user.delay") as broker:
            started, text = async_to_sync(engine.start_publishing)(101)
        self.assertFalse(started)
        self.assertIn("مشغولة", text)
        broker.assert_not_called()
        self.assertFalse(PublishingRun.objects.exists())
        with patch("publishing.tasks.publish_user.delay") as broker:
            self.assertTrue(async_to_sync(engine.start_publishing)(101)[0])
        broker.assert_called_once()

    def test_stop_contention_never_reports_success(self):
        run = self.start()
        with self.write_lock():
            text = async_to_sync(engine.stop_publishing)(101)
        self.assertIn("لم يتم تأكيد الإيقاف", text)
        self.assertTrue(async_to_sync(run_is_active)(101, run.pk))
        self.assertEqual(async_to_sync(engine.stop_publishing)(101), "تم إيقاف النشر.")

    def test_unrelated_database_error_is_not_reported_as_contention(self):
        with patch.object(engine, "create_run_async", new=AsyncMock(side_effect=OperationalError("no such table"))):
            with self.assertRaises(OperationalError):
                async_to_sync(engine.start_publishing)(101)

    def test_retry_rolls_back_transition_and_enqueues_only_once(self):
        original = PublishingRun.objects.create
        attempts = []

        def busy_once(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise OperationalError("database is locked")
            return original(*args, **kwargs)

        with patch.object(PublishingRun.objects, "create", side_effect=busy_once), \
                patch("publishing.tasks.publish_user.delay") as broker:
            self.assertTrue(async_to_sync(engine.start_publishing)(101)[0])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(PublishingRun.objects.filter(active=True).count(), 1)
        broker.assert_called_once()

    def test_contention_retries_are_bounded(self):
        calls = []

        @retry_transition
        def always_busy():
            calls.append(True)
            raise OperationalError("database is locked")

        with self.assertRaises(OperationalError):
            always_busy()
        self.assertEqual(len(calls), 3)

    def test_additive_migration_preserves_legacy_user_state(self):
        User.objects.filter(pk=self.user.pk).update(is_publishing=True)
        executor = MigrationExecutor(connection)
        try:
            executor.migrate([("publishing", "0006_publishlog")])
            executor = MigrationExecutor(connection)
            executor.migrate([("publishing", "0007_publishingrun")])
            self.user.refresh_from_db()
            self.assertTrue(self.user.is_publishing)
            self.assertEqual(self.user.saved_messages.count(), 1)
            self.assertEqual(self.user.telegram_groups.count(), 1)
            self.assertFalse(PublishingRun.objects.exists())
        finally:
            MigrationExecutor(connection).migrate([("publishing", "0007_publishingrun")])

    def test_flood_wait_restricts_bound_account_and_stops_run(self):
        run, client = self.start(), self.fake_client()
        client.send_message.side_effect = FloodWaitError(request=None, capture=120)
        with patch.object(engine.asyncio, "sleep", new=AsyncMock()):
            async_to_sync(self.send_cycle)(run, client)
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, "paused_flood_wait")
        self.assertGreater(self.account.paused_until, timezone.now())
        self.assertFalse(async_to_sync(run_is_active)(101, run.pk))

    def test_slow_mode_keeps_run_and_sets_group_cooldown(self):
        run, client = self.start(), self.fake_client()
        client.send_message.side_effect = SlowModeWaitError(request=None, capture=30)
        cooldowns = {}
        with patch.object(engine.asyncio, "sleep", new=AsyncMock()):
            async_to_sync(engine._send_cycle)(101, client, cooldowns, timezone.now() + timedelta(minutes=5), run.pk, self.account)
        self.assertIn(self.group.identifier, cooldowns)
        self.assertTrue(async_to_sync(run_is_active)(101, run.pk))

    def test_expiry_prevents_next_send_after_cycle_rest(self):
        run = self.start()
        Subscription.objects.filter(user=self.user).update(end_date=timezone.now() - timedelta(seconds=1))
        client = self.fake_client()
        async_to_sync(self.send_cycle)(run, client)
        client.send_message.assert_not_awaited()

    def execute_client(self, client, callback=None):
        with patch.object(engine.os, "getenv", side_effect=lambda name: "123" if name == "TELEGRAM_API_ID" else "test"), \
                patch("cryptography.fernet.Fernet") as cipher, patch.object(engine, "StringSession"), \
                patch.object(engine, "TelegramClient", return_value=client):
            cipher.return_value.decrypt.return_value = b"session"
            run = self.start()
            if callback:
                callback(run)
            async_to_sync(engine._run)(101, str(run.pk))

    def test_connection_error_preserved_if_disconnect_also_fails(self):
        client = self.fake_client()
        client.connect.side_effect = ValueError("primary")
        client.disconnect.side_effect = RuntimeError("cleanup")
        with self.assertLogs(engine.logger, level="ERROR") as logs, self.assertRaisesRegex(ValueError, "primary"):
            self.execute_client(client)
        self.assertTrue(any("disconnect failed" in line for line in logs.output))
        self.assertFalse(PublishingRun.objects.get(user=self.user).active)

    def test_disconnect_failure_without_primary_error_is_raised(self):
        client = self.fake_client()
        client.is_user_authorized.return_value = False
        client.disconnect.side_effect = RuntimeError("cleanup")
        with self.assertRaisesRegex(RuntimeError, "cleanup"):
            self.execute_client(client)
        self.assertFalse(PublishingRun.objects.get(user=self.user).active)

    def test_worker_cancellation_preserved_and_run_cleaned(self):
        client = self.fake_client()
        client.connect.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.execute_client(client)
        client.disconnect.assert_awaited_once()
        self.assertFalse(PublishingRun.objects.get(user=self.user).active)

    def test_db_cleanup_failure_does_not_skip_disconnect_or_mask_primary(self):
        client = self.fake_client()
        client.connect.side_effect = ValueError("primary")
        with patch.object(engine, "finish_run_async", new=AsyncMock(side_effect=OperationalError("database is locked"))), \
                self.assertLogs(engine.logger, level="ERROR") as logs, self.assertRaisesRegex(ValueError, "primary"):
            self.execute_client(client)
        client.disconnect.assert_awaited_once()
        self.assertTrue(any("Run cleanup failed" in line for line in logs.output))

    def test_broker_configuration_has_finite_producer_retries(self):
        from config.celery import app
        self.assertEqual(app.conf.broker_connection_timeout, 3)
        self.assertEqual(app.conf.broker_transport_options["socket_timeout"], 3)
        self.assertEqual(app.conf.broker_transport_options["socket_connect_timeout"], 3)
        self.assertEqual(app.conf.task_publish_retry_policy["max_retries"], 1)

    def test_in_memory_celery_worker_executes_real_task_wrapper(self):
        # Real non-eager solo worker, isolated broker/backend; no Redis or Telegram.
        app = Celery("onepost-isolated-test", broker="memory://", backend="cache+memory://", fixups=[], set_as_current=False)
        app.conf.update(task_default_queue="onepost-isolated-test", task_always_eager=False)
        task = app.task(name="onepost.isolated.publish_user")(publish_user.run)
        try:
            with patch.object(engine, "_run", new=AsyncMock()) as runner:
                with start_worker(app, pool="solo", concurrency=1, perform_ping_check=False, shutdown_timeout=10):
                    task.apply_async(args=(101, "test-run")).get(timeout=5)
                runner.assert_awaited_once_with(101, "test-run")
        finally:
            app.close()

    def test_legacy_signature_is_supported_only_by_new_wrapper(self):
        # Model the actual old Python signature, without importing old code.
        def old_task(telegram_user_id):
            return telegram_user_id
        with self.assertRaises(TypeError):
            old_task(101, "new-run")
        with patch.object(engine, "TelegramClient") as factory:
            publish_user.run(101)
        factory.assert_not_called()
