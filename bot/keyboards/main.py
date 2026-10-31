from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


DEVELOPER_URL = "https://t.me/OneStoreHub"


def main_menu_keyboard(*, is_publishing: bool = False, counts: dict | None = None) -> InlineKeyboardMarkup:
    counts = counts or {
        "messages": 0,
        "groups": 0,
        "accounts": 0,
        "cycle_minutes": 5,
        "sleep_enabled": False,
    }
    publishing_text = "🔴 إيقاف النشر" if is_publishing else "🟢 تشغيل النشر"
    publishing_callback = "publishing:stop" if is_publishing else "publishing:start"
    sleep_text = "🌙 السكون: مفعل" if counts.get("sleep_enabled", False) else "🌙 السكون: متوقف"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=publishing_text, callback_data=publishing_callback)],
        [InlineKeyboardButton(text="🎯 تحديد حساب النشر", callback_data="accounts:select")],
        [
            InlineKeyboardButton(text=f"💬 الرسائل ({counts['messages']})", callback_data="messages:list"),
            InlineKeyboardButton(text=f"👥 المجموعات ({counts['groups']})", callback_data="groups:list"),
        ],
        [
            InlineKeyboardButton(text=f"📱 الحسابات ({counts['accounts']})", callback_data="accounts:list"),
            InlineKeyboardButton(text=f"⏱️ التوقيت ({counts['cycle_minutes']} د)", callback_data="publishing:settings"),
        ],
        [InlineKeyboardButton(text=sleep_text, callback_data="sleep:settings")],
        [
            InlineKeyboardButton(text="💎 الباقات", callback_data="subscription:details"),
            InlineKeyboardButton(text="📊 الإحصائيات", callback_data="reports:statistics"),
        ],
        [InlineKeyboardButton(text="📖 دليل الاستخدام", callback_data="guide:show")],
        [InlineKeyboardButton(text="🎧 الدعم الفني", url=DEVELOPER_URL)],
    ])


def guide_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ القائمة الرئيسية", callback_data="menu:main")],
    ])
