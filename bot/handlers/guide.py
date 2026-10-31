from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.keyboards.main import guide_keyboard
from bot.ui.guide import guide_text

router = Router(name="guide")


@router.callback_query(F.data == "guide:show")
async def show_guide(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        if callback.from_user is None or callback.message is None:
            return
        await state.clear()
        try:
            await callback.message.edit_text(guide_text(), parse_mode="HTML", reply_markup=guide_keyboard())
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
    finally:
        await callback.answer()
