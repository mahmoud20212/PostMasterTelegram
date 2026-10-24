from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("publishing", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="publishingsettings",
            name="cycle_duration_minutes",
            field=models.PositiveIntegerField(default=5),
        ),
    ]
