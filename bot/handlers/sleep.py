from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.sleep import sleep_input_keyboard, sleep_keyboard
from bot.states.sleep import SleepForm
from publishing.services.settings import get_settings, toggle_sleep, update_work_sleep_hours
from notifications.events import local_time

router = Router(name="sleep")


def sleep_text(settings) -> str:
    status = "🟢 مفعل" if settings.sleep_enabled else "🔴 متوقف"
    phase = getattr(settings, "phase_name", "stopped")
    phase_text = {"rest": "😴 في فترة الراحة", "work": "▶️ في فترة العمل", "off": "▶️ يعمل دون سكون تلقائي", "stopped": "⏹️ النشر متوقف"}[phase]
    until = getattr(settings, "next_phase_at", None)
    next_text = f"\n⏰ الانتقال القادم: {local_time(until, settings.timezone)}" if until else ""
    return (
        f"🌙 وضع السكون (النوم التلقائي): {status}\n\n"
        "عند التفعيل يبدأ عدّ فترة عمل كاملة، ثم راحة، وتتكرر الفترتان تلقائيًا.\n"
        "كل تشغيل يدوي أو حفظ ساعات جديدة يبدأ فترة عمل من جديد.\n\n"
        f"{phase_text}{next_text}\n\n"
        "⚙️ إعداداتك الحالية:\n"
        f"▶️ يعمل: {settings.work_hours} ساعات متواصلة.\n"
        f"⏸️ ينام: {settings.sleep_hours} ساعات متواصلة."
    )


@router.callback_query(lambda callback: callback.data == "sleep:settings")
async def show_sleep_settings(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        return
    settings = await get_settings(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text(sleep_text(settings), reply_markup=sleep_keyboard(settings))
    await state.clear()
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "sleep:toggle")
async def toggle_sleep_mode(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return
    settings = await toggle_sleep(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text(sleep_text(settings), reply_markup=sleep_keyboard(settings))
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "sleep:hours")
async def start_sleep_hours(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(SleepForm.waiting_for_work_hours)
    await callback.message.edit_text(
        "▶️ كم ساعة متواصلة تريد أن يعمل البوت؟\n\n"
        "أرسل رقماً من 1 إلى 24.\n"
        "مثال: 16",
        reply_markup=sleep_input_keyboard(),
    )
    await callback.answer()


@router.message(SleepForm.waiting_for_work_hours)
async def save_work_hours(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text or not message.text.strip().isdigit():
        await message.answer("أرسل رقماً صحيحاً من 1 إلى 24.")
        return
    hours = int(message.text.strip())
    if not 1 <= hours <= 24:
        await message.answer("ساعات العمل يجب أن تكون بين 1 و24 ساعة.")
        return
    await state.update_data(work_hours=hours)
    await state.set_state(SleepForm.waiting_for_sleep_hours)
    await message.answer(
        "✅ تم استلام ساعات العمل. تُحفظ الإعدادات معًا بعد الخطوة التالية.\n\n"
        "⏸️ كم ساعة سكون تريد؟\nأرسل رقماً من 1 إلى 24.",
        reply_markup=sleep_input_keyboard(),
    )


@router.message(SleepForm.waiting_for_sleep_hours)
async def save_sleep_hours(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text or not message.text.strip().isdigit():
        await message.answer("أرسل رقماً صحيحاً من 1 إلى 24.")
        return
    sleep_hours = int(message.text.strip())
    if not 1 <= sleep_hours <= 24:
        await message.answer("ساعات السكون يجب أن تكون بين 1 و24 ساعة.")
        return
    data = await state.get_data()
    if "work_hours" not in data:
        await state.set_state(SleepForm.waiting_for_work_hours)
        await message.answer("أرسل ساعات العمل أولًا من 1 إلى 24.")
        return
    settings = await update_work_sleep_hours(telegram_user_id=message.from_user.id,
        work_hours=data["work_hours"], sleep_hours=sleep_hours)
    await state.clear()
    await message.answer(
        "✅ تم حفظ ساعات العمل والسكون.\n\n" + sleep_text(settings),
        reply_markup=sleep_keyboard(settings),
    )
