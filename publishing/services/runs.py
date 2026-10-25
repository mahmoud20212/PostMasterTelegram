"""Run ownership transitions. The first transaction statement is a write.

This deliberately avoids relying on SELECT FOR UPDATE on SQLite. Conditional
updates serialize competing starts; no database transaction spans Telegram I/O.
"""
from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from messages.models import SavedMessage
from publishing.models import PublishingRun, PublishingSettings
from publishing.services.policy import _normalise
from publishing.services.contention import retry_transition
from subscriptions.models import Subscription
from subscriptions.services.subscriptions import _ensure_subscription
from telegram_accounts.models import TelegramAccount
from telegram_groups.models import TelegramGroup
from users.models import User


ELIGIBLE_GROUP_STATUSES = ("pending", "active")


def _subscription_allowed(subscription):
    return (
        subscription.status == "active"
        and subscription.plan_definition_id is not None
        and (subscription.end_date is None or subscription.end_date > timezone.now())
    )


@retry_transition
def create_run(telegram_user_id):
    with transaction.atomic():
        # A compare-and-set, including on SQLite: only one caller can win.
        if not User.objects.filter(telegram_user_id=telegram_user_id, is_publishing=False).update(is_publishing=True):
            return None, "النشر يعمل بالفعل."
        user = User.objects.get(telegram_user_id=telegram_user_id)
        subscription = _ensure_subscription(user)
        account = TelegramAccount.objects.filter(user=user, status="connected").exclude(encrypted_session="").order_by("-is_publishing_account", "id").first()
        error = None
        if user.status != "active" or not _subscription_allowed(subscription):
            error = "الحساب أو الاشتراك غير متاح للنشر."
        elif account is None or (account.paused_until and account.paused_until > timezone.now()):
            error = "أضف حساب Telegram متصلاً أولاً."
        elif not SavedMessage.objects.filter(user=user, is_active=True).exists():
            error = "أضف رسالة فعالة أولاً."
        elif not TelegramGroup.objects.filter(user=user, is_selected=True, status__in=ELIGIBLE_GROUP_STATUSES).exists():
            error = "أضف مجموعة وحددها للنشر أولاً."
        if error:
            User.objects.filter(pk=user.pk).update(is_publishing=False)
            return None, error
        # Clean up an invalidated legacy/expired run without ever reusing its ID.
        PublishingRun.objects.filter(user=user, active=True).update(active=False, stopped_at=timezone.now())
        run = PublishingRun.objects.create(user=user, account=account)
        settings, _ = PublishingSettings.objects.get_or_create(user=user)
        settings.cycle_started_at = timezone.now()
        settings.is_running = True
        settings.save(update_fields=["cycle_started_at", "is_running"])
        return run, "تم تشغيل محرك النشر."


@retry_transition
def finish_run(telegram_user_id, run_id=None):
    with transaction.atomic():
        users = User.objects.filter(telegram_user_id=telegram_user_id)
        if run_id is not None:
            users = users.filter(publishing_runs__id=run_id, publishing_runs__active=True)
        # The write is first, with the ownership predicate in the same statement.
        if not users.update(is_publishing=False):
            return False
        runs = PublishingRun.objects.filter(user__telegram_user_id=telegram_user_id, active=True)
        if run_id is not None:
            runs = runs.filter(pk=run_id)
        runs.update(active=False, stopped_at=timezone.now())
        PublishingSettings.objects.filter(user__telegram_user_id=telegram_user_id).update(is_running=False)
        return True


@retry_transition
def recover_legacy_user(telegram_user_id):
    """Administrative operation: never touches a user with a new active run."""
    with transaction.atomic():
        changed = User.objects.filter(
            telegram_user_id=telegram_user_id, is_publishing=True,
        ).exclude(publishing_runs__active=True).update(is_publishing=False)
        if changed:
            PublishingSettings.objects.filter(user__telegram_user_id=telegram_user_id).update(is_running=False)
        return bool(changed)


@sync_to_async
def claim_run(telegram_user_id, run_id):
    return bool(PublishingRun.objects.filter(
        pk=run_id, user__telegram_user_id=telegram_user_id,
        user__is_publishing=True, active=True, task_claimed=False,
    ).update(task_claimed=True))


@sync_to_async
def run_is_active(telegram_user_id, run_id):
    return PublishingRun.objects.filter(
        pk=run_id, user__telegram_user_id=telegram_user_id,
        user__is_publishing=True, active=True,
    ).exists()


@sync_to_async
def run_policy(telegram_user_id, run_id, account_id, encrypted_session=None):
    """Read fresh permission state; no destructive subscription expiry side effects."""
    run = PublishingRun.objects.filter(
        pk=run_id, user__telegram_user_id=telegram_user_id, user__is_publishing=True,
        user__status="active", active=True, account_id=account_id,
        account__user__telegram_user_id=telegram_user_id, account__status="connected",
    ).select_related("account").first()
    if run is None or not run.account.encrypted_session:
        return None
    if encrypted_session is not None and run.account.encrypted_session != encrypted_session:
        return None
    if run.account.paused_until and run.account.paused_until > timezone.now():
        return None
    subscription = Subscription.objects.select_related("plan_definition").filter(user_id=run.user_id).first()
    if subscription is None or not _subscription_allowed(subscription):
        return None
    return _normalise(subscription.plan_definition)


@sync_to_async
def send_target_allowed(telegram_user_id, group_id, message_id):
    return (
        TelegramGroup.objects.filter(pk=group_id, user__telegram_user_id=telegram_user_id,
                                     is_selected=True, status__in=ELIGIBLE_GROUP_STATUSES).exists()
        and SavedMessage.objects.filter(pk=message_id, user__telegram_user_id=telegram_user_id, is_active=True).exists()
    )


create_run_async = sync_to_async(create_run)
finish_run_async = sync_to_async(finish_run)
