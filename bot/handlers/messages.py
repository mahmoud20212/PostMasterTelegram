from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.messages import message_details_keyboard, messages_keyboard
from bot.keyboards.main import main_menu_keyboard
from bot.states.messages import MessageForm
from bot.ui.home import home_text
from messages.services.messages import create_message, delete_message, get_message, list_messages, toggle_message
from telegram_accounts.services.accounts import has_connected_account
from users.services.onboarding import get_telegram_user
from users.services.dashboard import get_dashboard_counts

router = Router(name="messages")


def serialize_message_entities(message: Message) -> list[dict]:
    entities = []
    for entity in message.entities or []:
        data = {
            "type": entity.type,
            "offset": entity.offset,
            "length": entity.length,
        }
        for field in ("url", "language", "custom_emoji_id"):
            value = getattr(entity, field, None)
            if value is not None:
                data[field] = value
        entities.append(data)
    return entities


@router.callback_query(lambda callback: callback.data == "menu:main")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        return
    user = await get_telegram_user(telegram_user_id=callback.from_user.id)
    counts = await get_dashboard_counts(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text(
        home_text(user),
        reply_markup=main_menu_keyboard(is_publishing=user.is_publishing, counts=counts),
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "messages:list")
async def show_messages(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not await has_connected_account(telegram_user_id=callback.from_user.id):
        await callback.answer("أضف حساب Telegram أولاً للوصول إلى الرسائل.", show_alert=True)
        return
    items = await list_messages(telegram_user_id=callback.from_user.id)
    active_count = sum(item.is_active for item in items)
    inactive_count = len(items) - active_count
    text = (
        "📝 رسائلك المحفوظة:\n\n"
        f"📊 إجمالي الرسائل: {len(items)}\n"
        f"✅ الرسائل المفعلة: {active_count}\n"
        f"⏸️ الرسائل المتوقفة: {inactive_count}\n"
    ) if items else "📝 لا توجد رسائل محفوظة بعد.\n\n📊 إجمالي الرسائل: 0"
    await callback.message.edit_text(text, reply_markup=messages_keyboard(items))
    await state.clear()
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "messages:add")
async def start_add_message(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not await has_connected_account(telegram_user_id=callback.from_user.id):
        await callback.answer("أضف حساب Telegram أولاً قبل إضافة الرسائل.", show_alert=True)
        return
    await state.set_state(MessageForm.waiting_for_text)
    await callback.message.edit_text(
        "✍️ أرسل نص الرسالة التي تريد حفظها:\n\n"
        "اضغط «رجوع» لإلغاء العملية.",
        reply_markup=messages_keyboard([], include_add=False),
    )
    await callback.answer()


@router.message(MessageForm.waiting_for_text)
async def save_new_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text or not message.text.strip():
        await message.answer("يرجى إرسال نص صالح للرسالة.")
        return
    await create_message(
        telegram_user_id=message.from_user.id,
        text=message.text,
        entities=serialize_message_entities(message),
    )
    await state.clear()
    await message.answer("✅ تمت إضافة الرسالة بنجاح.")
    items = await list_messages(telegram_user_id=message.from_user.id)
    await message.answer("📝 رسائلك المحفوظة:", reply_markup=messages_keyboard(items))


@router.callback_query(lambda callback: callback.data and callback.data.startswith("messages:toggle:"))
async def toggle_saved_message(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    message_id = int(callback.data.rsplit(":", 1)[1])
    item = await toggle_message(telegram_user_id=callback.from_user.id, message_id=message_id)
    await callback.message.edit_text(
        f"📝 تفاصيل الرسالة:\n\n{item.text}\n\n"
        f"الحالة: {'✅ مفعلة' if item.is_active else '⏸️ متوقفة'}",
        reply_markup=message_details_keyboard(item),
    )
    await callback.answer("تم تفعيل الرسالة." if item.is_active else "تم تعطيل الرسالة.")


@router.callback_query(lambda callback: callback.data and callback.data.startswith("messages:view:"))
async def show_message_details(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return
    message_id = int(callback.data.rsplit(":", 1)[1])
    item = await get_message(telegram_user_id=callback.from_user.id, message_id=message_id)
    await callback.message.edit_text(
        f"📝 تفاصيل الرسالة:\n\n{item.text}\n\n"
        f"الحالة: {'✅ مفعلة' if item.is_active else '⏸️ متوقفة'}",
        reply_markup=message_details_keyboard(item),
    )
    await callback.answer()


@router.callback_query(lambda callback: callback.data and callback.data.startswith("messages:delete:"))
async def delete_saved_message(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return
    message_id = int(callback.data.rsplit(":", 1)[1])
    await delete_message(telegram_user_id=callback.from_user.id, message_id=message_id)
    items = await list_messages(telegram_user_id=callback.from_user.id)
    active_count = sum(item.is_active for item in items)
    inactive_count = len(items) - active_count
    await callback.message.edit_text(
        "✅ تم حذف الرسالة.\n\n"
        f"📊 إجمالي الرسائل: {len(items)}\n"
        f"✅ الرسائل المفعلة: {active_count}\n"
        f"⏸️ الرسائل المتوقفة: {inactive_count}",
        reply_markup=messages_keyboard(items),
    )
    await callback.answer()
