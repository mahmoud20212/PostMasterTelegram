from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def messages_keyboard(messages, *, include_add: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if include_add:
        rows.append([InlineKeyboardButton(text="➕ إضافة رسالة", callback_data="messages:add")])
    for message in messages:
        status = "✅" if message.is_active else "⏸️"
        rows.append([InlineKeyboardButton(text=f"{status} {message.text[:35]}", callback_data=f"messages:view:{message.id}")])
    back_callback = "messages:list" if not include_add else "menu:main"
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def message_details_keyboard(message) -> InlineKeyboardMarkup:
    toggle_text = "⏸️ تعطيل الرسالة" if message.is_active else "✅ تفعيل الرسالة"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=f"messages:toggle:{message.id}")],
        [InlineKeyboardButton(text="🗑️ حذف الرسالة", callback_data=f"messages:delete:{message.id}")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="messages:list")],
    ])
