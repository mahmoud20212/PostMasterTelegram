from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import EditMessageText, GetChatMember
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async
from django.test import SimpleTestCase, TestCase, override_settings

from bot.handlers.router import build_router
from bot.handlers.start import start_command, verify_membership
from bot.keyboards.main import main_menu_keyboard
from bot.middlewares.membership import RequiredGroupMiddleware
from bot.services.membership import check_required_membership
from bot.ui.membership import JOIN_REQUIRED_TEXT, VERIFICATION_UNAVAILABLE_TEXT
from users.models import User


def telegram_user():
    return SimpleNamespace(id=98001, username="member_test", first_name="Test")


def message():
    event = AsyncMock(spec=Message)
    event.from_user = telegram_user()
    event.text = "/start"
    event.successful_payment = None
    event.answer = AsyncMock()
    return event


def callback(data="membership:verify"):
    event = AsyncMock(spec=CallbackQuery)
    event.from_user = telegram_user()
    event.data = data
    event.message = SimpleNamespace(edit_text=AsyncMock(), from_user=SimpleNamespace(id=99999))
    event.answer = AsyncMock()
    return event


def context(status="member", **member_fields):
    return {
        "bot": SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status, **member_fields))),
        "state": SimpleNamespace(clear=AsyncMock()),
    }


class MembershipCheckTests(SimpleTestCase):
    async def test_server_membership_statuses_include_admins_and_restricted_members(self):
        for status, fields, expected in (
            ("creator", {}, True), ("administrator", {}, True), ("member", {}, True),
            ("restricted", {"is_member": True}, True), ("restricted", {"is_member": False}, False),
            ("left", {}, False), ("kicked", {}, False),
        ):
            with self.subTest(status=status, fields=fields):
                data = context(status, **fields)
                self.assertIs(await check_required_membership(data["bot"], 98001), expected)
                data["bot"].get_chat_member.assert_awaited_once_with(
                    chat_id="@onepost_group", user_id=98001, request_timeout=5)

    async def test_api_failures_and_timeout_are_distinct_from_non_membership(self):
        method = GetChatMember(chat_id="@onepost_group", user_id=98001)
        errors = [TelegramBadRequest(method, "chat not found"), TelegramForbiddenError(method, "not an administrator"),
            TelegramNetworkError(method, "mock network error"), TelegramRetryAfter(method, "mock limit", retry_after=10),
            TimeoutError(), OSError()]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                data = context()
                data["bot"].get_chat_member.side_effect = error
                with self.assertLogs("bot.services.membership", level="WARNING"):
                    self.assertIsNone(await check_required_membership(data["bot"], 98001))

    @override_settings(ONEPOST_REQUIRED_GROUP_CHAT_ID="-1001234567890")
    async def test_numeric_group_configuration_is_supported(self):
        data = context()
        self.assertTrue(await check_required_membership(data["bot"], 98001))
        data["bot"].get_chat_member.assert_awaited_once_with(chat_id=-1001234567890, user_id=98001, request_timeout=5)


