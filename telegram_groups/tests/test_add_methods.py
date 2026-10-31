from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase
from django.utils import timezone

from bot.handlers.groups import extract_account_groups, show_group_add_methods
from subscriptions.models import Subscription, SubscriptionPlan
from users.models import User


class GroupAddMethodsTests(TestCase):
    def setUp(self):
        free, _ = SubscriptionPlan.objects.get_or_create(code="free", defaults={"name": "Free", "is_free": True})
        paid = SubscriptionPlan.objects.create(code="custom-paid", name="Paid", is_free=False)
        self.free_user = User.objects.create(telegram_user_id=95001, current_plan="pro")
        self.paid_user = User.objects.create(telegram_user_id=95002)
        self.expired_user = User.objects.create(telegram_user_id=95003)
        Subscription.objects.create(user=self.free_user, plan_definition=free)
        Subscription.objects.create(user=self.paid_user, plan_definition=paid,
            end_date=timezone.now() + timedelta(days=7))
        Subscription.objects.create(user=self.expired_user, plan_definition=paid,
            end_date=timezone.now() - timedelta(days=1))

    def callback(self, user):
        return SimpleNamespace(from_user=SimpleNamespace(id=user.telegram_user_id),
            message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())

    def actions(self, callback):
        keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
        return [button.callback_data for row in keyboard.inline_keyboard for button in row]

    async def test_free_plan_hides_extraction_but_keeps_manual_addition_and_back(self):
        callback = self.callback(self.free_user)
        with patch("bot.handlers.groups.has_connected_account", AsyncMock(return_value=True)):
            await show_group_add_methods(callback)
        self.assertEqual(self.actions(callback), ["groups:add_manual", "groups:list"])
        callback.answer.assert_awaited_once_with()

    async def test_paid_plan_still_shows_extraction(self):
        callback = self.callback(self.paid_user)
        with patch("bot.handlers.groups.has_connected_account", AsyncMock(return_value=True)):
            await show_group_add_methods(callback)
        self.assertEqual(self.actions(callback), ["groups:add_manual", "groups:extract", "groups:list"])

    async def test_free_and_expired_users_cannot_use_old_extraction_button(self):
        with patch("bot.handlers.groups.extract_groups_from_account", AsyncMock()) as extract:
            for user in (self.free_user, self.expired_user):
                with self.subTest(user=user.telegram_user_id):
                    callback = self.callback(user)
                    await extract_account_groups(callback)
                    callback.answer.assert_awaited_once_with(
                        "استخراج المجموعات تلقائيًا متاح للباقات المدفوعة فقط.", show_alert=True)
                    callback.message.edit_text.assert_not_awaited()
            extract.assert_not_awaited()

    async def test_expired_subscription_does_not_show_extraction(self):
        callback = self.callback(self.expired_user)
        with patch("bot.handlers.groups.has_connected_account", AsyncMock(return_value=True)):
            await show_group_add_methods(callback)
        self.assertNotIn("groups:extract", self.actions(callback))

    async def test_paid_extraction_continues_and_error_screen_keeps_the_button(self):
        for error in (None, RuntimeError("mock extraction error")):
            with self.subTest(error=error):
                callback = self.callback(self.paid_user)
                with patch("bot.handlers.groups.extract_groups_from_account",
                        AsyncMock(return_value=(0, 0), side_effect=error)) as extract, \
                     patch("bot.handlers.groups.list_groups", AsyncMock(return_value=[])):
                    await extract_account_groups(callback)
                extract.assert_awaited_once_with(telegram_user_id=self.paid_user.telegram_user_id)
                callback.answer.assert_awaited_once_with("جارٍ استخراج المجموعات...")
                if error:
                    self.assertIn("groups:extract", self.actions(callback))
                else:
                    self.assertIn("اكتمل استخراج المجموعات", callback.message.edit_text.await_args.args[0])
