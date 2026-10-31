from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bot.keyboards import main
from bot.ui.manual_payment import manual_payment_copy
from bot.ui.subscription import duration_text, method_text, offer_price


def subscription_keyboard(*, renewal_plan_id=None, pending_order_id=None) -> InlineKeyboardMarkup:
    rows = []
    if renewal_plan_id is not None:
        rows.append([InlineKeyboardButton(text="🔄 تجديد الاشتراك", callback_data=f"subscription:plan:{renewal_plan_id}")])
    rows.append([InlineKeyboardButton(text="📦 استعراض الباقات", callback_data="subscription:plans")])
    if pending_order_id is not None:
        rows.append([InlineKeyboardButton(text=f"🧾 متابعة طلب الدفع #{pending_order_id}", callback_data=f"subscription:order:{pending_order_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ القائمة الرئيسية", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_keyboard(plans, *, current_plan_id=None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{plan.name}" + (" · باقتك الحالية" if plan.pk == current_plan_id else ""), callback_data=f"subscription:plan:{plan.id}")]
        for plan in plans if not plan.is_free
    ]
    rows.append([InlineKeyboardButton(text="⬅️ اشتراكي", callback_data="subscription:details")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _selection_context(selected_days=None, selected_offer_id=None) -> str:
    if selected_days is None:
        return ""
    return f":{selected_days}" + (f":{selected_offer_id}" if selected_offer_id is not None else "")


def payment_methods_keyboard(methods, *, plan_id: int, selected_days=None, selected_offer_id=None, selected_method=None) -> InlineKeyboardMarkup:
    selection = _selection_context(selected_days, selected_offer_id)
    rows = [[InlineKeyboardButton(
        text=("✓ " if method == selected_method else "") + method_text(method),
        callback_data=f"subscription:method:{method}:{plan_id}{selection}",
    )] for method in ("manual", "telegram_stars") if method in methods]
    rows.append([InlineKeyboardButton(text="⬅️ الباقات", callback_data="subscription:plans")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def durations_keyboard(durations, *, plan_id: int, payment_method=None, has_other_methods=False, selected_days=None, selected_offer_id=None) -> InlineKeyboardMarkup:
    grouped = {}
    for offer in durations:
        grouped.setdefault(offer.days, []).append(offer)
    rows = []
    for days, offers in grouped.items():
        prices = list(dict.fromkeys(offer_price(offer) for offer in offers))
        selection = f":{selected_offer_id}" if selected_offer_id is not None and days == selected_days else ""
        rows.append([InlineKeyboardButton(
            text=("✓ " if days == selected_days else "") + f"{duration_text(days)} · {' / '.join(prices)}",
            callback_data=f"subscription:term-method:{payment_method}:{plan_id}:{days}{selection}",
        )])
    if has_other_methods:
        selection = _selection_context(selected_days, selected_offer_id)
        rows.append([InlineKeyboardButton(text="⬅️ وسائل الدفع", callback_data=f"subscription:plan:{plan_id}{selection}")])
    else:
        rows.append([InlineKeyboardButton(text="⬅️ الباقات", callback_data="subscription:plans")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def offers_keyboard(offers, *, plan_id: int, days: int, payment_method: str, selected_offer_id=None) -> InlineKeyboardMarkup:
    rows = []
    for offer in offers:
        duplicate_method = sum(item.payment_method == offer.payment_method for item in offers) > 1
        label = f" · {offer.label}" if duplicate_method else ""
        rows.append([InlineKeyboardButton(
            text=("✓ " if offer.pk == selected_offer_id else "") + f"{method_text(offer.payment_method)} · {offer_price(offer)}{label}",
            callback_data=f"subscription:review:{plan_id}:{days}:{offer.pk}",
        )])
    selection = f":{selected_offer_id}" if selected_offer_id is not None else ""
    rows.append([InlineKeyboardButton(text="⬅️ تغيير المدة", callback_data=f"subscription:method:{payment_method}:{plan_id}:{days}{selection}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_payment_support_url(order_id: int, language_code=None) -> str:
    url = main.DEVELOPER_URL
    if url.startswith("@"):
        url = f"https://t.me/{url[1:]}"
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    public_username = parts.scheme in {"https", "http"} and parts.netloc.lower() in {"t.me", "telegram.me"} and re.fullmatch(r"/[A-Za-z][A-Za-z0-9_]{4,31}/?", parts.path)
    resolve_username = parts.scheme == "tg" and parts.netloc == "resolve" and any(key == "domain" for key, _ in query)
    if public_username or resolve_username:
        query = [(key, value) for key, value in query if key not in {"text", "profile"}]
        draft = manual_payment_copy(language_code)["draft"].format(order_id=order_id)
        query.append(("text", draft))
        return urlunsplit(parts._replace(query=urlencode(query)))
    # Business-chat, invite and external support links retain their configured form.
    return url


def manual_payment_keyboard(order_id: int, language_code=None, *, status="pending") -> InlineKeyboardMarkup:
    copy = manual_payment_copy(language_code)
    rows = []
    if status == "pending":
        rows.append([InlineKeyboardButton(text=copy["support"], url=manual_payment_support_url(order_id, language_code))])
    rows.append([InlineKeyboardButton(text="📜 عرض اشتراكي" if status == "paid" else copy["back"], callback_data="subscription:details")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_confirmation_keyboard(offer, *, has_other_methods: bool, has_other_offers: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text="🧾 إنشاء طلب الدفع" if offer.payment_method == "manual" else "⭐ المتابعة إلى الدفع",
        callback_data=f"subscription:pay:{offer.plan_id}:{offer.days}:{offer.pk}",
    )]]
    if has_other_offers:
        rows.append([InlineKeyboardButton(text="🏷 تغيير العرض", callback_data=f"subscription:term-method:{offer.payment_method}:{offer.plan_id}:{offer.days}:{offer.pk}")])
    rows.append([InlineKeyboardButton(text="⏱ تغيير المدة", callback_data=f"subscription:method:{offer.payment_method}:{offer.plan_id}:{offer.days}:{offer.pk}")])
    if has_other_methods:
        rows.append([InlineKeyboardButton(text="💳 تغيير وسيلة الدفع", callback_data=f"subscription:plan:{offer.plan_id}:{offer.days}:{offer.pk}")])
    rows.append([InlineKeyboardButton(text="⬅️ اشتراكي", callback_data="subscription:details")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
