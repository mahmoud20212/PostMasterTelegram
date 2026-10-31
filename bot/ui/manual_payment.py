"""Arabic copy for manual subscription payments."""

from html import escape

from bot.ui.subscription import duration_text, price_text


COPY = {
    "title": "طلب الاشتراك",
    "plan": "الباقة",
    "duration": "المدة",
    "price": "السعر",
    "order": "رقم الطلب",
    "status": "الحالة",
    "instructions": (
        "لإتمام اشتراكك، تواصل مع الدعم الفني "
        "وأرسل رقم الطلب للحصول على تفاصيل الدفع."
    ),
    "activation": "سيتم تفعيل اشتراكك بعد تأكيد استلام المبلغ.",
    "support": "💬 إتمام الدفع عبر الدعم",
    "back": "⬅️ اشتراكي",
    "draft": (
        "مرحبًا، أريد إتمام الدفع لطلب الاشتراك "
        "رقم #{order_id}"
    ),
    "unavailable": "خيار الدفع غير متاح. يرجى اختيار الباقة والمدة مجددًا.",
    "statuses": {
        "pending": "⏳ بانتظار الدفع",
        "paid": "✅ مدفوع",
        "failed": "❌ فشل الدفع",
        "canceled": "ملغى",
        "rejected": "مرفوض",
    },
}


def manual_payment_copy(language_code=None):
    return COPY


def manual_payment_text(payment, lang_code=None):
    status = COPY["statuses"].get(payment.status, payment.status)
    details = (
        f"🧾 <b>{COPY['title']}</b>\n\n"
        f"📦 {COPY['plan']}: {escape(payment.plan.name)}\n"
        f"⏱ {COPY['duration']}: {duration_text(payment.duration.days)}\n"
        f"💵 {COPY['price']}: {price_text(payment.amount, payment.currency)}\n"
        f"🔢 {COPY['order']}: <code>#{payment.pk}</code>\n"
        f"📋 {COPY['status']}: {escape(status)}"
    )
    if payment.status == "pending":
        return details + f"\n\n💬 {COPY['instructions']}\n\n✅ {COPY['activation']}"
    if payment.status == "paid":
        return details + "\n\n✅ تم تأكيد الدفع. يمكنك عرض اشتراكك من الزر أدناه."
    return details + "\n\nيمكنك العودة إلى اشتراكك لاستعراض الباقات وإنشاء طلب جديد."
