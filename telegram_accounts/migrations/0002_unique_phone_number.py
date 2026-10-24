from django.db import migrations, models


def remove_duplicate_pending_accounts(apps, schema_editor):
    TelegramAccount = apps.get_model("telegram_accounts", "TelegramAccount")
    seen = set()
    for account in TelegramAccount.objects.order_by("id"):
        phone = (account.phone_number or "").strip()
        if not phone or phone in seen:
            if account.status != "connected":
                account.delete()
                continue
        seen.add(phone)


class Migration(migrations.Migration):
    dependencies = [("telegram_accounts", "0001_initial")]
    operations = [
        migrations.RunPython(remove_duplicate_pending_accounts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="telegramaccount",
            name="phone_number",
            field=models.CharField(max_length=30, unique=True),
        ),
    ]
