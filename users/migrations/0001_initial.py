from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telegram_user_id", models.BigIntegerField(unique=True)),
                ("username", models.CharField(blank=True, max_length=150)),
                ("first_name", models.CharField(blank=True, max_length=150)),
                ("status", models.CharField(default="active", max_length=30)),
                ("current_plan", models.CharField(default="free", max_length=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_active_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
