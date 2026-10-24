from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.keyboards.main import main_menu_keyboard
from bot.ui.home import home_text
from users.services.onboarding import register_telegram_user
from users.services.dashboard import get_dashboard_counts

router = Router(name="start")


@router.message(Command("start"))
async def start_command(message: Message) -> None:
    if message.from_user is None:
        return

    user = await register_telegram_user(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    counts = await get_dashboard_counts(telegram_user_id=user.telegram_user_id)
    await message.answer(
        home_text(user),
        reply_markup=main_menu_keyboard(is_publishing=user.is_publishing, counts=counts),
        parse_mode="HTML",
    )
