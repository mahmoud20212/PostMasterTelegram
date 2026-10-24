from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.sleep import sleep_input_keyboard, sleep_keyboard
from bot.states.sleep import SleepForm
from publishing.services.settings import get_settings, toggle_sleep, update_work_hours

router = Router(name="sleep")


def sleep_text(settings) -> str:
    status = "🟢 مفعل" if settings.sleep_enabled else "🔴 متوقف"
    return (
        f"🌙 وضع السكون (النوم التلقائي): {status}\n\n"
        "يساعد هذا الوضع في تقليل الضغط بشكل كبير، حيث يجعل البوت يعمل لساعات "
        "معينة مخصصة ثم يتوقف تلقائياً للراحة لإبقاء سلوك البشر الطبيعي لتجنب رصد "
        "الفلاتر الذكية للموج.\n\n"
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
    await update_work_hours(telegram_user_id=message.from_user.id, hours=hours)
    await state.set_state(SleepForm.waiting_for_sleep_hours)
    await message.answer(
        "✅ تم حفظ ساعات العمل.\n\n"
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
    settings = await get_settings(telegram_user_id=message.from_user.id)
    settings.sleep_hours = sleep_hours
    settings.sleep_enabled = True
    from asgiref.sync import sync_to_async
    await sync_to_async(settings.save)(update_fields=["sleep_hours", "sleep_enabled"])
    await state.clear()
    await message.answer(
        "✅ تم حفظ ساعات العمل والسكون.\n\n" + sleep_text(settings),
        reply_markup=sleep_keyboard(settings),
    )
