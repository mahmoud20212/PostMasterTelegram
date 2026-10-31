from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.conf import settings


def membership_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 الانضمام للمجموعة", url=settings.ONEPOST_REQUIRED_GROUP_URL)],
        [InlineKeyboardButton(text="✅ تحقق من الانضمام", callback_data="membership:verify")],
    ])
