"""Create events inside the caller's state transaction; no network I/O."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from notifications.models import BotNotification


def emit(user_id, kind, event_key, *, run=None, payload=None, phase_version=None):
    event, _ = BotNotification.objects.get_or_create(event_key=event_key, defaults={
        "user_id": user_id, "kind": kind, "run": run, "payload": payload or {},
        "phase_version": phase_version, "available_at": timezone.now(),
    })
    return event


def local_time(value, zone):
    if not value:
        return ""
    if isinstance(value, str):
        from datetime import datetime
        value = datetime.fromisoformat(value)
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Asia/Gaza")
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def render(event):
    p = event.payload
    when = local_time(p.get("until"), p.get("timezone", "Asia/Gaza"))
    if event.kind == "sleep_started":
        return f"🌙 بدأت فترة الراحة. موعد انتهاء السكون: {when}."
    if event.kind == "sleep_ended":
        account = event.run.account if event.run_id else None
        if account and account.status == "paused_flood_wait" and account.paused_until and account.paused_until > timezone.now():
            return "☀️ انتهت فترة السكون. ما زال الحساب ينتظر انتهاء قيد Telegram قبل استئناف النشر."
        return "☀️ انتهت فترة السكون وعاد جدول النشر للعمل، مع مراعاة فواصل الإرسال وقيود الحساب."
    if event.kind == "flood_started":
        return f"⏳ فرض Telegram انتظارًا مؤقتًا على حساب النشر. ينتهي القيد في {when}، ثم يُستأنف النشر وفق جدول العمل والراحة."
    if event.kind == "flood_ended":
        return "✅ انتهى انتظار Telegram المؤقت. يستأنف النشر وفق جدول العمل والراحة وأي قيود أخرى."
    if event.kind == "account_restricted":
        return "⚠️ توقف النشر بسبب تقييد حساب Telegram. تحقق من الحساب قبل تشغيل النشر مجددًا."
    if event.kind == "session_invalid":
        return "🔐 توقف النشر لأن جلسة حساب Telegram لم تعد صالحة. أعد ربط الحساب ثم شغّل النشر."
    if event.kind == "daily_limit":
        return "📊 توقف النشر بعد بلوغ الحد اليومي لخطتك. يمكنك تشغيله مجددًا بعد تجدد الحد."
    if event.kind == "slow_mode":
        return "🐢 فعّل Telegram وضع الإرسال البطيء لبعض المجموعات. تم حفظ فترات الانتظار، ويستمر النشر في المجموعات المتاحة."
    if event.kind == "subscription_expired":
        return "⌛ انتهى اشتراكك المدفوع وتمت العودة إلى الخطة المجانية. راجع الباقات لتجديد الاشتراك."
    if event.kind == "subscription_renewed":
        return f"💎 تم تجديد اشتراكك في باقة {p['plan']} بنجاح. ينتهي في {when}."
    if event.kind == "subscription_changed":
        return f"💎 تم تغيير باقتك من {p['old_plan']} إلى {p['plan']}. ينتهي الاشتراك في {when}."
    if event.kind == "subscription_activated":
        return f"🎉 تم تفعيل اشتراكك في باقة {p['plan']} بنجاح. ينتهي في {when}."
    raise ValueError("Unknown notification kind")
