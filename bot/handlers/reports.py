from aiogram import Router
from aiogram.types import CallbackQuery

from bot.keyboards.reports import reports_keyboard
from reports.services.statistics import get_statistics

router = Router(name="reports")


def statistics_text(stats: dict) -> str:
    sleep_status = "✅ مفعّل" if stats["sleep_enabled"] else "⏸️ متوقف"
    return (
        "📊 إحصائيات الحساب\n\n"
        f"📌 حالة النشر: {stats['publishing_status']}\n\n"
        f"💬 الرسائل: {stats['messages']}\n"
        f"✅ الرسائل المفعلة: {stats['active_messages']}\n"
        f"📥 المجموعات: {stats['groups']}\n"
        f"✅ المجموعات المحددة: {stats['selected_groups']}\n"
        f"📱 الحسابات: {stats['accounts']}\n"
        f"🟢 الحسابات المتصلة: {stats['connected_accounts']}\n\n"
        f"⏱️ مدة دورة النشر: {stats['cycle_minutes']} دقيقة\n"
        f"▶️ ساعات العمل: {stats['work_hours']} ساعة\n"
        f"😴 ساعات السكون: {stats['sleep_hours']} ساعة\n"
        f"🌙 وضع السكون: {sleep_status}"
    )


@router.callback_query(lambda callback: callback.data == "reports:statistics")
async def show_statistics(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return
    stats = await get_statistics(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text(statistics_text(stats), reply_markup=reports_keyboard())
    await callback.answer()
