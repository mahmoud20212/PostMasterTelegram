from django.db import migrations


def create_free_plan(apps, schema_editor):
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    SubscriptionPlan.objects.get_or_create(
        code="free",
        defaults={
            "name": "المجاني",
            "description": "الخطة الأساسية المجانية",
            "features": [],
            "is_free": True,
            "is_active": True,
            "display_order": 0,
            "max_accounts": 1,
            "max_groups": 3,
            "max_messages": 1,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0002_subscriptionplan_subscription_expired_at_and_more")]
    operations = [migrations.RunPython(create_free_plan, migrations.RunPython.noop)]
