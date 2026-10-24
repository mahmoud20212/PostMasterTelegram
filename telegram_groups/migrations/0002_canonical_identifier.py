from django.db import migrations, models


def populate_canonical_identifiers(apps, schema_editor):
    TelegramGroup = apps.get_model("telegram_groups", "TelegramGroup")
    seen = set()
    for group in TelegramGroup.objects.order_by("id"):
        value = (group.identifier or "").strip()
        lowered = value.lower()
        if lowered.startswith("https://t.me/"):
            value = value[13:]
        elif lowered.startswith("http://t.me/"):
            value = value[12:]
        elif lowered.startswith("t.me/"):
            value = value[5:]
        value = value.strip("/@").lower()
        canonical = f"id:{value}" if value.lstrip("-").isdigit() else f"username:{value}"
        key = (group.user_id, canonical)
        if key in seen:
            canonical = f"legacy:{group.pk}"
        seen.add(key)
        TelegramGroup.objects.filter(pk=group.pk).update(canonical_identifier=canonical)


class Migration(migrations.Migration):
    dependencies = [("telegram_groups", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="telegramgroup",
            name="canonical_identifier",
            field=models.CharField(max_length=255, default=""),
            preserve_default=False,
        ),
        migrations.RunPython(populate_canonical_identifiers, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="telegramgroup",
            constraint=models.UniqueConstraint(
                fields=("user", "canonical_identifier"),
                name="unique_user_group_identifier",
            ),
        ),
    ]
