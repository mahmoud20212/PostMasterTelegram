"""Generate synthetic input, never read an application/production SQLite file."""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    output = Path(parser.parse_args().output).resolve()
    if output.exists():
        raise RuntimeError("Refusing to overwrite an existing fixture")
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.migration_fixture_settings"
    # Guard against inheriting a PostgreSQL opt-in while making synthetic data.
    os.environ["DATABASE_BACKEND"] = "sqlite"
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connections
    from django.contrib.auth.models import User as AdminUser, Group, Permission
    from users.models import User
    from telegram_accounts.models import TelegramAccount
    from telegram_groups.models import TelegramGroup
    from messages.models import SavedMessage
    from publishing.models import PublishingRun, PublishingSettings, PublishLog
    from subscriptions.models import Subscription, SubscriptionPlan, PlanDuration, PaymentTransaction
    from django.contrib.admin.models import LogEntry, ADDITION
    from django.contrib.contenttypes.models import ContentType
    from django.contrib.sessions.backends.db import SessionStore
    from django.utils import timezone
    from datetime import timedelta
    from staging.sqlite_snapshot import backup

    call_command("migrate", verbosity=0, interactive=False)
    user = User.objects.create(id=501, telegram_user_id=900000001, is_publishing=True)
    account = TelegramAccount.objects.create(id=601, user=user, phone_number="+10000000501",
                    encrypted_session="SYNTHETIC_NOT_A_REAL_TELEGRAM_SESSION", status="connected")
    message = SavedMessage.objects.create(id=701, user=user, text="اختبار PostgreSQL", entities=[])
    group = TelegramGroup.objects.create(id=801, user=user, identifier="@synthetic_group",
                                        canonical_identifier="username:synthetic_group")
    PublishingSettings.objects.create(user=user, current_message_index=7, current_group_index=3)
    PublishingRun.objects.create(user=user, account=account, task_claimed=True)
    PublishLog.objects.create(user=user, account=account, message=message, group=group, status="success")
    plan = SubscriptionPlan.objects.get(code="free")
    duration = PlanDuration.objects.create(plan=plan, label="synthetic", days=30,
                    price="10.25", currency="USD", payment_method="manual")
    Subscription.objects.create(user=user, plan_definition=plan, duration=duration,
                    end_date=timezone.now() + timedelta(days=30), limits_snapshot={"groups": 3})
    PaymentTransaction.objects.create(user=user, plan=plan, duration=duration,
                    payment_method="manual", amount="10.25", currency="USD",
                    invoice_payload="synthetic-only", status="paid", paid_at=timezone.now())
    admin = AdminUser.objects.create(username="synthetic-admin", password="!", is_staff=True)
    auth_group = Group.objects.create(name="synthetic-reviewers")
    auth_group.permissions.add(Permission.objects.first())
    admin.groups.add(auth_group)
    admin.user_permissions.add(Permission.objects.last())
    LogEntry.objects.create(user=admin, content_type=ContentType.objects.get_for_model(User),
                    object_id=str(user.pk), object_repr="synthetic", action_flag=ADDITION)
    SessionStore().create()
    path = connections["default"].settings_dict["NAME"]
    connections["default"].close()
    backup(path, output)
    print("Synthetic SQLite fixture created; no credentials or real sessions used.")


if __name__ == "__main__":
    main()
