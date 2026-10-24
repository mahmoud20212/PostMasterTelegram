from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("publishing", "0002_cycle_duration")]
    operations = [
        migrations.AddField(model_name="publishingsettings", name="work_hours", field=models.PositiveIntegerField(default=16)),
        migrations.AddField(model_name="publishingsettings", name="sleep_hours", field=models.PositiveIntegerField(default=8)),
        migrations.AlterField(model_name="publishingsettings", name="timezone", field=models.CharField(default="Asia/Gaza", max_length=64)),
    ]
