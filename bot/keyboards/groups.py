from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def groups_keyboard(groups, *, include_add: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if include_add:
        rows.append([InlineKeyboardButton(text="➕ إضافة مجموعة", callback_data="groups:add")])
        if groups:
            rows.append([InlineKeyboardButton(text="🗑️ حذف جميع المجموعات", callback_data="groups:delete_all")])
    for group in groups:
        status = "✅" if group.is_selected else "⏸️"
        rows.append([InlineKeyboardButton(text=f"{status} {group.title[:35]}", callback_data=f"groups:view:{group.id}")])
    rows.append([InlineKeyboardButton(text="↩️ رجوع", callback_data="menu:main" if include_add else "groups:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_add_methods_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖊️ إضافة يدوية", callback_data="groups:add_manual")],
        [InlineKeyboardButton(text="🔍 استخراج من رقمي تلقائياً", callback_data="groups:extract")],
        [InlineKeyboardButton(text="↩️ رجوع", callback_data="groups:list")],
    ])


def delete_all_groups_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ نعم، احذف الجميع", callback_data="groups:delete_all_confirm")],
        [InlineKeyboardButton(text="↩️ إلغاء", callback_data="groups:list")],
    ])


def group_details_keyboard(group) -> InlineKeyboardMarkup:
    toggle_text = "⏸️ إيقاف النشر لهذه المجموعة" if group.is_selected else "▶️ تشغيل النشر لهذه المجموعة"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=f"groups:toggle:{group.id}")],
        [InlineKeyboardButton(text="🗑️ حذف المجموعة", callback_data=f"groups:delete:{group.id}")],
        [InlineKeyboardButton(text="↩️ رجوع", callback_data="groups:list")],
    ])
