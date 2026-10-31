"""Arabic HTML views for the subscription journey (no database access)."""

from html import escape

from django.utils import timezone


def duration_text(days: int) -> str:
    if days == 1:
        return "يوم واحد"
    if days == 2:
        return "يومان"
    return f"{days} أيام" if 3 <= days <= 10 else f"{days} يومًا"


def price_text(amount, currency: str) -> str:
    if currency == "XTR":
        return f"{int(amount)} نجمة"
    if currency == "USD":
        return f"{amount:.2f}$"
    return f"{amount:.2f} {escape(str(currency))}"


def offer_price(offer) -> str:
    return price_text(offer.price, offer.currency)


def method_text(method: str) -> str:
    return "⭐ Telegram Stars" if method == "telegram_stars" else "💬 الدفع اليدوي عبر الدعم"


def is_renewal(subscription, plan_id: int) -> bool:
    return (
        subscription.status == "active"
        and subscription.plan_definition_id == plan_id
        and subscription.plan_definition is not None
        and not subscription.plan_definition.is_free
    )


def subscription_text(subscription) -> str:
    plan = subscription.plan_definition
    expiry = (
        timezone.localtime(subscription.end_date).strftime("%d / %m / %Y")
        if subscription.end_date else "بدون تاريخ انتهاء"
    )
    statuses = {"active": "✅ نشط", "expired": "⌛ منتهٍ", "canceled": "⏸ ملغى"}
    status = statuses.get(subscription.status, "⏸ غير نشط")
    return (
        "📜 <b>اشتراكك الحالي</b>\n\n"
        f"📦 {escape(str(plan.name if plan else subscription.plan))}\n"
        f"📋 الحالة: {status}\n"
        f"📅 ينتهي في: {expiry}"
    )


def plans_text(has_plans: bool) -> str:
    return (
        "📦 <b>الباقات المتاحة</b>\n\n"
        + ("اختر الباقة للاطلاع على مزاياها ومدد الاشتراك." if has_plans
           else "لا توجد باقات متاحة للاشتراك حاليًا. يمكنك العودة لاحقًا.")
    )


def plan_text(plan, *, renewal=False, selected_days=None, has_offers=True, payment_method=None, choose_method=False) -> str:
    sections = [f"📦 <b>{escape(str(plan.name))}</b>"]
    if renewal:
        sections.append("هذه باقتك الحالية. يمكنك تجديد اشتراكك من الخيارات أدناه.")
    if plan.description and plan.description.strip():
        sections.append(escape(plan.description.strip()))
    features = [escape(str(feature).strip()) for feature in (plan.features or []) if str(feature).strip()]
    if features:
        sections.append("\n".join(f"✅ {feature}" for feature in features))
    limits = [("الحسابات", plan.max_accounts), ("المجموعات", plan.max_groups), ("الرسائل", plan.max_messages)]
    unlimited = [label for label, value in limits if value is None]
    lines = [f"• {label}: {value}" for label, value in limits if value is not None]
    if unlimited:
        lines.append(f"• {' و'.join(unlimited)}: غير محدودة")
    sections.append("<b>حدود الباقة</b>\n" + "\n".join(lines))
    if payment_method is not None:
        sections.append(f"💳 وسيلة الدفع: {method_text(payment_method)}")
    prompt = "💳 اختر وسيلة الدفع:" if choose_method else "⏱ اختر مدة الاشتراك:"
    if not has_offers:
        prompt = "لا توجد مدد متاحة لهذه الباقة حاليًا."
    if selected_days is not None:
        prompt += f"\nالمدة المختارة: {duration_text(selected_days)}"
    sections.append(prompt)
    return "\n\n".join(sections)


def offer_selection_text(plan, days: int, payment_method: str) -> str:
    return (
        "🏷 <b>اختر عرض الاشتراك</b>\n\n"
        f"📦 {escape(str(plan.name))}\n"
        f"⏱ المدة: {duration_text(days)}\n"
        f"💳 الطريقة: {method_text(payment_method)}\n\n"
        "اختر العرض المناسب للمتابعة إلى المراجعة."
    )


def review_text(offer, *, renewal=False, distinguish_offer=False) -> str:
    title = "مراجعة تجديد الاشتراك" if renewal else "مراجعة الاشتراك"
    lines = [
        f"🧾 <b>{title}</b>", "",
        f"📦 الباقة: {escape(str(offer.plan.name))}",
        f"⏱ المدة: {duration_text(offer.days)}",
        f"💵 المبلغ: {offer_price(offer)}",
        f"💳 الطريقة: {method_text(offer.payment_method)}",
    ]
    if distinguish_offer:
        lines.append(f"🏷 العرض: {escape(str(offer.label))}")
    lines.extend(["", (
        "يُفعّل الاشتراك بعد تأكيد الدفع من الإدارة. إنشاء الطلب لا يفعّل الاشتراك."
        if offer.payment_method == "manual" else "ستُرسل فاتورة Telegram Stars لإتمام الدفع بأمان."
    )])
    return "\n".join(lines)
