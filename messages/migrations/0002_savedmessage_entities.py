from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saved_messages", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="savedmessage",
            name="entities",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
