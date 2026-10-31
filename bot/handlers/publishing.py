from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.publishing import publishing_settings_keyboard
from bot.states.publishing import PublishingSettingsForm
from publishing.services.settings import update_cycle_duration
from publishing.services.engine import start_publishing, stop_publishing
from bot.keyboards.main import main_menu_keyboard
from bot.ui.home import home_text
from users.services.onboarding import get_telegram_user
from users.services.dashboard import get_dashboard_counts

router = Router(name="publishing")


async def _refresh_main_menu(callback: CallbackQuery) -> None:
    user = await get_telegram_user(telegram_user_id=callback.from_user.id)
    counts = await get_dashboard_counts(telegram_user_id=user.telegram_user_id)
    await callback.message.edit_text(
        home_text(user),
        reply_markup=main_menu_keyboard(is_publishing=user.is_publishing, counts=counts),
        parse_mode="HTML",
    )


@router.callback_query(lambda callback: callback.data == "publishing:start")
async def start_publishing_handler(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return
    started, text = await start_publishing(callback.from_user.id)
    await callback.answer(text, show_alert=not started)
    if started:
        await _refresh_main_menu(callback)


@router.callback_query(lambda callback: callback.data == "publishing:stop")
async def stop_publishing_handler(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        return
    text = await stop_publishing(callback.from_user.id)
    await callback.answer(text, show_alert=text != "تم إيقاف النشر.")
    await _refresh_main_menu(callback)


@router.callback_query(lambda callback: callback.data == "publishing:settings")
async def show_publishing_settings(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(PublishingSettingsForm.waiting_for_cycle_duration)
    await callback.message.edit_text(
        "⏱️ أرسل وقت الفاصل الزمني (دورة النشر) بالدقائق للنشر العام:\n\n"
        "مثال: أرسل 10 ليقوم البوت ببدء دورة النشر كل 10 دقائق.\n\n"
        "الحد الأدنى: 5 دقائق.",
        reply_markup=publishing_settings_keyboard(),
    )
    await callback.answer()


@router.message(PublishingSettingsForm.waiting_for_cycle_duration)
async def save_cycle_duration(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text or not message.text.strip().isdigit():
        await message.answer("⚠️ أرسل عدد دقائق صحيحاً، والحد الأدنى 5 دقائق.")
        return
    minutes = int(message.text.strip())
    if minutes < 5:
        await message.answer("⚠️ الحد الأدنى لمدة الدورة هو 5 دقائق.")
        return
    settings = await update_cycle_duration(telegram_user_id=message.from_user.id, minutes=minutes)
    await state.clear()
    await message.answer(f"✅ تم حفظ مدة دورة النشر: {settings.cycle_duration_minutes} دقائق.")
