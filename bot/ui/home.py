from html import escape
from math import ceil

from django.utils import timezone

from bot.ui.subscription import duration_text


def home_text(user) -> str:
    username = f"@{escape(user.username)}" if user.username else "غير متوفر"
    plan = escape(user.current_plan.title())
    subscription = getattr(user, "subscription", None)
    if subscription is not None and subscription.end_date is not None:
        days = max(0, ceil((subscription.end_date - timezone.now()).total_seconds() / 86400))
        if subscription.status != "active":
            days = 0
        remaining = duration_text(days) if days else "0 أيام"
        plan += f" | متبقي {remaining}"
    return (
        "<b>أهلاً بك في بوت النشر التلقائي الأقوى والأذكى!</b>\n\n"
        "<b>👤 معلومات حسابك:</b>\n"
        f"├ <b>🆔 الآيدي:</b> <code>{user.telegram_user_id}</code>\n"
        f"└ <b>📛 اليوزر:</b> {username}\n\n"
        f"<b>💳 الباقة الحالية:</b> {plan}\n\n"
        "« تحكم بحساباتك ومجموعاتك وإعلاناتك من خلال الأزرار التفاعلية أدناه ⚙️👇"
    )
