from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.main import main_menu_keyboard
from bot.ui.home import home_text
from users.services.onboarding import register_telegram_user
from users.services.dashboard import get_dashboard_counts

router = Router(name="start")


async def _home_view(telegram_user):
    user = await register_telegram_user(
        telegram_user_id=telegram_user.id,
        username=telegram_user.username or "",
        first_name=telegram_user.first_name or "",
    )
    counts = await get_dashboard_counts(telegram_user_id=user.telegram_user_id)
    return home_text(user), main_menu_keyboard(is_publishing=user.is_publishing, counts=counts)


@router.message(Command("start"))
async def start_command(message: Message) -> None:
    if message.from_user is None:
        return

    text, keyboard = await _home_view(message.from_user)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "membership:verify")
async def verify_membership(callback: CallbackQuery, state: FSMContext, required_group_verified: bool = False) -> None:
    try:
        if callback.from_user is None or callback.message is None:
            return
        # The parent middleware supplies this flag after a fresh server-side check.
        if not required_group_verified:
            return
        await state.clear()
        text, keyboard = await _home_view(callback.from_user)
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
    finally:
        await callback.answer()
