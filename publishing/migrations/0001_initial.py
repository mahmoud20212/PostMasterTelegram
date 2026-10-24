from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [("users", "0002_user_is_publishing")]
    operations = [
        migrations.CreateModel(
            name="PublishingSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("interval_seconds", models.PositiveIntegerField(default=15)),
                ("distribution_mode", models.CharField(default="round_robin", max_length=30)),
                ("timezone", models.CharField(default="UTC", max_length=64)),
                ("is_running", models.BooleanField(default=False)),
                ("sleep_enabled", models.BooleanField(default=False)),
                ("sleep_start", models.TimeField(blank=True, null=True)),
                ("sleep_end", models.TimeField(blank=True, null=True)),
                ("current_message_index", models.PositiveIntegerField(default=0)),
                ("current_group_index", models.PositiveIntegerField(default=0)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="publishing_settings", to="users.user")),
            ],
        ),
    ]
