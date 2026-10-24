def home_text(user) -> str:
    username = f"@{user.username}" if user.username else "غير متوفر"
    return (
        "<b>🌟 أهلاً بك في بوت النشر التلقائي الأقوى والأذكى!</b>\n"
        "<b>👤 معلومات حسابك:</b>\n"
        f"├ <b>🆔 الآيدي:</b> <code>{user.telegram_user_id}</code>\n"
        f"└ <b>📛 اليوزر:</b> {username}\n\n"
        f"<b>💳 الباقة الحالية:</b> {user.current_plan.title()}\n\n"
        "« تحكم بحساباتك ومجموعاتك وإعلاناتك من خلال الأزرار التفاعلية أدناه ⚙️👇"
    )
