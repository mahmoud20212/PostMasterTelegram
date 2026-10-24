from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [("users", "0002_user_is_publishing")]
    operations = [
        migrations.CreateModel(
            name="TelegramGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("identifier", models.CharField(max_length=255)),
                ("title", models.CharField(blank=True, max_length=255)),
                ("is_selected", models.BooleanField(default=True)),
                ("can_publish", models.BooleanField(default=False)),
                ("status", models.CharField(default="pending", max_length=30)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="telegram_groups", to="users.user")),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
