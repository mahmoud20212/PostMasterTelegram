from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from bot.keyboards.membership import membership_keyboard
from bot.services.membership import check_required_membership
from bot.ui.membership import JOIN_REQUIRED_TEXT, VERIFICATION_UNAVAILABLE_TEXT


class RequiredGroupMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Accepted payments must always be finalized; Stop must remain usable even
        # when Telegram cannot check membership. Neither starts new publishing.
        if isinstance(event, Message) and event.successful_payment is not None:
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "publishing:stop":
            return await handler(event, data)
        if event.from_user is None:
            return await handler(event, data)

        membership = await check_required_membership(data["bot"], event.from_user.id)
        if membership is True:
            data["required_group_verified"] = True
            return await handler(event, data)

        state = data.get("state")
        if state is not None:
            await state.clear()
        text = JOIN_REQUIRED_TEXT if membership is False else VERIFICATION_UNAVAILABLE_TEXT
        if isinstance(event, CallbackQuery):
            try:
                if event.message is not None:
                    try:
                        await event.message.edit_text(text, parse_mode="HTML", reply_markup=membership_keyboard())
                    except TelegramBadRequest as error:
                        if "message is not modified" not in str(error).lower():
                            raise
            finally:
                alert = (
                    "انضم إلى مجموعة OnePost أولًا، ثم اضغط تحقق من الانضمام."
                    if membership is False else "تعذر التحقق مؤقتًا. يرجى المحاولة بعد قليل."
                )
                await event.answer(alert, show_alert=True)
        else:
            await event.answer(text, parse_mode="HTML", reply_markup=membership_keyboard())
