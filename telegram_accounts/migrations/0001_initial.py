from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [("users", "0002_user_is_publishing")]
    operations = [
        migrations.CreateModel(
            name="TelegramAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telegram_user_id", models.BigIntegerField(blank=True, null=True)),
                ("phone_number", models.CharField(blank=True, max_length=30)),
                ("username", models.CharField(blank=True, max_length=150)),
                ("encrypted_session", models.TextField(blank=True)),
                ("status", models.CharField(default="disconnected", max_length=30)),
                ("last_error", models.TextField(blank=True)),
                ("last_connected_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="telegram_accounts", to="users.user")),
            ],
        ),
    ]
