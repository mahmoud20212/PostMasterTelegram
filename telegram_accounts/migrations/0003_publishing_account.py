from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("telegram_accounts", "0002_unique_phone_number")]

    operations = [
        migrations.AddField(
            model_name="telegramaccount",
            name="is_publishing_account",
            field=models.BooleanField(default=False),
        ),
    ]
