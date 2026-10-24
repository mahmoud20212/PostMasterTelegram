from asgiref.sync import sync_to_async
from aiogram import F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from bot.keyboards.main import main_menu_keyboard
from bot.keyboards.subscription import (
    durations_keyboard,
    payment_confirmation_keyboard,
    manual_payment_keyboard,
    payment_methods_keyboard,
    plans_keyboard,
    subscription_keyboard,
)
from bot.ui.home import home_text
from subscriptions.models import PaymentTransaction, PlanDuration, SubscriptionPlan
from subscriptions.services.subscriptions import (
    complete_demo_payment,
    create_pending_payment,
    fail_pending_payment,
    get_subscription,
    list_active_plans,
    list_plan_durations,
    finalize_stars_payment,
    set_payment_invoice_id,
    validate_pending_payment,
)
from users.services.dashboard import get_dashboard_counts
from users.services.onboarding import get_telegram_user

router = Router(name="subscription")


@router.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery) -> None:
    valid = await validate_pending_payment(
        payload=query.invoice_payload,
        telegram_user_id=query.from_user.id,
        amount=query.total_amount,
        currency=query.currency,
    )
    if valid:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="تعذر التحقق من الفاتورة. أعد المحاولة من فضلك.")


@router.message(F.successful_payment)
async def process_successful_payment(message: Message) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    payment_data = message.successful_payment
    try:
        payment, subscription = await finalize_stars_payment(
            payload=payment_data.invoice_payload,
            telegram_user_id=message.from_user.id,
            charge_id=payment_data.telegram_payment_charge_id,
        )
    except (PaymentTransaction.DoesNotExist, ValueError):
        await message.answer("تعذر تأكيد عملية الدفع. تواصل مع المطور قبل إعادة الدفع.")
        return
    await message.answer(
        f"🎉 تهانينا! تم تفعيل اشتراكك بنجاح.\n\n"
        f"📦 الخطة: {subscription.plan_definition.name}\n"
        f"🧾 رقم العملية: {payment.id}"
    )
    user = await get_telegram_user(telegram_user_id=message.from_user.id)
    counts = await get_dashboard_counts(telegram_user_id=message.from_user.id)
    await message.answer(
        home_text(user),
        reply_markup=main_menu_keyboard(is_publishing=user.is_publishing, counts=counts),
        parse_mode="HTML",
    )


def _limit_text(value) -> str:
    return "مفتوح" if value is None else str(value)


def _currency_text(duration: PlanDuration) -> str:
    return "نجمة" if duration.currency == "XTR" else "$"


def _price_text(duration: PlanDuration) -> str:
    return str(int(duration.price)) if duration.currency == "XTR" else f"{duration.price:.2f}"


def subscription_text(subscription) -> str:
    plan = subscription.plan_definition
    limits = subscription.limits_snapshot or {}
    return (
        "📜 الخطة الحالية\n\n"
        f"{plan.name if plan else subscription.plan}\n\n"
        f"📌 الحالة: {'✅ فعال' if subscription.status == 'active' else '⏸️ غير فعال'}\n"
        f"📅 الانتهاء: {subscription.end_date.strftime('%Y-%m-%d') if subscription.end_date else 'بدون انتهاء'}\n\n"
        # f"📱 الحسابات: {_limit_text(limits.get('accounts'))}\n"
        # f"👥 المجموعات: {_limit_text(limits.get('groups'))}\n"
        # f"💬 الرسائل: {_limit_text(limits.get('messages'))}"
    )


def plan_text(plan: SubscriptionPlan) -> str:
    features = "\n".join(f"✅ {feature}" for feature in plan.features) or "✅ مميزات مخصصة حسب الخطة"
    return (
        f"{plan.name}\n\n{plan.description}\n\n"
        f"📱 الحسابات: {_limit_text(plan.max_accounts)}\n"
        f"👥 المجموعات: {_limit_text(plan.max_groups)}\n"
        f"💬 الرسائل: {_limit_text(plan.max_messages)}\n\n{features}\n\n"
        "💳 اختر وسيلة الدفع أولاً:"
    )


@sync_to_async
def get_plan(*, plan_id: int) -> SubscriptionPlan:
    return SubscriptionPlan.objects.get(id=plan_id, is_active=True)


@sync_to_async
def get_duration(*, duration_id: int) -> PlanDuration:
    return PlanDuration.objects.select_related("plan").get(id=duration_id, is_active=True, plan__is_active=True)


@sync_to_async
def create_demo_payment(*, telegram_user_id: int, duration: PlanDuration) -> PaymentTransaction:
    from users.models import User

    return PaymentTransaction.objects.create(
        user=User.objects.get(telegram_user_id=telegram_user_id),
        plan=duration.plan,
        duration=duration,
        payment_method=duration.payment_method,
        amount=duration.price,
        currency=duration.currency,
    )


@router.callback_query(lambda callback: callback.data == "subscription:details")
async def show_subscription(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return
    subscription = await get_subscription(telegram_user_id=callback.from_user.id)
    await callback.message.edit_text(subscription_text(subscription), reply_markup=subscription_keyboard())
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "subscription:plans")
async def show_plans(callback: CallbackQuery) -> None:
    plans = await list_active_plans()
    available = [plan for plan in plans if not plan.is_free]
    text = "📦 ترقية الخطة الحالية\n\nاختر الخطة المناسبة لك:" if available else "لا توجد خطط مدفوعة متاحة حالياً."
    await callback.message.edit_text(text, reply_markup=plans_keyboard(plans))
    await callback.answer()


