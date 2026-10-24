from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [("users", "0002_user_is_publishing")]
    operations = [
        migrations.CreateModel(
            name="Subscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("plan", models.CharField(default="free", max_length=50)),
                ("status", models.CharField(default="active", max_length=30)),
                ("start_date", models.DateTimeField(blank=True, null=True)),
                ("end_date", models.DateTimeField(blank=True, null=True)),
                ("limits_snapshot", models.JSONField(default=dict)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="subscription", to="users.user")),
            ],
        ),
    ]