class MembershipGateTests(SimpleTestCase):
    async def test_non_member_start_shows_join_and_verify_without_executing_handler(self):
        event, data, handler = message(), context("left"), AsyncMock()
        await RequiredGroupMiddleware()(handler, event, data)
        handler.assert_not_awaited()
        data["state"].clear.assert_awaited_once_with()
        view = event.answer.await_args
        self.assertEqual((view.args[0], view.kwargs["parse_mode"]), (JOIN_REQUIRED_TEXT, "HTML"))
        join, verify = [row[0] for row in view.kwargs["reply_markup"].inline_keyboard]
        self.assertEqual(join.url, "https://t.me/onepost_group")
        self.assertIsNone(join.callback_data)
        self.assertEqual(verify.callback_data, "membership:verify")

    async def test_old_buttons_and_fsm_text_cannot_bypass_membership(self):
        for event in (callback("menu:main"), callback("publishing:start"), callback("subscription:pay:1:7:1"), message()):
            with self.subTest(event=event):
                data, handler = context("left"), AsyncMock()
                await RequiredGroupMiddleware()(handler, event, data)
                handler.assert_not_awaited()
                data["bot"].get_chat_member.assert_awaited_once_with(
                    chat_id="@onepost_group", user_id=98001, request_timeout=5)
                if isinstance(event, CallbackQuery):
                    self.assertTrue(event.answer.await_args.kwargs["show_alert"])
                    self.assertEqual(event.message.edit_text.await_args.args[0], JOIN_REQUIRED_TEXT)

    async def test_membership_is_rechecked_when_user_leaves(self):
        data, handler = context(), AsyncMock()
        gate = RequiredGroupMiddleware()
        await gate(handler, callback("menu:main"), data)
        self.assertTrue(handler.await_args.args[1]["required_group_verified"])
        handler.reset_mock()
        data = context("left")
        await gate(handler, callback("menu:main"), data)
        handler.assert_not_awaited()

    async def test_verification_success_opens_home_and_repeated_click_is_safe(self):
        data, event = context(), callback()

        async def verified_handler(event, data):
            await verify_membership(event, data["state"], required_group_verified=data["required_group_verified"])

        with patch("bot.handlers.start._home_view", AsyncMock(return_value=("الرئيسية", main_menu_keyboard()))) as home:
            await RequiredGroupMiddleware()(verified_handler, event, data)
            home.assert_awaited_once_with(event.from_user)
            self.assertEqual(event.message.edit_text.await_args.args[0], "الرئيسية")
            event.answer.assert_awaited_once_with()
            event.answer.reset_mock()
            event.message.edit_text.side_effect = TelegramBadRequest(
                EditMessageText(text="same", chat_id=1, message_id=1), "message is not modified")
            await RequiredGroupMiddleware()(verified_handler, event, context())
            event.answer.assert_awaited_once_with()

    async def test_verify_without_server_check_never_opens_home(self):
        with patch("bot.handlers.start._home_view", AsyncMock()) as home:
            event = callback()
            await verify_membership(event, context()["state"])
        home.assert_not_awaited()
        event.answer.assert_awaited_once_with()

    async def test_repeated_non_member_verify_clears_spinner_on_unchanged_message(self):
        event = callback()
        event.message.edit_text.side_effect = TelegramBadRequest(
            EditMessageText(text="same", chat_id=1, message_id=1), "message is not modified")
        handler = AsyncMock()
        await RequiredGroupMiddleware()(handler, event, context("left"))
        handler.assert_not_awaited()
        event.answer.assert_awaited_once()

    async def test_unavailable_check_blocks_access_with_retry_message(self):
        event, handler = callback(), AsyncMock()
        data = context()
        data["bot"].get_chat_member.side_effect = TimeoutError()
        with self.assertLogs("bot.services.membership", level="WARNING"):
            await RequiredGroupMiddleware()(handler, event, data)
        handler.assert_not_awaited()
        self.assertEqual(event.message.edit_text.await_args.args[0], VERIFICATION_UNAVAILABLE_TEXT)
        event.answer.assert_awaited_once()

    async def test_stop_and_already_successful_payments_are_never_blocked(self):
        payment = message()
        payment.successful_payment = SimpleNamespace(invoice_payload="existing-payment")
        for event in (callback("publishing:stop"), payment):
            with self.subTest(event=event):
                data, handler = context("left"), AsyncMock()
                await RequiredGroupMiddleware()(handler, event, data)
                handler.assert_awaited_once_with(event, data)
                data["bot"].get_chat_member.assert_not_awaited()

    async def test_parent_inner_middleware_covers_child_handlers_without_checking_unmatched_chatter(self):
        parent, child = Router(), Router()
        parent.message.middleware(RequiredGroupMiddleware())
        parent.callback_query.middleware(RequiredGroupMiddleware())
        handler = AsyncMock()

        async def matched(event, **data):
            return await handler(event, data)

        child.message.register(matched, F.text == "/start")
        child.callback_query.register(matched, F.data == "menu:main")
        parent.include_router(child)
        data = context("left")
        await parent.propagate_event("message", message(), **data)
        await parent.propagate_event("callback_query", callback("menu:main"), **data)
        handler.assert_not_awaited()
        data["bot"].get_chat_member.reset_mock()
        event = message()
        event.text = "unmatched group chatter"
        await parent.propagate_event("message", event, **data)
        data["bot"].get_chat_member.assert_not_awaited()
        await parent.propagate_event("message", message(), **context())
        handler.assert_awaited_once()

    def test_gate_is_installed_on_both_event_types_in_application_router(self):
        with patch("bot.handlers.router.Router") as router_class:
            build_router()
        router = router_class.return_value
        middleware = router.message.middleware.call_args.args[0]
        self.assertIsInstance(middleware, RequiredGroupMiddleware)
        router.callback_query.middleware.assert_called_once_with(middleware)


class MembershipOnboardingTests(TestCase):
    async def test_new_user_registers_only_after_join_and_verified_callback(self):
        gate = RequiredGroupMiddleware()

        async def start(event, data):
            await start_command(event)

        await gate(start, message(), context("left"))
        self.assertFalse(await sync_to_async(User.objects.filter(telegram_user_id=98001).exists)())

        async def verify(event, data):
            await verify_membership(event, data["state"], data["required_group_verified"])

        event = callback()
        await gate(verify, event, context())
        self.assertEqual(await sync_to_async(User.objects.filter(telegram_user_id=98001).count)(), 1)
        actions = [button.callback_data for row in event.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for button in row]
        self.assertIn("publishing:start", actions)
        await gate(verify, callback(), context())
        self.assertEqual(await sync_to_async(User.objects.filter(telegram_user_id=98001).count)(), 1)

    async def test_existing_non_member_is_blocked_without_altering_saved_publishing_state(self):
        user = await sync_to_async(User.objects.create)(telegram_user_id=98001, current_plan="pro", is_publishing=True)
        handler = AsyncMock()
        await RequiredGroupMiddleware()(handler, callback("menu:main"), context("left"))
        handler.assert_not_awaited()
        await sync_to_async(user.refresh_from_db)()
        self.assertTrue(user.is_publishing)
        self.assertEqual(user.current_plan, "pro")
