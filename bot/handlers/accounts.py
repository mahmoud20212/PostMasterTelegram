import re

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.accounts import account_details_keyboard, account_input_keyboard, account_terms_keyboard, accounts_keyboard, publishing_account_keyboard
from bot.keyboards.main import main_menu_keyboard
from bot.states.accounts import AccountForm
from bot.ui.home import home_text
from telegram_accounts.services.accounts import complete_login, complete_password_login, create_pending_account, list_accounts, select_publishing_account, start_login, terminate_account_session
from users.services.dashboard import get_dashboard_counts
from users.services.onboarding import get_telegram_user

router = Router(name="accounts")


def mask_phone(phone: str) -> str:
    return phone[:4] + "x" * max(0, len(phone) - 4)


def accounts_text(accounts, prefix="📱 حسابات Telegram المرتبطة:") -> str:
    if not accounts:
        return "📱 لا يوجد حساب Telegram مرتبط بعد."
    return prefix + "\n\n" + "\n".join(
        f"{'🟢 متصل' if account.status == 'connected' else '🟡 بانتظار التحقق'} — "
        f"{account.phone_number}"
        for account in accounts
    )


@router.callback_query(lambda callback: callback.data == "accounts:list")
async def show_accounts(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        return
    accounts = await list_accounts(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text(accounts_text(accounts), reply_markup=accounts_keyboard(accounts))
    await state.clear()
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "accounts:select")
async def show_publishing_account_selection(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.from_user is None:
        return
    accounts = await list_accounts(telegram_user_id=callback.from_user.id)
    connected = [account for account in accounts if account.status == "connected"]
    if not connected:
        await callback.answer("أضف حساب Telegram متصلاً أولاً.", show_alert=True)
        return
    await callback.message.edit_text(
        "🎯 اختر الحساب الذي سيستخدمه البوت للنشر:",
        reply_markup=publishing_account_keyboard(connected),
    )
    await state.clear()
    await callback.answer()


@router.callback_query(lambda callback: callback.data and callback.data.startswith("accounts:set_publish:"))
async def set_publishing_account_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.from_user is None or callback.data is None:
        return
    account_id = int(callback.data.rsplit(":", 1)[1])
    try:
        account = await select_publishing_account(
            telegram_user_id=callback.from_user.id,
            account_id=account_id,
        )
    except Exception:
        await callback.answer("الحساب غير موجود أو غير متصل.", show_alert=True)
        return
    await callback.message.edit_text(
        f"✅ تم تحديد حساب النشر:\n\n📱 {account.phone_number}",
        reply_markup=publishing_account_keyboard(await list_accounts(telegram_user_id=callback.from_user.id)),
    )
    await state.clear()
    await callback.answer("تم تحديث حساب النشر.")


@router.callback_query(lambda callback: callback.data == "accounts:add")
async def show_account_terms(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await callback.message.edit_text(
        "📜 شروط وإرشادات عامة قبل ربط حسابك:\n\n"
        "حفاظاً على أمان حسابك واستقرار إعلاناتك، يرجى قراءة التعليمات:\n"
        "1️⃣ التحقق بخطوتين (2FA): يُنصح بتفعيله لحماية حسابك.\n"
        "2️⃣ جلسة البوت: سيتم إنشاء جلسة اتصال داخل حسابك باسم كمبيوتر، لا تشاركها.\n"
        "3️⃣ جودة الأرقام: يُفضّل استخدام أرقام قديمة لتقليل قيود Telegram.\n\n"
        "👇 هل توافق على المتابعة؟",
        reply_markup=account_terms_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "accounts:terms_cancel")
async def cancel_account_terms(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    accounts = await list_accounts(telegram_user_id=callback.from_user.id) if callback.from_user else []
    await callback.message.edit_text(accounts_text(accounts), reply_markup=accounts_keyboard(accounts))
    await state.clear()
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "accounts:accept_terms")
async def start_add_account(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(AccountForm.waiting_for_phone)
    await callback.message.edit_text(
        "📱 أرسل رقم الهاتف بصيغة دولية، مثال:\n+970xxxxxxxxx\n\n"
        "اضغط «رجوع» للإلغاء.",
        reply_markup=account_input_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda callback: callback.data and callback.data.startswith("accounts:delete:"))
async def confirm_delete_account(callback: CallbackQuery) -> None:
    if callback.message is None or callback.data is None:
        return
    account_id = int(callback.data.rsplit(":", 1)[1])
    await callback.message.edit_text("⚠️ هل تريد حذف الحساب وإنهاء جلسة Telegram؟", reply_markup=account_details_keyboard(account_id, confirm_delete=True))
    await callback.answer()


@router.callback_query(lambda callback: callback.data and callback.data.startswith("accounts:delete_confirm:"))
async def delete_account(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    account_id = int(callback.data.rsplit(":", 1)[1])
    try:
        deleted = await terminate_account_session(telegram_user_id=callback.from_user.id, account_id=account_id)
    except Exception:
        await callback.answer("تعذر إنهاء جلسة Telegram. لم يتم حذف الحساب.", show_alert=True)
        return
    if not deleted:
        await callback.answer("الحساب غير موجود.", show_alert=True)
        return
    accounts = await list_accounts(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text("✅ تم حذف الحساب وإنهاء جلسته.\n\n" + accounts_text(accounts), reply_markup=accounts_keyboard(accounts))
    await callback.answer()


@router.message(AccountForm.waiting_for_phone)
async def save_phone_number(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text:
        await message.answer("أرسل رقم هاتف صالحاً بصيغة دولية.")
        return
    phone = message.text.strip().replace(" ", "")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        await message.answer("⚠️ رقم الهاتف غير صالح. استخدم الصيغة الدولية، مثال: +970xxxxxxxxx")
        return
    try:
        await create_pending_account(telegram_user_id=message.from_user.id, phone_number=phone)
    except ValueError as error:
        await message.answer(f"⚠️ {error}")
        await state.clear()
        return
    try:
        await start_login(telegram_user_id=message.from_user.id, phone_number=phone)
    except Exception:
        await message.answer("تعذر إرسال رمز Telegram. تحقق من بيانات API وحاول مرة أخرى.")
        return
    await state.set_state(AccountForm.waiting_for_code)
    await state.update_data(phone=phone)
    await message.answer("✅ تم إرسال رمز التحقق إلى Telegram.\n\nأرسل الرمز بهذا الشكل:\n1 2 3 4 5")


@router.message(AccountForm.waiting_for_code)
async def verify_code(message: Message, state: FSMContext) -> None:
    code = "".join(message.text.split()) if message.text else ""
    if message.from_user is None or not code or not code.isdigit():
        await message.answer("أرسل رمز التحقق الرقمي فقط.")
        return
    try:
        verified = await complete_login(telegram_user_id=message.from_user.id, code=code)
    except Exception:
        await message.answer("رمز التحقق غير صحيح أو انتهت صلاحيته. ابدأ إضافة الحساب من جديد.")
        await state.clear()
        return
    if not verified:
        await state.set_state(AccountForm.waiting_for_password)
        await message.answer("🔐 الحساب محمي بالتحقق بخطوتين. أرسل كلمة مرور 2FA:")
        return
    await state.clear()
    await message.answer("✅ تم ربط حساب Telegram بنجاح.")
    user = await get_telegram_user(telegram_user_id=message.from_user.id)
    counts = await get_dashboard_counts(telegram_user_id=message.from_user.id)
    await message.answer(
        home_text(user),
        reply_markup=main_menu_keyboard(is_publishing=user.is_publishing, counts=counts),
        parse_mode="HTML",
    )


@router.message(AccountForm.waiting_for_password)
async def verify_password(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text:
        await message.answer("أرسل كلمة مرور 2FA.")
        return
    try:
        await complete_password_login(telegram_user_id=message.from_user.id, password=message.text)
    except Exception:
        await message.answer("تعذر التحقق من كلمة مرور 2FA. ابدأ إضافة الحساب من جديد.")
        await state.clear()
        return
    await state.clear()
    await message.answer("✅ تم ربط حساب Telegram بنجاح.")
    user = await get_telegram_user(telegram_user_id=message.from_user.id)
    counts = await get_dashboard_counts(telegram_user_id=message.from_user.id)
    await message.answer(
        home_text(user),
        reply_markup=main_menu_keyboard(is_publishing=user.is_publishing, counts=counts),
        parse_mode="HTML",
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("accounts:view:"))
async def show_account_details(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return
    account_id = int(callback.data.rsplit(":", 1)[1])
    accounts = await list_accounts(telegram_user_id=callback.from_user.id)
    account = next((item for item in accounts if item.id == account_id), None)
    if account is None:
        await callback.answer("الحساب غير موجود.", show_alert=True)
        return
    status = "🟢 متصل" if account.status == "connected" else "🟡 بانتظار التحقق"
    await callback.message.edit_text(
        f"📱 تفاصيل الحساب:\n\n"
        f"رقم الهاتف: {account.phone_number}\n"
        f"الحالة: {status}\n\n"
        "سيتم تفعيل تسجيل الدخول والتحقق في الخطوة التالية.",
        reply_markup=account_details_keyboard(account.id),
    )
    await callback.answer()
