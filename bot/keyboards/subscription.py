from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 عرض الخطط", callback_data="subscription:plans")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="menu:main")],
    ])


def plans_keyboard(plans) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{plan.name}", callback_data=f"subscription:plan:{plan.id}")]
        for plan in plans if not plan.is_free
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="subscription:details")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def durations_keyboard(durations, payment_method: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{duration.label} - {int(duration.price) if duration.currency == 'XTR' else f'{duration.price:.2f}'} {'نجمة' if duration.currency == 'XTR' else '$'}",
            callback_data=f"subscription:duration:{payment_method}:{duration.id}",
        )]
        for duration in durations
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="subscription:plans")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_methods_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"subscription:method:telegram_stars:{plan_id}")],
        [InlineKeyboardButton(text="💳 دفع يدوي", callback_data=f"subscription:method:manual:{plan_id}")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="subscription:plans")],
    ])


def manual_payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="الدعم الفني", url="https://t.me/OneStoreHub")],
        [InlineKeyboardButton(text="⬅️ العودة للاشتراكات", callback_data="subscription:plans")],
    ])


def payment_confirmation_keyboard(payment_method: str, duration_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 الدفع الآن", callback_data=f"subscription:pay:{payment_method}:{duration_id}")],
        [InlineKeyboardButton(text="⬅️ تغيير المدة", callback_data=f"subscription:method-back:{payment_method}:{duration_id}")],
    ])
