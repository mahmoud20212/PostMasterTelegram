from asgiref.sync import sync_to_async
from django.test import TestCase

from messages.models import SavedMessage
from messages.services.messages import create_message, delete_message, list_messages, toggle_message
from users.models import User


class SavedMessageTests(TestCase):
    def setUp(self):
        User.objects.create(telegram_user_id=1001, current_plan="free")

    async def test_creates_and_lists_message_for_owner(self):
        await create_message(telegram_user_id=1001, text="  Hello group  ")

        items = await list_messages(telegram_user_id=1001)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].text, "Hello group")
        self.assertTrue(items[0].is_active)

    async def test_toggles_message_status(self):
        item = await create_message(telegram_user_id=1001, text="Hello")

        updated = await toggle_message(telegram_user_id=1001, message_id=item.id)

        self.assertFalse(updated.is_active)
        saved = await sync_to_async(SavedMessage.objects.get)(id=item.id)
        self.assertFalse(saved.is_active)

    async def test_preserves_text_offsets_when_entities_are_saved(self):
        item = await create_message(
            telegram_user_id=1001,
            text="  🔥 عرض  ",
            entities=[{"type": "custom_emoji", "offset": 2, "length": 2, "custom_emoji_id": "99"}],
        )

        self.assertEqual(item.text, "  🔥 عرض  ")
        self.assertEqual(item.entities[0]["custom_emoji_id"], "99")

    async def test_deletes_only_the_owner_message(self):
        item = await create_message(telegram_user_id=1001, text="Delete me")

        await delete_message(telegram_user_id=1001, message_id=item.id)

        self.assertFalse(await sync_to_async(SavedMessage.objects.filter(id=item.id).exists)())
