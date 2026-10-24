from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("publishing", "0003_work_sleep_hours")]

    operations = [
        migrations.AddField(
            model_name="publishingsettings",
            name="cycle_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
