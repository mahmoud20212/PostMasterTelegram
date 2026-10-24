from asgiref.sync import sync_to_async
from django.test import TestCase

from telegram_groups.models import TelegramGroup
from telegram_groups.services.groups import create_group, normalize_group_identifier
from users.models import User


class GroupValidationTests(TestCase):
    def setUp(self):
        User.objects.create(telegram_user_id=2001)

    def test_normalizes_username_and_link_to_same_value(self):
        self.assertEqual(normalize_group_identifier("@MyGroup"), "username:mygroup")
        self.assertEqual(normalize_group_identifier("https://t.me/MyGroup"), "username:mygroup")

    def test_rejects_invalid_identifier(self):
        with self.assertRaises(ValueError):
            normalize_group_identifier("not valid group")

    async def test_rejects_duplicate_group_in_different_format(self):
        await create_group(telegram_user_id=2001, identifier="@MyGroup")

        with self.assertRaises(ValueError):
            await create_group(telegram_user_id=2001, identifier="https://t.me/MyGroup")

        self.assertEqual(await sync_to_async(TelegramGroup.objects.count)(), 1)
