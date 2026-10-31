"""Mock-only Telegram tests; transaction tests also run against PostgreSQL."""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import SyncToAsync, sync_to_async
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, IntegrityError, OperationalError, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from telethon.errors import BadRequestError, ChatWriteForbiddenError, FloodWaitError, PeerFloodError, SlowModeWaitError

from messages.models import SavedMessage
from publishing.models import PublishingAttempt, PublishingCooldown, PublishingRun, PublishingSettings, PublishLog
from publishing.services.engine import execute_step, start_publishing
from publishing.services.execution_lock import execution_lock
from publishing.services.runs import create_run, finish_run
from publishing.services.scheduler import dispatch_due, recover_expired
from publishing.services.steps import begin_send, claim_step, complete_attempt
from subscriptions.models import Subscription, SubscriptionPlan
from telegram_accounts.models import TelegramAccount
from telegram_groups.models import TelegramGroup
from users.models import User


@override_settings(PUBLISHING_MAX_PENDING=4, PUBLISHING_DISPATCH_BATCH=4)
class DurablePublishingTests(TransactionTestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.key = Fernet.generate_key()
        self.ciphertext = Fernet(self.key).encrypt(b"mock-session").decode()
        self.plan = SubscriptionPlan.objects.create(code="test", name="Test", daily_send_limit=100,
            min_interval_seconds=30, max_interval_seconds=30, cycle_rest_seconds=60)
        self.queue = []
        self.client = SimpleNamespace(
            connect=AsyncMock(), is_user_authorized=AsyncMock(return_value=True),
            get_input_entity=AsyncMock(side_effect=lambda value: value),
            send_message=AsyncMock(return_value=SimpleNamespace(id=123)), disconnect=AsyncMock(),
        )
        self.clock = patch("django.utils.timezone.now", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        for target, value in (("publishing.services.engine.TelegramClient", MagicMock(return_value=self.client)),
                              ("publishing.services.engine.StringSession", MagicMock())):
            mock = patch(target, value)
            mock.start()
            self.addCleanup(mock.stop)
        env = patch.dict("os.environ", TELEGRAM_SESSION_ENCRYPTION_KEY=self.key.decode(), TELEGRAM_API_ID="123", TELEGRAM_API_HASH="mock-api-hash")
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        # asyncio.run bridges use this executor; don't leave test DB sessions open.
        SyncToAsync.single_thread_executor.submit(connections.close_all).result(timeout=10)

    def make_user(self, identity=1, group_count=3):
        user = User.objects.create(telegram_user_id=identity)
        PublishingSettings.objects.create(user=user, cycle_duration_minutes=5)
        TelegramAccount.objects.create(user=user, telegram_user_id=10000 + identity,
            phone_number=f"+970000{identity:05}", status="connected", encrypted_session=self.ciphertext)
        Subscription.objects.create(user=user, plan_definition=self.plan, status="active")
        SavedMessage.objects.create(user=user, text=f"message {identity}")
        for number in range(group_count):
            TelegramGroup.objects.create(user=user, identifier=f"@group{identity}_{number}", canonical_identifier=f"username:group{identity}_{number}")
        run, _ = create_run(identity)
        self.assertIsNotNone(run)
        return user, run

    def tick(self, publish=None):
        return dispatch_due(publish=publish or (lambda *args: self.queue.append(args)))

    def execute(self, index=0):
        execute_step(*self.queue[index])

    def advance(self, seconds=30):
        self.now += timedelta(seconds=seconds)

    def test_start_is_durable_and_does_not_publish_to_broker(self):
        user, old = self.make_user()
        finish_run(user.telegram_user_id)
        with patch("publishing.tasks.publish_step.apply_async") as broker:
            started, _ = asyncio.run(start_publishing(user.telegram_user_id))
        self.assertTrue(started)
        broker.assert_not_called()
        run = PublishingRun.objects.get(user=user, active=True)
        self.assertEqual(run.scheduler_version, 2)
        self.assertEqual(run.next_due_at, self.now)
        self.assertNotEqual(old.pk, run.pk)

    def test_one_send_then_worker_returns_without_interval_sleep_or_typing(self):
        user, run = self.make_user()
        self.tick()
        with patch("publishing.services.engine.asyncio.sleep", side_effect=AssertionError("No scheduling sleeps")):
            self.execute()
        self.client.send_message.assert_awaited_once()
        self.client.disconnect.assert_awaited_once()
        self.assertFalse(hasattr(self.client, "action"))
        run.refresh_from_db()
        self.assertTrue(run.active)
        self.assertEqual(run.cycle_position, 1)
        self.assertEqual(run.next_due_at, self.now + timedelta(seconds=30))
        self.assertEqual(PublishingAttempt.objects.get().status, "success")
        user.refresh_from_db()
        self.assertEqual(user.daily_send_count, 1)

    def test_duplicate_delivery_and_duplicate_completion_do_not_send_or_advance_twice(self):
        user, run = self.make_user()
        self.tick()
        self.execute()
        self.execute()
        attempt = PublishingAttempt.objects.get()
        self.assertFalse(complete_attempt(attempt.pk, "success"))
        self.client.send_message.assert_awaited_once()
        self.assertEqual(PublishLog.objects.count(), 1)
        run.refresh_from_db()
        self.assertEqual(run.cycle_position, 1)

    def test_confirmed_failure_is_logged_refunded_and_next_item_sends(self):
        user, run = self.make_user()
        self.client.send_message.side_effect = BadRequestError(request=None, message="MOCK_REJECTION")
        self.tick()
        self.execute()
        self.assertEqual(PublishingAttempt.objects.get().status, "failed")
        self.assertEqual(PublishLog.objects.get().status, "failed")
        user.refresh_from_db()
        self.assertEqual(user.daily_send_count, 0)
        run.refresh_from_db()
        self.assertTrue(run.active)
        self.assertEqual(run.cycle_position, 1)
        self.client.send_message.side_effect = None
        self.advance()
        self.tick()
        self.execute(1)
        self.assertEqual(self.client.send_message.await_args_list[1].args[0], "@group1_1")
        self.assertEqual(PublishingAttempt.objects.filter(status="success").count(), 1)

    def test_send_timeout_is_unknown_charged_and_never_retried(self):
        user, run = self.make_user()
        self.client.send_message.side_effect = TimeoutError()
        self.tick()
        self.execute()
        self.execute()
        self.assertEqual(PublishingAttempt.objects.get().status, "unknown")
        user.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(user.daily_send_count, 1)
        self.assertTrue(run.active)
        self.assertEqual(run.cycle_position, 1)
        self.client.send_message.side_effect = None
        self.advance()
        self.tick()
        self.execute(1)
        self.assertEqual(self.client.send_message.await_count, 2)
        self.assertEqual(self.client.send_message.await_args_list[1].args[0], "@group1_1")

    @override_settings(PUBLISHING_STEP_TIMEOUT=0.3)
    def test_actual_async_deadline_consumes_unknown_without_pausing_run(self):
        user, run = self.make_user()
        async def blocked(*args, **kwargs):
            await asyncio.Event().wait()
        self.client.send_message.side_effect = blocked
        self.tick()
        self.execute()
        self.assertEqual(PublishingAttempt.objects.get().status, "unknown")
        run.refresh_from_db()
        self.assertTrue(run.active)

    def test_connect_failure_is_confirmed_unsent_and_advances(self):
        user, run = self.make_user()
        self.client.connect.side_effect = ConnectionError()
        self.tick()
        self.execute()
        self.client.send_message.assert_not_awaited()
        attempt = PublishingAttempt.objects.get()
        self.assertEqual(attempt.status, "failed")
        self.assertFalse(attempt.quota_reserved)
        run.refresh_from_db()
        self.assertEqual(run.cycle_position, 1)

    def test_crash_after_send_before_result_commit_recovers_unknown_and_continues(self):
        user, run = self.make_user()
        self.tick()
        with patch("publishing.services.engine.complete_attempt", side_effect=DatabaseError("mock DB failure")):
            with self.assertRaises(DatabaseError):
                self.execute()
        self.assertEqual(PublishingAttempt.objects.get().status, "sending")
        self.advance(100)
        self.assertEqual(recover_expired(), 1)
        self.assertEqual(recover_expired(), 0)
        attempt = PublishingAttempt.objects.get()
        self.assertEqual(attempt.status, "unknown")
        self.execute()  # stale delivery of consumed item
        self.advance()
        self.tick()
        self.execute(1)
        self.assertEqual(self.client.send_message.await_count, 2)
        run.refresh_from_db()
        self.assertTrue(run.active)

    def test_expired_pre_send_claim_records_failure_not_unknown(self):
        user, run = self.make_user()
        self.tick()
        payload = claim_step(*self.queue[0])
        self.advance(100)
        self.assertEqual(recover_expired(), 1)
        attempt = PublishingAttempt.objects.get(pk=payload["attempt_id"])
        self.assertEqual(attempt.status, "failed")
        run.refresh_from_db()
        self.assertEqual(run.cycle_position, 1)

    def test_expiry_does_not_steal_a_live_workers_execution_lock(self):
        user, run = self.make_user()
        self.tick()
        payload = claim_step(*self.queue[0])
        self.assertTrue(begin_send(payload["attempt_id"]))
        self.advance(100)
        with execution_lock("user", user.pk) as acquired:
            self.assertTrue(acquired)
            self.assertEqual(recover_expired(), 0)
            self.assertEqual(PublishingAttempt.objects.get().status, "sending")
        self.assertEqual(recover_expired(), 1)

    def test_broker_failure_preserves_run_and_expiry_republishes_safely(self):
        user, run = self.make_user()
        def unavailable(*args):
            self.queue.append(args)
            raise ConnectionError("mock broker")
        self.assertEqual(self.tick(unavailable), 0)
        run.refresh_from_db()
        self.assertTrue(run.active)
        self.assertEqual(run.dispatch_error, "ConnectionError")
        self.assertEqual(self.tick(), 0)
        self.advance(31)
        self.assertEqual(self.tick(), 1)
        self.execute(0)  # old token is fenced
        self.client.send_message.assert_not_awaited()
        self.execute(1)
        self.client.send_message.assert_awaited_once()

    def test_broker_accepted_then_raised_can_still_deliver_original_token(self):
        user, run = self.make_user()
        def accepted(*args):
            self.queue.append(args)
            raise ConnectionError()
        self.tick(accepted)
        self.execute()
        self.assertEqual(PublishingAttempt.objects.get().status, "success")

    def test_deleted_planned_target_is_consumed_without_sending(self):
        user, run = self.make_user()
        self.tick()
        self.execute()
        TelegramGroup.objects.filter(user=user, identifier="@group1_1").delete()
        self.advance()
        self.tick()
        self.execute(1)
        self.assertEqual(self.client.send_message.await_count, 1)
        self.assertEqual(PublishingAttempt.objects.filter(status="skipped").count(), 1)
        run.refresh_from_db()
        self.assertEqual(run.cycle_position, 2)

    def test_group_rejection_disables_only_its_owners_group(self):
        first, run = self.make_user(1)
        second, _ = self.make_user(2)
        self.client.send_message.side_effect = ChatWriteForbiddenError(request=None)
        self.tick()
        first_index = next(i for i, item in enumerate(self.queue) if item[0] == str(run.pk))
        self.execute(first_index)
        self.assertEqual(TelegramGroup.objects.filter(user=first, status="disabled").count(), 1)
        self.assertFalse(TelegramGroup.objects.filter(user=second, status="disabled").exists())

    def test_account_restriction_does_not_stop_another_users_run(self):
        first, run = self.make_user(1)
        second, other = self.make_user(2)
        self.client.send_message.side_effect = PeerFloodError(request=None)
        self.tick()
        first_index = next(i for i, item in enumerate(self.queue) if item[0] == str(run.pk))
        self.execute(first_index)
        run.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(run.active)
        self.assertTrue(other.active)
        self.assertEqual(TelegramAccount.objects.get(user=first).status, "restricted")
        self.assertEqual(TelegramAccount.objects.get(user=second).status, "connected")

    def test_cross_owner_account_never_connects(self):
        first, run = self.make_user(1)
        second, _ = self.make_user(2)
        PublishingRun.objects.filter(pk=run.pk).update(account=TelegramAccount.objects.get(user=second))
        self.tick()
        first_index = next(i for i, item in enumerate(self.queue) if item[0] == str(run.pk))
        self.execute(first_index)
        self.client.connect.assert_not_awaited()
        run.refresh_from_db()
        self.assertFalse(run.active)

    def test_more_users_than_slots_all_receive_a_turn(self):
        users = [self.make_user(number)[0] for number in range(1, 10)]
        for batch_size in (4, 4, 1):
            start = len(self.queue)
            self.assertEqual(self.tick(), batch_size)
            for index in range(start, len(self.queue)):
                self.execute(index)
        self.assertEqual(PublishingAttempt.objects.values("user_id").distinct().count(), len(users))
        self.assertEqual(self.client.send_message.await_count, 9)

    def test_overdue_user_moves_behind_users_not_yet_dispatched(self):
        with override_settings(PUBLISHING_MAX_PENDING=1):
            first, run = self.make_user(1)
            self.advance(1)
            second, _ = self.make_user(2)
            self.tick()
            self.execute()
            self.advance(31)
            self.tick()
            self.execute(1)
        self.assertEqual(list(PublishingAttempt.objects.order_by("started_at").values_list("user_id", flat=True)), [first.pk, second.pk])

    def test_stop_one_user_leaves_other_user_running(self):
        first, run = self.make_user(1)
        second, other = self.make_user(2)
        self.tick()
        finish_run(first.telegram_user_id)
        for index in range(len(self.queue)):
            self.execute(index)
        self.assertFalse(PublishingAttempt.objects.filter(user=first).exists())
        self.assertEqual(PublishingAttempt.objects.get(user=second).status, "success")
        other.refresh_from_db()
        self.assertTrue(other.active)

    def test_stop_during_connect_prevents_send(self):
        user, run = self.make_user()
        async def stop():
            await sync_to_async(finish_run)(user.telegram_user_id)
        self.client.connect.side_effect = stop
        self.tick()
        self.execute()
        self.client.send_message.assert_not_awaited()
        self.assertEqual(PublishingAttempt.objects.get().status, "skipped")

    def test_external_subscription_stop_is_reconciled_without_dispatch(self):
        user, run = self.make_user()
        User.objects.filter(pk=user.pk).update(is_publishing=False)
        self.assertEqual(self.tick(), 0)
        run.refresh_from_db()
        self.assertFalse(run.active)
        self.assertFalse(PublishingSettings.objects.get(user=user).is_running)

    def test_old_completion_cannot_change_replacement_generation(self):
        user, old = self.make_user()
        self.tick()
        payload = claim_step(*self.queue[0])
        begin_send(payload["attempt_id"])
        finish_run(user.telegram_user_id)
        replacement, _ = create_run(user.telegram_user_id)
        PublishingSettings.objects.filter(user=user).update(current_group_index=2)
        complete_attempt(payload["attempt_id"], "interrupted")
        replacement.refresh_from_db()
        self.assertTrue(replacement.active)
        self.assertEqual(replacement.cycle_position, 0)
        self.assertEqual(PublishingSettings.objects.get(user=user).current_group_index, 2)

    def test_database_constraint_prevents_open_attempt_across_generations(self):
        user, run = self.make_user()
        self.tick()
        payload = claim_step(*self.queue[0])
        attempt = PublishingAttempt.objects.get(pk=payload["attempt_id"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            attempt.pk = uuid.uuid4()
            attempt.sequence += 1
            attempt.save(force_insert=True)

    def test_quota_limit_stops_only_that_user(self):
        self.plan.daily_send_limit = 1
        self.plan.save()
        user, run = self.make_user()
        self.tick()
        self.execute()
        self.advance()
        self.tick()
        self.execute(1)
        self.client.send_message.assert_awaited_once()
        run.refresh_from_db()
        self.assertFalse(run.active)
        user.refresh_from_db()
        self.assertEqual(user.daily_send_count, 1)

    def test_refund_does_not_decrement_a_new_quota_window(self):
        user, run = self.make_user()
        self.tick()
        payload = claim_step(*self.queue[0])
        begin_send(payload["attempt_id"])
        User.objects.filter(pk=user.pk).update(daily_send_count=7, daily_send_reset_at=self.now + timedelta(days=2))
        complete_attempt(payload["attempt_id"], "failed")
        user.refresh_from_db()
        self.assertEqual(user.daily_send_count, 7)

    def test_flood_wait_persists_account_delay_and_resumes_without_replaying_item(self):
        user, run = self.make_user()
        self.client.send_message.side_effect = FloodWaitError(request=None, capture=120)
        self.tick()
        self.execute()
        account = TelegramAccount.objects.get(user=user)
        self.assertEqual(account.status, "paused_flood_wait")
        run.refresh_from_db()
        self.assertTrue(run.active)
        self.assertEqual(run.next_due_at, self.now + timedelta(seconds=120))
        self.client.send_message.side_effect = None
        self.advance(120)
        self.tick()
        self.execute(1)
        self.assertEqual(self.client.send_message.await_args_list[1].args[0], "@group1_1")
        account.refresh_from_db()
        self.assertEqual(account.status, "connected")

    def test_slow_mode_cooldown_is_account_scoped_and_survives_steps(self):
        user, run = self.make_user()
        self.client.send_message.side_effect = SlowModeWaitError(request=None, capture=900)
        self.tick()
        self.execute()
        cooldown = PublishingCooldown.objects.get()
        self.assertEqual(cooldown.account.user_id, user.pk)
        self.assertEqual(cooldown.until, self.now + timedelta(seconds=900))
        self.client.send_message.side_effect = None
        self.advance()
        self.tick()
        self.execute(1)
        self.assertEqual(self.client.send_message.await_count, 2)
        # Re-enter first group in a later cycle: cooldown skips it without I/O.
        self.advance(30)
        self.tick()
        self.execute(2)  # finish the original pass at t=60
        self.advance(300)
        self.tick()
        self.execute(3)  # first group of the next pass, before cooldown expiry
        self.assertTrue(PublishingAttempt.objects.filter(status="skipped", error_type="SlowModeCooldown").exists())

    def test_subscription_expiry_before_send_prevents_network_request(self):
        user, run = self.make_user()
        async def expire():
            await sync_to_async(Subscription.objects.filter(user=user).update)(end_date=self.now - timedelta(seconds=1))
        self.client.connect.side_effect = expire
        self.tick()
        self.execute()
        self.client.send_message.assert_not_awaited()
        run.refresh_from_db()
        self.assertFalse(run.active)

    def test_replaced_session_before_send_does_not_use_stale_authorization(self):
        user, run = self.make_user()
        replacement = Fernet(self.key).encrypt(b"replacement").decode()
        async def replace():
            await sync_to_async(TelegramAccount.objects.filter(user=user).update)(encrypted_session=replacement)
        self.client.connect.side_effect = replace
        self.tick()
        self.execute()
        self.client.send_message.assert_not_awaited()
        self.assertEqual(TelegramAccount.objects.get(user=user).encrypted_session, replacement)

    def test_cycle_completion_schedules_rest_without_occupying_worker(self):
        user, run = self.make_user(group_count=1)
        self.tick()
        self.execute()
        run.refresh_from_db()
        self.assertEqual(run.next_due_at, self.now + timedelta(seconds=360))
        self.assertEqual(run.cycle_plan, [])
        self.assertEqual(self.tick(), 0)

    def test_sleep_and_empty_targets_only_reschedule(self):
        user, run = self.make_user()
        self.now = self.now.replace(hour=13)
        PublishingRun.objects.filter(pk=run.pk).update(next_due_at=self.now)
        PublishingSettings.objects.filter(user=user).update(sleep_enabled=True, work_hours=1, sleep_hours=23)
        self.tick()  # legacy enabled setting initializes a full work period
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "work")
        self.advance(3600)
        self.tick()  # enter rest without a worker task
        self.assertEqual(PublishingAttempt.objects.count(), 0)
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "rest")
        PublishingSettings.objects.filter(user=user).update(sleep_enabled=False)
        SavedMessage.objects.filter(user=user).update(is_active=False)
        self.advance(60)
        self.tick()
        self.execute(len(self.queue) - 1)
        self.client.connect.assert_not_awaited()

    def test_legacy_runs_and_queued_task_type_are_not_implicitly_adopted(self):
        user, run = self.make_user()
        PublishingRun.objects.filter(pk=run.pk).update(scheduler_version=1, next_due_at=None)
        self.assertEqual(self.tick(), 0)
        from publishing.tasks import publish_user
        publish_user(user.telegram_user_id, str(run.pk))
        self.client.connect.assert_not_awaited()
        with self.assertRaises(CommandError):
            call_command("upgrade_publishing_runs")
        call_command("upgrade_publishing_runs", confirm_execution_stopped=True, verbosity=0)
        self.assertEqual(self.tick(), 1)
        self.assertEqual(TelegramAccount.objects.get(user=user).encrypted_session, self.ciphertext)

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrent connections")
    def test_concurrent_duplicate_workers_have_one_sender(self):
        user, run = self.make_user()
        self.tick()
        def invoke():
            try:
                execute_step(*self.queue[0])
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: invoke(), range(8)))
        self.client.send_message.assert_awaited_once()
        self.assertEqual(PublishingAttempt.objects.count(), 1)

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrent connections")
    def test_concurrent_dispatchers_preserve_global_backlog_bound(self):
        for number in range(1, 10):
            self.make_user(number)
        def invoke():
            try:
                return self.tick()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: invoke(), range(4)))
        self.assertEqual(len(self.queue), 4)
        self.assertEqual(len({event[0] for event in self.queue}), 4)

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrent connections")
    def test_four_different_users_can_send_in_parallel(self):
        for number in range(1, 5):
            self.make_user(number)
        self.tick()
        barrier = threading.Barrier(4)
        async def send(*args, **kwargs):
            barrier.wait(timeout=10)
            return SimpleNamespace(id=123)
        self.client.send_message.side_effect = send
        def invoke(event):
            try:
                execute_step(*event)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(invoke, self.queue))
        self.assertEqual(PublishingAttempt.objects.filter(status="success").count(), 4)
        self.assertEqual(User.objects.filter(daily_send_count=1).count(), 4)
        for call in self.client.send_message.await_args_list:
            identity = call.args[1].split()[-1]
            self.assertTrue(call.args[0].startswith(f"@group{identity}_"))

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL lock timeouts")
    @override_settings(PUBLISHING_DB_LOCK_TIMEOUT_MS=50)
    def test_database_contention_defers_one_user_without_losing_schedule(self):
        user, run = self.make_user()
        from publishing.services.scheduler import reserve_dispatch
        def blocked():
            try:
                with self.assertRaises(OperationalError):
                    reserve_dispatch(run.pk, now=self.now)
            finally:
                connections.close_all()
        with transaction.atomic():
            User.objects.select_for_update().get(pk=user.pk)
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(blocked).result(timeout=5)
        self.assertEqual(self.tick(), 1)
        self.execute()
        self.assertEqual(PublishingAttempt.objects.get().status, "success")

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL process-lifetime locks")
    def test_process_death_releases_execution_guard_for_unknown_recovery(self):
        user, run = self.make_user()
        self.tick()
        payload = claim_step(*self.queue[0])
        begin_send(payload["attempt_id"])
        self.advance(100)
        key = int.from_bytes(hashlib.blake2b(f"onepost:user:{user.pk}".encode(), digest_size=8).digest(), "big", signed=True)
        config = connection.settings_dict
        params = {"dbname": config["NAME"], "connect_timeout": 3}
        for field, argument in (("HOST", "host"), ("PORT", "port"), ("USER", "user"), ("PASSWORD", "password")):
            if config.get(field):
                params[argument] = config[field]
        code = "import sys,json,psycopg; p=json.loads(sys.stdin.readline()); key=p.pop('key'); c=psycopg.connect(**p,autocommit=True); c.execute('SELECT pg_advisory_lock(%s)',[key]); print('locked',flush=True); sys.stdin.readline()"
        process = subprocess.Popen([sys.executable, "-B", "-c", code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            process.stdin.write(json.dumps({**params, "key": key}) + "\n")
            process.stdin.flush()
            self.assertEqual(process.stdout.readline().strip(), "locked")
            self.assertEqual(recover_expired(), 0)
            process.terminate()
            process.wait(timeout=5)
            self.assertEqual(recover_expired(), 1)
            self.assertEqual(PublishingAttempt.objects.get().status, "unknown")
            run.refresh_from_db()
            self.assertTrue(run.active)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
