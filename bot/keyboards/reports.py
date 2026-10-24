from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 تحديث الإحصائيات", callback_data="reports:statistics")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="menu:main")],
    ])
