from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def mask_phone(phone: str) -> str:
    return phone[:4] + "x" * max(0, len(phone) - 4)


def accounts_keyboard(accounts, *, include_add: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if include_add:
        rows.append([InlineKeyboardButton(text="➕ إضافة حساب", callback_data="accounts:add")])
    for account in accounts:
        # status = "🟢" if account.status == "connected" else "🟡"
        # selected = " 🎯" if account.is_publishing_account else ""
        label = (account.phone_number or "حساب بدون رقم") # + selected
        rows.append([InlineKeyboardButton(text=f"{label}", callback_data=f"accounts:view:{account.id}")])
    rows.append([InlineKeyboardButton(text="↩️ رجوع", callback_data="menu:main" if include_add else "accounts:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def publishing_account_keyboard(accounts) -> InlineKeyboardMarkup:
    rows = []
    for account in accounts:
        if account.status != "connected":
            continue
        selected = " ✅" if account.is_publishing_account else ""
        rows.append([InlineKeyboardButton(
            text=f"{account.phone_number}{selected}",
            callback_data=f"accounts:set_publish:{account.id}",
        )])
    rows.append([InlineKeyboardButton(text="↩️ رجوع", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ أوافق، ابدأ ربط الحساب", callback_data="accounts:accept_terms")],
        [InlineKeyboardButton(text="❌ إلغاء ورجوع", callback_data="accounts:terms_cancel")],
    ])


def account_input_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ رجوع", callback_data="accounts:list")],
    ])


def account_details_keyboard(account_id: int, *, confirm_delete: bool = False) -> InlineKeyboardMarkup:
    if confirm_delete:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ نعم، احذف الحساب", callback_data=f"accounts:delete_confirm:{account_id}")],
            [InlineKeyboardButton(text="↩️ إلغاء", callback_data=f"accounts:view:{account_id}")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ حذف الحساب وإنهاء الجلسة", callback_data=f"accounts:delete:{account_id}")],
        [InlineKeyboardButton(text="↩️ رجوع", callback_data="accounts:list")],
    ])