@router.callback_query(lambda callback: callback.data.startswith("subscription:plan:"))
async def show_plan(callback: CallbackQuery) -> None:
    plan = await get_plan(plan_id=int(callback.data.rsplit(":", 1)[1]))
    await callback.message.edit_text(plan_text(plan), reply_markup=payment_methods_keyboard(plan.id))
    await callback.answer()


@router.callback_query(lambda callback: callback.data.startswith("subscription:method:"))
async def choose_payment_method(callback: CallbackQuery) -> None:
    _, _, payment_method, plan_id = callback.data.split(":")
    plan = await get_plan(plan_id=int(plan_id))
    durations = await list_plan_durations(plan_id=plan.id, payment_method=payment_method)
    method_name = "Telegram Stars" if payment_method == "telegram_stars" else "Manual USD"
    await callback.message.edit_text(
        f"{plan.name}\n\n💳 وسيلة الدفع: {method_name}\n\n📌 اختر مدة الاشتراك:",
        reply_markup=durations_keyboard(durations, payment_method),
    )
    await callback.answer()


@router.callback_query(lambda callback: callback.data.startswith("subscription:duration:"))
async def choose_duration(callback: CallbackQuery) -> None:
    _, _, payment_method, duration_id = callback.data.split(":")
    duration = await get_duration(duration_id=int(duration_id))
    await callback.message.edit_text(
        f"⭐ تأكيد الاشتراك\n\n📦 الخطة: {duration.plan.name}\n⏱️ المدة: {duration.label}\n"
        f"💰 السعر: {_price_text(duration)} {_currency_text(duration)}\n"
        f"💳 الدفع: {'Telegram Stars' if payment_method == 'telegram_stars' else 'Manual USD'}",
        reply_markup=payment_confirmation_keyboard(payment_method, duration.id),
    )
    await callback.answer()


@router.callback_query(lambda callback: callback.data.startswith("subscription:method-back:"))
async def back_to_payment_method(callback: CallbackQuery) -> None:
    duration = await get_duration(duration_id=int(callback.data.rsplit(":", 1)[1]))
    plan = await get_plan(plan_id=duration.plan_id)
    await callback.message.edit_text(plan_text(plan), reply_markup=payment_methods_keyboard(plan.id))
    await callback.answer()


@router.callback_query(lambda callback: callback.data.startswith("subscription:pay:"))
async def demo_payment(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return
    _, _, method, duration_id = callback.data.split(":")
    duration = await get_duration(duration_id=int(duration_id))
    if method == "telegram_stars":
        payment = await create_pending_payment(
            telegram_user_id=callback.from_user.id,
            duration_id=duration.id,
            payment_method=method,
        )
        try:
            invoice = await callback.message.answer_invoice(
                title=f"{duration.plan.name} - {duration.label}",
                description=duration.plan.description or f"اشتراك لمدة {duration.label}",
                payload=payment.invoice_payload,
                currency="XTR",
                prices=[LabeledPrice(label=duration.label, amount=int(duration.price))],
            )
            await set_payment_invoice_id(payment.id, str(invoice.message_id))
            await callback.answer("تم إرسال فاتورة Telegram Stars.")
        except Exception as error:
            await fail_pending_payment(payment.id, str(error))
            await callback.answer("تعذر إنشاء فاتورة الدفع.", show_alert=True)
        return

    if method == "manual":
        payment = await create_pending_payment(
            telegram_user_id=callback.from_user.id,
            duration_id=duration.id,
            payment_method=method,
        )
        await callback.message.answer(
            f"🧾 تم إنشاء طلب الدفع اليدوي.\n\n"
            f"📦 الخطة: {duration.plan.name}\n"
            f"⏱️ المدة: {duration.label}\n"
            f"💵 السعر: {duration.price:.2f}$\n"
            f"🔢 رقم الطلب: {payment.id}\n\n"
            "أرسل رقم الطلب للمطور بعد إتمام الدفع. سيتم تفعيل الاشتراك بعد تأكيد العملية.",
            reply_markup=manual_payment_keyboard(),
        )
        await callback.answer()
        return

    payment, subscription = await complete_demo_payment(
        telegram_user_id=callback.from_user.id, duration_id=duration.id, payment_method=method,
    )
    method_name = "Telegram Stars" if method == "telegram_stars" else "Crypto"
    success_text = (
        f"🎉🎊 تهانينا! تم تفعيل اشتراكك بنجاح 🎊🎉\n\n"
        f"✅ طريقة الدفع: {method_name} (تجريبي)\n"
        f"📦 الخطة: {subscription.plan_definition.name}\n"
        f"📅 ينتهي الاشتراك: {subscription.end_date.strftime('%Y-%m-%d %H:%M')}\n"
        f"🧾 رقم العملية: {payment.id}"
    )
    await callback.message.answer(success_text)
    user = await get_telegram_user(telegram_user_id=callback.from_user.id)
    counts = await get_dashboard_counts(telegram_user_id=callback.from_user.id)
    await callback.message.answer(
        home_text(user),
        reply_markup=main_menu_keyboard(is_publishing=user.is_publishing, counts=counts),
        parse_mode="HTML",
    )
    await callback.answer()
