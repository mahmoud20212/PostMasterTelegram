from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.groups import delete_all_groups_keyboard, group_add_methods_keyboard, group_details_keyboard, groups_keyboard
from bot.states.groups import GroupForm
from telegram_groups.services.groups import create_group, delete_all_groups, delete_group, extract_groups_from_account, list_groups, toggle_group
from telegram_accounts.services.accounts import has_connected_account
from subscriptions.services.subscriptions import get_subscription

router = Router(name="groups")


async def _can_extract_groups(telegram_user_id: int) -> bool:
    subscription = await get_subscription(telegram_user_id=telegram_user_id)
    plan = subscription.plan_definition
    return subscription.status == "active" and plan is not None and not plan.is_free


def groups_text(groups, prefix="👥 مجموعاتك المحفوظة:") -> str:
    selected = sum(group.is_selected for group in groups)
    return f"{prefix}\n\n📊 الإجمالي: {len(groups)}\n✅ المحددة للنشر: {selected}\n⏸️ غير المحددة: {len(groups) - selected}"


@router.callback_query(lambda callback: callback.data == "groups:list")
async def show_groups(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not await has_connected_account(telegram_user_id=callback.from_user.id):
        await callback.answer("أضف حساب Telegram أولاً للوصول إلى المجموعات.", show_alert=True)
        return
    groups = await list_groups(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text(groups_text(groups), reply_markup=groups_keyboard(groups))
    await state.clear()
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "groups:add")
async def show_group_add_methods(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return
    if not await has_connected_account(telegram_user_id=callback.from_user.id):
        await callback.answer("أضف حساب Telegram أولاً قبل إضافة المجموعات.", show_alert=True)
        return
    await callback.message.edit_text(
        "📥 إضافة مجموعات:\n\nاختر طريقة إضافة المجموعات لتفعيلها في النشر العام:",
        reply_markup=group_add_methods_keyboard(allow_extract=await _can_extract_groups(callback.from_user.id)),
    )
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "groups:add_manual")
async def start_add_group(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not await has_connected_account(telegram_user_id=callback.from_user.id):
        await callback.answer("أضف حساب Telegram أولاً قبل إضافة المجموعات.", show_alert=True)
        return
    await state.set_state(GroupForm.waiting_for_identifier)
    await callback.message.edit_text("✍️ أرسل اسم المجموعة أو رابط الدعوة:\n\nاضغط «رجوع» للإلغاء.", reply_markup=groups_keyboard([], include_add=False))
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "groups:extract")
async def extract_account_groups(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return
    if not await _can_extract_groups(callback.from_user.id):
        await callback.answer("استخراج المجموعات تلقائيًا متاح للباقات المدفوعة فقط.", show_alert=True)
        return
    await callback.answer("جارٍ استخراج المجموعات...")
    try:
        added, total = await extract_groups_from_account(telegram_user_id=callback.from_user.id)
    except Exception as error:
        await callback.message.edit_text(
            f"❌ تعذر استخراج المجموعات.\n\nالسبب: {error}",
            reply_markup=group_add_methods_keyboard(allow_extract=await _can_extract_groups(callback.from_user.id)),
        )
        return
    groups = await list_groups(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text(
        f"✅ اكتمل استخراج المجموعات.\n\n🔎 المجموعات المكتشفة: {total}\n➕ المجموعات المضافة: {added}\n📥 الإجمالي المحفوظ: {len(groups)}",
        reply_markup=groups_keyboard(groups),
    )


@router.callback_query(lambda callback: callback.data == "groups:delete_all")
async def confirm_delete_all_groups(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return
    groups = await list_groups(telegram_user_id=callback.from_user.id)
    if not groups:
        await callback.answer("لا توجد مجموعات لحذفها.", show_alert=True)
        return
    await callback.message.edit_text(
        f"⚠️ هل أنت متأكد من حذف جميع المجموعات؟\n\nسيتم حذف {len(groups)} مجموعة ولا يمكن التراجع عن العملية.",
        reply_markup=delete_all_groups_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "groups:delete_all_confirm")
async def delete_all_saved_groups(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return
    deleted = await delete_all_groups(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text(
        f"✅ تم حذف جميع المجموعات ({deleted}).",
        reply_markup=groups_keyboard([]),
    )
    await callback.answer()


@router.message(GroupForm.waiting_for_identifier)
async def save_new_group(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text or not message.text.strip():
        await message.answer("يرجى إرسال اسم مجموعة أو رابط صالح.")
        return
    try:
        await create_group(telegram_user_id=message.from_user.id, identifier=message.text)
    except ValueError as error:
        await message.answer(str(error), reply_markup=groups_keyboard([], include_add=False))
        return
    await state.clear()
    groups = await list_groups(telegram_user_id=message.from_user.id)
    await message.answer("✅ تمت إضافة المجموعة بنجاح.")
    await message.answer(groups_text(groups), reply_markup=groups_keyboard(groups))


@router.callback_query(lambda callback: callback.data and callback.data.startswith("groups:toggle:"))
async def toggle_saved_group(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return
    group_id = int(callback.data.rsplit(":", 1)[1])
    await toggle_group(telegram_user_id=callback.from_user.id, group_id=group_id)
    groups = await list_groups(telegram_user_id=callback.from_user.id)
    updated = next(group for group in groups if group.id == group_id)
    await callback.message.edit_text(
        f"👥 تفاصيل المجموعة:\n\n{updated.title}\n\n"
        f"حالة النشر: {'✅ مفعلة' if updated.is_selected else '⏸️ متوقفة'}",
        reply_markup=group_details_keyboard(updated),
    )
    await callback.answer("تم تحديث تحديد المجموعة.")


@router.callback_query(lambda callback: callback.data and callback.data.startswith("groups:view:"))
async def show_group_details(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    group_id = int(callback.data.rsplit(":", 1)[1])
    groups = await list_groups(telegram_user_id=callback.from_user.id)
    group = next((item for item in groups if item.id == group_id), None)
    if group is None:
        await callback.answer("المجموعة غير موجودة.", show_alert=True)
        return
    await callback.message.edit_text(
        f"👥 تفاصيل المجموعة:\n\n{group.title}\n\n"
        f"حالة النشر: {'✅ مفعلة' if group.is_selected else '⏸️ متوقفة'}\n"
        f"صلاحية النشر: {'✅ متاحة' if group.can_publish else '⏳ لم يتم التحقق'}",
        reply_markup=group_details_keyboard(group),
    )
    await callback.answer()


@router.callback_query(lambda callback: callback.data and callback.data.startswith("groups:delete:"))
async def delete_saved_group(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    group_id = int(callback.data.rsplit(":", 1)[1])
    await delete_group(telegram_user_id=callback.from_user.id, group_id=group_id)
    groups = await list_groups(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text("✅ تم حذف المجموعة.\n\n" + groups_text(groups), reply_markup=groups_keyboard(groups))
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "groups:delete_last")
async def delete_last_group(callback: CallbackQuery) -> None:
    await callback.answer("افتح المجموعة من القائمة لحذفها.")
