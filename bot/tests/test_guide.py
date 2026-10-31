from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText
from django.test import SimpleTestCase

from bot.handlers.guide import router as guide_router, show_guide
from bot.handlers.messages import router as messages_router
from bot.handlers.router import build_router
from bot.keyboards.main import DEVELOPER_URL, main_menu_keyboard
from bot.states.messages import MessageForm


class GuideTests(SimpleTestCase):
    """No database access or external Telegram client is permitted in these tests."""

    def callback(self, data="guide:show"):
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=97001),
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()), answer=AsyncMock())

    def state(self):
        return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=97001, user_id=97001))

    def test_guide_is_above_support_in_both_publishing_states(self):
        for publishing in (False, True):
            with self.subTest(publishing=publishing):
                keyboard = main_menu_keyboard(is_publishing=publishing)
                guide, support = keyboard.inline_keyboard[-2:]
                self.assertEqual(len(guide), 1)
                self.assertEqual((guide[0].text, guide[0].callback_data), ("📖 دليل الاستخدام", "guide:show"))
                self.assertEqual(support[0].url, DEVELOPER_URL)
                actions = [button.callback_data for row in keyboard.inline_keyboard[:-2] for button in row]
                self.assertEqual(actions, ["publishing:stop" if publishing else "publishing:start",
                    "accounts:select", "messages:list", "groups:list", "accounts:list",
                    "publishing:settings", "sleep:settings", "subscription:details", "reports:statistics"])

    async def test_guide_and_back_use_same_message_and_clear_pending_input_without_account(self):
        state = self.state()
        await state.set_state(MessageForm.waiting_for_text)
        await state.update_data(draft="unsaved")
        callback = self.callback()
        user = SimpleNamespace(telegram_user_id=97001, username="", current_plan="free",
            subscription=None, is_publishing=False)
        counts = {"messages": 0, "groups": 0, "accounts": 0, "cycle_minutes": 5, "sleep_enabled": False}
        with patch("bot.handlers.messages.get_telegram_user", AsyncMock(return_value=user)) as load_user, \
             patch("bot.handlers.messages.get_dashboard_counts", AsyncMock(return_value=counts)) as load_counts:
            await guide_router.propagate_event("callback_query", callback, state=state)
            load_user.assert_not_awaited()
            load_counts.assert_not_awaited()
            self.assertIsNone(await state.get_state())
            self.assertEqual(await state.get_data(), {})
            callback.message.edit_text.assert_awaited_once()
            callback.message.answer.assert_not_awaited()
            callback.answer.assert_awaited_once_with()
            view = callback.message.edit_text.await_args
            self.assertEqual(view.kwargs["parse_mode"], "HTML")
            self.assertIn("<b>دليل الاستخدام السريع</b>", view.args[0])
            for label in ("أضف حسابك", "حدّد حساب النشر", "أضف رسائلك", "أضف المجموعات", "اضبط التوقيت", "ابدأ النشر"):
                self.assertIn(f"<b>{label}:</b>", view.args[0])
            self.assertLess(len(view.args[0]), 4096)
            back = view.kwargs["reply_markup"].inline_keyboard
            self.assertEqual(len(back), 1)
            self.assertEqual(len(back[0]), 1)
            self.assertEqual((back[0][0].text, back[0][0].callback_data), ("⬅️ القائمة الرئيسية", "menu:main"))
            self.assertIsNone(back[0][0].url)
            callback.message.edit_text.reset_mock()
            callback.answer.reset_mock()
            callback.data = back[0][0].callback_data
            await messages_router.propagate_event("callback_query", callback, state=state)
            load_user.assert_awaited_once_with(telegram_user_id=97001)
            load_counts.assert_awaited_once_with(telegram_user_id=97001)
        callback.message.edit_text.assert_awaited_once()
        callback.message.answer.assert_not_awaited()
        callback.answer.assert_awaited_once_with()
        actions = [button.callback_data for row in callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for button in row]
        self.assertIn("guide:show", actions)
        self.assertIn("publishing:start", actions)

    async def test_repeated_open_acknowledges_and_handles_not_modified(self):
        callback = self.callback()
        state = self.state()
        await show_guide(callback, state)
        callback.answer.reset_mock()
        callback.message.edit_text.side_effect = TelegramBadRequest(
            method=EditMessageText(text="same", chat_id=97001, message_id=1),
            message="Bad Request: message is not modified",
        )
        await state.set_state(MessageForm.waiting_for_text)
        await show_guide(callback, state)
        callback.answer.assert_awaited_once_with()
        callback.message.answer.assert_not_awaited()
        self.assertIsNone(await state.get_state())

    async def test_unexpected_edit_error_is_not_suppressed_and_spinner_is_cleared(self):
        callback = self.callback()
        callback.message.edit_text.side_effect = TelegramBadRequest(
            method=EditMessageText(text="same", chat_id=97001, message_id=1), message="message can't be edited",
        )
        with self.assertRaises(TelegramBadRequest):
            await show_guide(callback, self.state())
        callback.answer.assert_awaited_once_with()
        callback.message.answer.assert_not_awaited()

    async def test_missing_message_or_sender_is_acknowledged_without_editing(self):
        for field in ("message", "from_user"):
            with self.subTest(field=field):
                callback = self.callback()
                edit = callback.message.edit_text
                setattr(callback, field, None)
                state = SimpleNamespace(clear=AsyncMock())
                await show_guide(callback, state)
                callback.answer.assert_awaited_once_with()
                edit.assert_not_awaited()
                state.clear.assert_not_awaited()

    def test_guide_router_is_registered_without_mutating_shared_routers(self):
        with patch("bot.handlers.router.Router") as router_class:
            build_router()
        included = [call.args[0] for call in router_class.return_value.include_router.call_args_list]
        self.assertEqual(included.count(guide_router), 1)
