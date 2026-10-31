import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage
from django.db import transaction
from django.test import TransactionTestCase, override_settings

from notifications import delivery
from notifications.events import emit, render
from notifications.models import BotNotification
from publishing import test_durable_engine as fixtures
from publishing.services.runs import finish_run
from publishing.services.settings import update_work_sleep_hours
from publishing.services.work_rest import synchronize_run
from subscriptions.models import PlanDuration
from subscriptions.services.subscriptions import activate_manual_payment, activate_paid_subscription, create_pending_payment, get_subscription


@override_settings(PUBLISHING_MAX_PENDING=4, PUBLISHING_DISPATCH_BATCH=4)
class NotificationTests(TransactionTestCase):
    setUp_fixtures = fixtures.DurablePublishingTests.setUp
    tearDown = fixtures.DurablePublishingTests.tearDown
    make_user = fixtures.DurablePublishingTests.make_user
    advance = fixtures.DurablePublishingTests.advance

    def setUp(self):
        self.setUp_fixtures()
        self.user, self.run = self.make_user()
        self.queue = []
        self.real_send = delivery._send
        self.send_patch = patch("notifications.delivery._send", new_callable=AsyncMock)
        self.send = self.send_patch.start()
        self.send.return_value = SimpleNamespace(message_id=99)
        self.addCleanup(self.send_patch.stop)

    def tick(self, publish=None):
        return delivery.dispatch_notifications(publish=publish or (lambda *args: self.queue.append(args)))

    def event(self, key="one", **kwargs):
        return emit(self.user.pk, "daily_limit", key, **kwargs)

    def execute(self, index=0):
        delivery.deliver_notification(*self.queue[index])

    def test_event_is_transactional_and_deduplicated(self):
        event = self.event()
        self.assertEqual(event.pk, self.event().pk)
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.event("rollback")
            raise RuntimeError("rollback")
        self.assertEqual(BotNotification.objects.count(), 1)

    def test_bounded_delivery_goes_only_to_owner_and_duplicate_is_noop(self):
        event = self.event()
        self.tick()
        self.execute()
        self.execute()
        self.send.assert_awaited_once()
        self.assertEqual(self.send.await_args.args[0], self.user.telegram_user_id)
        event.refresh_from_db()
        self.assertEqual(event.status, "sent")
        self.assertEqual(event.telegram_message_id, 99)

    def test_bot_adapter_sends_arabic_and_closes_session_without_telethon(self):
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)), session=SimpleNamespace(close=AsyncMock()))
        with patch("notifications.delivery.Bot", return_value=bot), patch.dict("os.environ", TELEGRAM_BOT_TOKEN="mock-only"):
            asyncio.run(self.real_send(self.user.telegram_user_id, render(self.event())))
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.kwargs["request_timeout"], 10)
        bot.session.close.assert_awaited_once()

    def test_timeout_becomes_unknown_without_resend_or_stopping_publishing(self):
        event = self.event()
        self.send.side_effect = TimeoutError()
        self.tick()
        self.execute()
        self.advance(100)
        self.assertEqual(self.tick(), 0)
        self.execute()
        self.send.assert_awaited_once()
        event.refresh_from_db()
        self.assertEqual(event.status, "unknown")
        self.run.refresh_from_db()
        self.assertTrue(self.run.active)

    def test_forbidden_is_terminal_and_another_users_notification_continues(self):
        first = self.event()
        other, _ = self.make_user(2)
        second = emit(other.pk, "daily_limit", "other")
        self.send.side_effect = TelegramForbiddenError(method=SendMessage(chat_id=1, text="mock"), message="mock blocked")
        self.tick()
        self.execute()
        self.send.side_effect = None
        self.execute(1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "failed")
        self.assertEqual(second.status, "sent")

    def test_retry_after_is_scheduled_without_worker_sleep(self):
        event = self.event()
        self.send.side_effect = TelegramRetryAfter(method=SendMessage(chat_id=1, text="mock"), message="mock rate", retry_after=60)
        self.tick()
        self.execute()
        event.refresh_from_db()
        self.assertEqual(event.status, "pending")
        self.assertEqual(event.available_at, self.now + timedelta(seconds=60))
        self.assertEqual(self.tick(), 0)
        self.advance(60)
        self.send.side_effect = None
        self.tick()
        self.execute(1)
        event.refresh_from_db()
        self.assertEqual(event.status, "sent")

    def test_failed_publication_expires_and_old_token_cannot_send(self):
        event = self.event()
        def unavailable(*args):
            self.queue.append(args)
            raise ConnectionError("mock broker")
        self.tick(unavailable)
        self.advance(31)
        self.tick()
        self.execute()
        self.send.assert_not_awaited()
        self.execute(1)
        self.send.assert_awaited_once()

    def test_worker_loss_after_sending_marker_is_unknown_and_not_retried(self):
        event = self.event()
        self.tick()
        self.assertIsNotNone(delivery.claim_notification(*self.queue[0]))
        self.advance(31)
        self.assertEqual(self.tick(), 0)
        event.refresh_from_db()
        self.assertEqual(event.status, "unknown")
        self.execute()
        self.send.assert_not_awaited()

    def enter_rest(self):
        asyncio.run(update_work_sleep_hours(telegram_user_id=self.user.telegram_user_id, work_hours=1, sleep_hours=1))
        self.advance(3600)
        synchronize_run(self.run.pk)
        return BotNotification.objects.get(kind="sleep_started")

    def test_stopped_run_cancels_sleep_notification(self):
        event = self.enter_rest()
        finish_run(self.user.telegram_user_id)
        self.tick()
        self.execute()
        event.refresh_from_db()
        self.assertEqual(event.status, "obsolete")
        self.send.assert_not_awaited()

    def test_new_phase_cancels_old_notification_and_sends_current_phase(self):
        event = self.enter_rest()
        self.advance(3600)
        synchronize_run(self.run.pk)
        self.tick()
        self.execute()
        self.tick()
        self.execute(1)
        event.refresh_from_db()
        self.assertEqual(event.status, "obsolete")
        self.assertEqual(BotNotification.objects.get(kind="sleep_ended").status, "sent")
        self.send.assert_awaited_once()

    def test_wake_during_flood_wait_does_not_claim_immediate_sending(self):
        self.enter_rest()
        from telegram_accounts.models import TelegramAccount
        TelegramAccount.objects.filter(user=self.user).update(status="paused_flood_wait", paused_until=self.now + timedelta(hours=4))
        self.advance(3600)
        synchronize_run(self.run.pk)
        event = BotNotification.objects.select_related("run__account").get(kind="sleep_ended")
        text = render(event)
        self.assertIn("ينتظر", text)

    def test_backlog_and_per_user_dispatch_are_bounded(self):
        self.event()
        self.event("second")
        for number in range(2, 7):
            user, _ = self.make_user(number)
            emit(user.pk, "daily_limit", f"user-{number}")
        self.assertEqual(self.tick(), 4)
        self.assertEqual(len(self.queue), 4)
        self.assertEqual(self.tick(), 0)

    def test_manual_subscription_activation_is_notified_once_including_duplicate_payment(self):
        duration = PlanDuration.objects.create(plan=self.plan, label="month", days=30, price=10, payment_method="manual", currency="USD")
        payment = asyncio.run(create_pending_payment(telegram_user_id=self.user.telegram_user_id, duration_id=duration.pk, payment_method="manual"))
        asyncio.run(activate_manual_payment(payment_id=payment.pk))
        asyncio.run(activate_manual_payment(payment_id=payment.pk))
        self.assertEqual(BotNotification.objects.filter(event_key=f"payment:{payment.pk}").count(), 1)
        self.assertEqual(BotNotification.objects.get().kind, "subscription_activated")
        asyncio.run(activate_paid_subscription(telegram_user_id=self.user.telegram_user_id, duration_id=duration.pk))
        self.assertEqual(BotNotification.objects.filter(kind="subscription_renewed").count(), 1)

    def test_subscription_expiry_is_notified_once(self):
        from subscriptions.models import Subscription
        Subscription.objects.filter(user=self.user).update(end_date=self.now - timedelta(seconds=1))
        asyncio.run(get_subscription(telegram_user_id=self.user.telegram_user_id))
        asyncio.run(get_subscription(telegram_user_id=self.user.telegram_user_id))
        self.assertEqual(BotNotification.objects.filter(kind="subscription_expired").count(), 1)

    def test_delivery_rechecks_elapsed_phase_without_waiting_for_scheduler(self):
        event = self.enter_rest()
        self.tick()
        self.advance(3600)
        self.execute()
        event.refresh_from_db()
        self.assertEqual(event.status, "queued")  # queued notification itself expired
        # A fresh delivery token must still not send the old phase.
        self.tick()
        self.execute(1)
        event.refresh_from_db()
        self.assertEqual(event.status, "obsolete")
        self.send.assert_not_awaited()

    def test_repeated_rate_limit_is_bounded_to_three_delivery_attempts(self):
        event = self.event()
        self.send.side_effect = TelegramRetryAfter(method=SendMessage(chat_id=1, text="mock"), message="mock", retry_after=1)
        for index in range(3):
            self.tick()
            self.execute(index)
            self.advance(1)
        event.refresh_from_db()
        self.assertEqual(event.status, "failed")
        self.assertEqual(self.tick(), 0)
