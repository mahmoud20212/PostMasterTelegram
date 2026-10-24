from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def sleep_keyboard(settings) -> InlineKeyboardMarkup:
    toggle_text = "🔴 إيقاف وضع السكون والراحة" if settings.sleep_enabled else "🟢 تفعيل وضع السكون والراحة"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ ضبط وإعداد الساعات", callback_data="sleep:hours")],
        [InlineKeyboardButton(text=toggle_text, callback_data="sleep:toggle")],
        [InlineKeyboardButton(text="↩️ رجوع", callback_data="menu:main")],
    ])


def sleep_input_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ رجوع", callback_data="sleep:settings")],
    ])
