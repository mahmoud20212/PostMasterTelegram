from django.test import TestCase
from asgiref.sync import sync_to_async

from users.models import User
from users.services.onboarding import register_telegram_user


class OnboardingTests(TestCase):
    async def test_registers_new_user_on_free_plan(self):
        user = await register_telegram_user(
            telegram_user_id=12345,
            username="tester",
            first_name="Test",
        )

        self.assertEqual(await sync_to_async(User.objects.count)(), 1)
        self.assertEqual(user.current_plan, "free")
        self.assertEqual(user.status, "active")

    async def test_start_registration_is_idempotent(self):
        await register_telegram_user(telegram_user_id=12345, username="old")
        await register_telegram_user(telegram_user_id=12345, username="new")

        self.assertEqual(await sync_to_async(User.objects.count)(), 1)
        user = await sync_to_async(User.objects.get)()
        self.assertEqual(user.username, "new")
