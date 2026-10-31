from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase
from django.utils import timezone

from bot.handlers.publishing import _refresh_main_menu
from bot.handlers.start import start_command
from bot.ui.home import home_text
from subscriptions.models import Subscription, SubscriptionPlan
from users.models import User
from users.services.onboarding import get_telegram_user, register_telegram_user


class HomeSubscriptionTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = User.objects.create(telegram_user_id=94001, username="tester", current_plan="pro")
        self.plan = SubscriptionPlan.objects.create(code="home-pro", name="Pro")
        self.subscription = Subscription.objects.create(user=self.user, plan="pro", plan_definition=self.plan,
            status="active", end_date=self.now + timedelta(days=6, hours=4))

    async def test_loaded_user_renders_remaining_days_without_async_database_queries(self):
        user = await get_telegram_user(telegram_user_id=self.user.telegram_user_id)
        with patch("bot.ui.home.timezone.now", return_value=self.now):
            text = home_text(user)
        self.assertIn("<b>💳 الباقة الحالية:</b> Pro | متبقي 7 أيام", text)
        self.assertIn("@tester", text)

    async def test_registration_also_preloads_subscription_for_start(self):
        user = await register_telegram_user(telegram_user_id=self.user.telegram_user_id, username="tester")
        with patch("bot.ui.home.timezone.now", return_value=self.now):
            self.assertIn("Pro | متبقي 7 أيام", home_text(user))

    def test_partial_last_day_expiry_and_inactive_subscription(self):
        for remaining, status, expected in (
            (timedelta(hours=1), "active", "متبقي يوم واحد"),
            (timedelta(days=2), "active", "متبقي يومان"),
            (timedelta(days=30), "active", "متبقي 30 يومًا"),
            (timedelta(0), "active", "متبقي 0 أيام"),
            (timedelta(days=-2), "active", "متبقي 0 أيام"),
            (timedelta(days=7), "canceled", "متبقي 0 أيام"),
        ):
            with self.subTest(remaining=remaining, status=status):
                self.subscription.end_date = self.now + remaining
                self.subscription.status = status
                self.user.subscription = self.subscription
                with patch("bot.ui.home.timezone.now", return_value=self.now), self.assertNumQueries(0):
                    text = home_text(self.user)
                self.assertIn(expected, text)

    async def test_new_free_user_without_subscription_does_not_invent_expiry(self):
        user = await register_telegram_user(telegram_user_id=94002)
        self.assertIn("الباقة الحالية:</b> Free", home_text(user))
        self.assertNotIn("متبقي", home_text(user))

    def test_subscription_without_expiry_and_html_escaping(self):
        self.subscription.end_date = None
        self.user.subscription = self.subscription
        self.user.current_plan = "pro <vip>"
        self.user.username = "name<unsafe>"
        with self.assertNumQueries(0):
            text = home_text(self.user)
        self.assertNotIn("متبقي", text)
        self.assertIn("Pro &lt;Vip&gt;", text)
        self.assertIn("@name&lt;unsafe&gt;", text)

    async def test_start_and_publishing_refresh_show_days_with_mocked_telegram(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=self.user.telegram_user_id,
            username="tester", first_name="Test"), answer=AsyncMock())
        callback = SimpleNamespace(from_user=message.from_user, message=SimpleNamespace(edit_text=AsyncMock()))
        with patch("bot.ui.home.timezone.now", return_value=self.now):
            await start_command(message)
            await _refresh_main_menu(callback)
        for call in (message.answer.await_args, callback.message.edit_text.await_args):
            self.assertIn("Pro | متبقي 7 أيام", call.args[0])
            self.assertEqual(call.kwargs["parse_mode"], "HTML")
