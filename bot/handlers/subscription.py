from functools import wraps

from asgiref.sync import sync_to_async
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from django.db import OperationalError

from bot.keyboards.main import main_menu_keyboard
from bot.keyboards.subscription import (
    durations_keyboard,
    payment_confirmation_keyboard,
    manual_payment_keyboard,
    offers_keyboard,
    payment_methods_keyboard,
    plans_keyboard,
    subscription_keyboard,
)
from bot.ui.home import home_text
from bot.ui.manual_payment import manual_payment_copy, manual_payment_text
from bot.ui.subscription import (
    is_renewal, offer_selection_text, plan_text, plans_text, review_text, subscription_text,
)
from publishing.services.contention import is_sqlite_contention
from subscriptions.models import PaymentTransaction, PlanDuration, SubscriptionPlan
from subscriptions.services.subscriptions import (
    create_pending_payment,
    fail_pending_payment,
    get_subscription,
    is_valid_payment_offer,
    list_active_plans,
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
    # The transactional notification outbox sends the activation confirmation.
    user = await get_telegram_user(telegram_user_id=message.from_user.id)
    counts = await get_dashboard_counts(telegram_user_id=message.from_user.id)
    await message.answer(
        home_text(user),
        reply_markup=main_menu_keyboard(is_publishing=user.is_publishing, counts=counts),
        parse_mode="HTML",
    )


@sync_to_async
def get_plan(*, plan_id: int) -> SubscriptionPlan:
    plan = SubscriptionPlan.objects.prefetch_related("durations").get(
        pk=plan_id, is_active=True, is_free=False,
    )
    plan.offers = [offer for offer in plan.durations.all() if is_valid_payment_offer(offer)]
    return plan


@sync_to_async
def get_duration(*, duration_id: int) -> PlanDuration:
    duration = PlanDuration.objects.select_related("plan").get(
        pk=duration_id, is_active=True, plan__is_active=True, plan__is_free=False,
    )
    if not is_valid_payment_offer(duration):
        raise ValueError("Unavailable offer")
    return duration


@sync_to_async
def get_manual_order(*, telegram_user_id: int, order_id=None):
    orders = PaymentTransaction.objects.select_related("plan", "duration").filter(
        user__telegram_user_id=telegram_user_id, payment_method="manual",
    )
    if order_id is not None:
        # Re-read the status, even if the button was rendered while it was pending.
        return orders.get(pk=order_id)
    return orders.filter(status=PaymentTransaction.PENDING).order_by("-created_at", "-pk").first()


def _positive_id(value: str) -> int:
    if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 2**63 - 1:
        raise ValueError("Invalid selection")
    return int(value)


class PaymentScreenError(ValueError):
    """A user-facing payment error that should acknowledge the callback."""


def subscription_screen(handler):
    @wraps(handler)
    async def wrapped(callback: CallbackQuery):
        alert = None
        try:
            if callback.from_user is not None and callback.message is not None:
                await handler(callback)
        except PaymentScreenError as error:
            alert = str(error)
        except PaymentTransaction.DoesNotExist:
            alert = "طلب الدفع غير متاح. ارجع إلى اشتراكك لمتابعة طلباتك."
        except (SubscriptionPlan.DoesNotExist, PlanDuration.DoesNotExist, ValueError, IndexError):
            alert = manual_payment_copy()["unavailable"]
        except OperationalError as error:
            if not is_sqlite_contention(error):
                raise
            alert = "يجري معالجة طلب آخر حاليًا. يرجى المحاولة مجددًا بعد لحظات."
        finally:
            # Every path, including stale buttons and repeated edits, clears the spinner.
            if alert:
                await callback.answer(alert, show_alert=True)
            else:
                await callback.answer()
    return wrapped


async def _edit(callback, text, keyboard):
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise


async def _show_plan(callback, plan_id: int, selected_days=None, selected_offer_id=None, *, payment_method=None):
    plan = await get_plan(plan_id=plan_id)
    subscription = await get_subscription(telegram_user_id=callback.from_user.id)
    methods = {offer.payment_method for offer in plan.offers}
    if payment_method is not None and payment_method not in methods:
        raise ValueError("Unavailable payment method")
    choose_method = payment_method is None and len(methods) > 1
    if payment_method is None and len(methods) == 1:
        payment_method = next(iter(methods))
    offers = [offer for offer in plan.offers if payment_method is None or offer.payment_method == payment_method]
    if selected_days not in {offer.days for offer in offers}:
        selected_days = None
    selected_offer = next((offer for offer in offers if offer.pk == selected_offer_id and offer.days == selected_days), None)
    if selected_offer is None:
        selected_offer_id = None
    text = plan_text(plan, renewal=is_renewal(subscription, plan.pk), selected_days=selected_days,
        has_offers=bool(offers), payment_method=payment_method, choose_method=choose_method)
    if choose_method:
        keyboard = payment_methods_keyboard(methods, plan_id=plan.pk, selected_days=selected_days,
            selected_offer_id=selected_offer_id, selected_method=selected_offer.payment_method if selected_offer else None)
    else:
        keyboard = durations_keyboard(offers, plan_id=plan.pk, payment_method=payment_method,
            has_other_methods=len(methods) > 1, selected_days=selected_days, selected_offer_id=selected_offer_id)
    await _edit(callback, text, keyboard)


async def _show_review(callback, offer):
    plan = await get_plan(plan_id=offer.plan_id)
    # Fetch alternatives again; a catalogue entry can change after a button was shown.
    offer = next((item for item in plan.offers if item.pk == offer.pk and item.days == offer.days), None)
    if offer is None:
        raise ValueError("Unavailable offer")
    alternatives = [item for item in plan.offers if item.days == offer.days and item.payment_method == offer.payment_method]
    methods = {item.payment_method for item in plan.offers}
    subscription = await get_subscription(telegram_user_id=callback.from_user.id)
    await _edit(callback, review_text(
        offer, renewal=is_renewal(subscription, plan.pk),
        distinguish_offer=len(alternatives) > 1,
    ), payment_confirmation_keyboard(offer, has_other_methods=len(methods) > 1, has_other_offers=len(alternatives) > 1))


async def _show_term(callback, plan, days: int, payment_method: str, selected_offer_id=None):
    offers = [offer for offer in plan.offers if offer.days == days and offer.payment_method == payment_method]
    if not offers:
        raise ValueError("Unavailable term")
    if selected_offer_id not in {offer.pk for offer in offers}:
        selected_offer_id = None
    if len(offers) == 1:
        await _show_review(callback, offers[0])
    else:
        await _edit(callback, offer_selection_text(plan, days, payment_method), offers_keyboard(
            offers, plan_id=plan.pk, days=days, payment_method=payment_method, selected_offer_id=selected_offer_id,
        ))


async def _callback_offer(callback, *, legacy=False):
    parts = callback.data.split(":")
    if legacy:
        if len(parts) != 4 or parts[2] not in {"manual", "telegram_stars"}:
            raise ValueError("Invalid selection")
        offer = await get_duration(duration_id=_positive_id(parts[3]))
        if offer.payment_method != parts[2]:
            raise ValueError("Invalid method")
        return offer
    if len(parts) != 5:
        raise ValueError("Invalid selection")
    plan_id, days, offer_id = map(_positive_id, parts[2:])
    offer = await get_duration(duration_id=offer_id)
    if offer.plan_id != plan_id or offer.days != days:
        raise ValueError("Invalid selection")
    return offer


async def _show_order(callback, payment):
    await _edit(callback, manual_payment_text(payment), manual_payment_keyboard(payment.pk, status=payment.status))


@router.callback_query(F.data == "subscription:details")
@subscription_screen
async def show_subscription(callback: CallbackQuery) -> None:
    subscription = await get_subscription(telegram_user_id=callback.from_user.id)
    pending = await get_manual_order(telegram_user_id=callback.from_user.id)
    plan = subscription.plan_definition
    await _edit(callback, subscription_text(subscription), subscription_keyboard(
        renewal_plan_id=plan.pk if plan and is_renewal(subscription, plan.pk) else None,
        pending_order_id=pending.pk if pending else None,
    ))


@router.callback_query(F.data == "subscription:plans")
@subscription_screen
async def show_plans(callback: CallbackQuery) -> None:
    plans = await list_active_plans()
    available = [plan for plan in plans if not plan.is_free and any(
        is_valid_payment_offer(offer) for offer in plan.durations.all()
    )]
    subscription = await get_subscription(telegram_user_id=callback.from_user.id)
    current = subscription.plan_definition_id if subscription.status == "active" else None
    await _edit(callback, plans_text(bool(available)), plans_keyboard(available, current_plan_id=current))


@router.callback_query(F.data.startswith("subscription:plan:"))
@subscription_screen
async def show_plan(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) not in {3, 4, 5}:
        raise ValueError("Invalid selection")
    await _show_plan(callback, _positive_id(parts[2]),
        _positive_id(parts[3]) if len(parts) >= 4 else None,
        _positive_id(parts[4]) if len(parts) == 5 else None,
    )


@router.callback_query(F.data.startswith("subscription:term:"))
@subscription_screen
async def choose_term(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) not in {4, 5}:
        raise ValueError("Invalid selection")
    plan = await get_plan(plan_id=_positive_id(parts[2]))
    days = _positive_id(parts[3])
    offers = [offer for offer in plan.offers if offer.days == days]
    if not offers:
        raise ValueError("Unavailable term")
    selected_offer_id = _positive_id(parts[4]) if len(parts) == 5 else None
    methods = {offer.payment_method for offer in offers}
    if len(methods) > 1:
        # Legacy day-first buttons now lead to the method selector.
        await _show_plan(callback, plan.pk, days, selected_offer_id)
    else:
        await _show_term(callback, plan, days, next(iter(methods)), selected_offer_id)


@router.callback_query(F.data.startswith("subscription:term-method:"))
@subscription_screen
async def choose_method_term(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) not in {5, 6} or parts[2] not in {"manual", "telegram_stars"}:
        raise ValueError("Invalid selection")
    plan = await get_plan(plan_id=_positive_id(parts[3]))
    await _show_term(callback, plan, _positive_id(parts[4]), parts[2],
        _positive_id(parts[5]) if len(parts) == 6 else None)


@router.callback_query(F.data.startswith("subscription:review:"))
@subscription_screen
async def review_subscription(callback: CallbackQuery) -> None:
    await _show_review(callback, await _callback_offer(callback))


@router.callback_query(F.data.startswith("subscription:order:"))
@subscription_screen
async def show_order(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        raise ValueError("Invalid order")
    payment = await get_manual_order(telegram_user_id=callback.from_user.id, order_id=_positive_id(parts[2]))
    await _show_order(callback, payment)


@router.callback_query(F.data.startswith("subscription:method:"))
@subscription_screen
async def choose_payment_method(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) not in {4, 5, 6} or parts[2] not in {"manual", "telegram_stars"}:
        raise ValueError("Invalid selection")
    await _show_plan(callback, _positive_id(parts[3]),
        _positive_id(parts[4]) if len(parts) >= 5 else None,
        _positive_id(parts[5]) if len(parts) == 6 else None, payment_method=parts[2])


@router.callback_query(F.data.startswith("subscription:duration:"))
@subscription_screen
async def choose_duration(callback: CallbackQuery) -> None:
    await _show_review(callback, await _callback_offer(callback, legacy=True))


@router.callback_query(F.data.startswith("subscription:method-back:"))
@subscription_screen
async def back_to_payment_method(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) not in {3, 4}:
        raise ValueError("Invalid selection")
    offer = await get_duration(duration_id=_positive_id(parts[-1]))
    await _show_plan(callback, offer.plan_id, offer.days, offer.pk, payment_method=offer.payment_method)


@router.callback_query(F.data.startswith("subscription:pay:"))
@subscription_screen
async def demo_payment(callback: CallbackQuery) -> None:
    # Retain the existing handler name for compatibility; all payments require confirmation.
    offer = await _callback_offer(callback, legacy=len(callback.data.split(":")) == 4)
    payment = await create_pending_payment(
        telegram_user_id=callback.from_user.id, duration_id=offer.pk, payment_method=offer.payment_method,
    )
    if offer.payment_method == "manual":
        payment = await get_manual_order(telegram_user_id=callback.from_user.id, order_id=payment.pk)
        await _show_order(callback, payment)
        return
    try:
        invoice = await callback.message.answer_invoice(
            title=f"{offer.plan.name} - {offer.label}",
            description=offer.plan.description or f"اشتراك لمدة {offer.label}",
            payload=payment.invoice_payload,
            currency="XTR",
            prices=[LabeledPrice(label=offer.label, amount=int(payment.amount))],
        )
        await set_payment_invoice_id(payment.id, str(invoice.message_id))
    except Exception as error:
        await fail_pending_payment(payment.id, str(error))
        raise PaymentScreenError("تعذر إنشاء فاتورة الدفع. يرجى المحاولة مجددًا.") from error
    await _edit(callback, "⭐ <b>فاتورة الدفع جاهزة</b>\n\nأكمل الدفع من فاتورة Telegram Stars أدناه.", subscription_keyboard())
