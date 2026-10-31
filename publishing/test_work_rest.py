import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import sync_to_async
from django.test import TransactionTestCase, override_settings

from notifications.models import BotNotification
from publishing.models import PublishingAttempt, PublishingRun, PublishingSettings
from publishing.services.runs import create_run, finish_run
from publishing.services.settings import toggle_sleep, update_work_sleep_hours
from publishing.services.steps import begin_send, claim_step, complete_attempt
from publishing.services.work_rest import synchronize_run
from publishing import test_durable_engine as fixtures


@override_settings(PUBLISHING_MAX_PENDING=4, PUBLISHING_DISPATCH_BATCH=4)
class WorkRestTests(TransactionTestCase):
    setUp = fixtures.DurablePublishingTests.setUp
    tearDown = fixtures.DurablePublishingTests.tearDown
    make_user = fixtures.DurablePublishingTests.make_user
    tick = fixtures.DurablePublishingTests.tick
    execute = fixtures.DurablePublishingTests.execute
    advance = fixtures.DurablePublishingTests.advance

    def enable(self, user, work=1, rest=1):
        return asyncio.run(update_work_sleep_hours(telegram_user_id=user.telegram_user_id, work_hours=work, sleep_hours=rest))

    def test_enable_at_any_hour_starts_full_work_then_rest_then_work(self):
        user, run = self.make_user()
        self.now = self.now.replace(hour=23)
        self.enable(user, work=2, rest=3)
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "work")
        self.assertEqual(run.phase_ends_at, self.now + timedelta(hours=2))
        self.advance(7200)
        synchronize_run(run.pk)
        synchronize_run(run.pk)
        run.refresh_from_db()
        self.assertTrue(run.active)
        self.assertEqual(run.work_rest_phase, "rest")
        self.assertEqual(run.phase_ends_at, self.now + timedelta(hours=3))
        self.assertEqual(BotNotification.objects.filter(kind="sleep_started").count(), 1)
        self.advance(10800)
        synchronize_run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "work")
        self.assertEqual(BotNotification.objects.filter(kind="sleep_ended").count(), 1)

    def test_sleep_transitions_even_when_next_send_is_far_in_future(self):
        user, run = self.make_user()
        self.enable(user)
        PublishingRun.objects.filter(pk=run.pk).update(next_due_at=self.now + timedelta(days=2))
        self.advance(3600)
        self.assertEqual(self.tick(), 0)
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "rest")
        self.assertEqual(PublishingAttempt.objects.count(), 0)
        self.assertEqual(BotNotification.objects.filter(kind="sleep_started").count(), 1)

    def test_rest_uses_no_worker_and_wake_sends_from_saved_cursor(self):
        user, run = self.make_user()
        self.enable(user)
        self.tick()
        self.execute()
        self.advance(3600)
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.client.send_message.await_count, 1)
        self.advance(3600)
        self.assertEqual(self.tick(), 1)
        self.execute(1)
        self.assertEqual(self.client.send_message.await_args_list[1].args[0], "@group1_1")

    def test_rest_between_connect_and_send_defers_without_advancing_or_quota(self):
        user, run = self.make_user()
        self.enable(user)
        PublishingRun.objects.filter(pk=run.pk).update(phase_started_at=self.now - timedelta(seconds=3599), phase_ends_at=self.now + timedelta(seconds=1))
        async def reach_boundary():
            self.advance(1)
        self.client.connect.side_effect = reach_boundary
        self.tick()
        self.execute()
        self.client.send_message.assert_not_awaited()
        attempt = PublishingAttempt.objects.get()
        self.assertEqual(attempt.status, "deferred")
        self.assertFalse(attempt.quota_reserved)
        run.refresh_from_db()
        self.assertEqual(run.cycle_position, 0)
        self.assertEqual(PublishingSettings.objects.get(user=user).current_group_index, 0)
        self.client.connect.side_effect = None
        self.advance(3600)
        self.tick()
        self.execute(1)
        self.assertEqual(self.client.send_message.await_args.args[0], "@group1_0")

    def test_inflight_request_completes_after_rest_begins(self):
        user, run = self.make_user()
        self.enable(user)
        self.tick()
        payload = claim_step(*self.queue[0])
        self.assertTrue(begin_send(payload["attempt_id"]))
        self.advance(3600)
        synchronize_run(run.pk)
        complete_attempt(payload["attempt_id"], "success")
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "rest")
        self.assertEqual(PublishingSettings.objects.get(user=user).current_group_index, 1)

    def test_manual_restart_and_saving_new_hours_reset_full_work_period(self):
        user, run = self.make_user()
        self.enable(user)
        self.advance(3600)
        synchronize_run(run.pk)
        self.enable(user, work=4, rest=2)
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "work")
        self.assertEqual(run.phase_ends_at, self.now + timedelta(hours=4))
        finish_run(user.telegram_user_id)
        self.advance(300)
        replacement, _ = create_run(user.telegram_user_id)
        self.assertEqual(replacement.phase_ends_at, self.now + timedelta(hours=4))

    def test_disable_rest_resumes_without_changing_saved_hours(self):
        user, run = self.make_user()
        self.enable(user, work=1, rest=4)
        self.advance(3600)
        synchronize_run(run.pk)
        config = asyncio.run(toggle_sleep(telegram_user_id=user.telegram_user_id))
        run.refresh_from_db()
        self.assertFalse(config.sleep_enabled)
        self.assertEqual(run.work_rest_phase, "off")
        self.assertIsNone(run.phase_ends_at)
        self.assertEqual(config.sleep_hours, 4)

    def test_enabling_while_stopped_waits_for_start(self):
        user, run = self.make_user()
        finish_run(user.telegram_user_id)
        self.enable(user)
        self.advance(5 * 3600)
        self.assertEqual(self.tick(), 0)
        self.assertFalse(BotNotification.objects.exists())
        replacement, _ = create_run(user.telegram_user_id)
        self.assertEqual(replacement.phase_ends_at, self.now + timedelta(hours=1))

    def test_long_outage_skips_old_transitions_without_notification_burst(self):
        user, run = self.make_user()
        self.enable(user)
        self.advance(100 * 7200 + 3600)
        synchronize_run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "rest")
        self.assertEqual(run.phase_ends_at, self.now + timedelta(hours=1))
        self.assertEqual(BotNotification.objects.count(), 1)

    def test_phase_and_rest_are_isolated_between_users(self):
        first, run = self.make_user(1)
        second, other = self.make_user(2)
        self.enable(first)
        self.advance(3600)
        self.tick()
        other.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "rest")
        self.assertEqual(other.work_rest_phase, "off")
        for index in range(len(self.queue)):
            self.execute(index)
        self.assertTrue(PublishingAttempt.objects.filter(user=second, status="success").exists())
        self.assertFalse(PublishingAttempt.objects.filter(user=first).exists())

    def test_intervals_rest_and_flood_wait_do_not_pause_elapsed_clock(self):
        user, run = self.make_user()
        self.enable(user)
        from telegram_accounts.models import TelegramAccount
        TelegramAccount.objects.filter(user=user).update(status="paused_flood_wait", paused_until=self.now + timedelta(hours=10))
        self.advance(3600)
        self.tick()
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "rest")

    def test_work_and_rest_values_save_atomically(self):
        user, run = self.make_user()
        before = PublishingSettings.objects.get(user=user)
        with self.assertRaises(ValueError):
            self.enable(user, work=5, rest=0)
        after = PublishingSettings.objects.get(user=user)
        self.assertEqual((after.work_hours, after.sleep_hours), (before.work_hours, before.sleep_hours))

    def test_flood_wait_ends_and_notifies_while_user_remains_in_rest(self):
        user, run = self.make_user()
        self.enable(user, work=1, rest=4)
        from telegram_accounts.models import TelegramAccount
        TelegramAccount.objects.filter(user=user).update(status="paused_flood_wait", paused_until=self.now + timedelta(hours=2))
        self.advance(7200)
        self.tick()
        run.refresh_from_db()
        self.assertEqual(run.work_rest_phase, "rest")
        self.assertEqual(TelegramAccount.objects.get(user=user).status, "connected")
        self.assertEqual(BotNotification.objects.filter(kind="flood_ended").count(), 1)
        self.assertEqual(PublishingAttempt.objects.count(), 0)

    def test_multiple_slow_groups_generate_one_summary_per_rolling_window(self):
        user, run = self.make_user()
        from telethon.errors import SlowModeWaitError
        self.client.send_message.side_effect = SlowModeWaitError(request=None, capture=10)
        self.tick()
        self.execute()
        self.advance(30)
        self.tick()
        self.execute(1)
        self.assertEqual(BotNotification.objects.filter(kind="slow_mode").count(), 1)
        self.advance(1801)
        self.tick()
        self.execute(2)  # close elapsed cycle
        self.advance(60)
        self.tick()
        self.execute(3)
        self.assertEqual(BotNotification.objects.filter(kind="slow_mode").count(), 2)

    def test_bot_hours_form_does_not_save_until_both_values_are_received(self):
        from bot.handlers.sleep import save_work_hours, save_sleep_hours
        user, run = self.make_user()
        before = PublishingSettings.objects.get(user=user)
        message = SimpleNamespace(from_user=SimpleNamespace(id=user.telegram_user_id), text="5", answer=AsyncMock())
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock(), clear=AsyncMock(),
                                get_data=AsyncMock(return_value={"work_hours": 5}))
        asyncio.run(save_work_hours(message, state))
        unchanged = PublishingSettings.objects.get(user=user)
        self.assertEqual(unchanged.work_hours, before.work_hours)
        state.update_data.assert_awaited_once_with(work_hours=5)
        message.text = "2"
        asyncio.run(save_sleep_hours(message, state))
        saved = PublishingSettings.objects.get(user=user)
        self.assertEqual((saved.work_hours, saved.sleep_hours, saved.sleep_enabled), (5, 2, True))
        run.refresh_from_db()
        self.assertEqual(run.phase_ends_at, self.now + timedelta(hours=5))
