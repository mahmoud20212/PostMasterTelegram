from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def publishing_settings_keyboard(_settings=None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="menu:main")],
    ])
