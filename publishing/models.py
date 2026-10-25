import uuid

from django.db import models


class PublishingRun(models.Model):
    """Persistent generation; a stopped run is never reused."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="publishing_runs")
    account = models.ForeignKey("telegram_accounts.TelegramAccount", null=True, on_delete=models.SET_NULL)
    active = models.BooleanField(default=True)
    task_claimed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    stopped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user"], condition=models.Q(active=True), name="one_active_publishing_run"),
        ]


class PublishingSettings(models.Model):
    user = models.OneToOneField("users.User", on_delete=models.CASCADE, related_name="publishing_settings")
    interval_seconds = models.PositiveIntegerField(default=15)
    cycle_duration_minutes = models.PositiveIntegerField(default=5)
    distribution_mode = models.CharField(max_length=30, default="round_robin")
    timezone = models.CharField(max_length=64, default="Asia/Gaza")
    is_running = models.BooleanField(default=False)
    sleep_enabled = models.BooleanField(default=False)
    work_hours = models.PositiveIntegerField(default=16)
    sleep_hours = models.PositiveIntegerField(default=8)
    sleep_start = models.TimeField(null=True, blank=True)
    sleep_end = models.TimeField(null=True, blank=True)
    current_message_index = models.PositiveIntegerField(default=0)
    current_group_index = models.PositiveIntegerField(default=0)
    cycle_started_at = models.DateTimeField(null=True, blank=True)
    next_cycle_at = models.DateTimeField(null=True, blank=True)


class PublishLog(models.Model):
    STATUS_CHOICES = [("success", "Success"), ("failed", "Failed")]

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="publish_logs")
    account = models.ForeignKey("telegram_accounts.TelegramAccount", on_delete=models.SET_NULL, null=True, blank=True)
    group = models.ForeignKey("telegram_groups.TelegramGroup", on_delete=models.SET_NULL, null=True, blank=True)
    message = models.ForeignKey("saved_messages.SavedMessage", on_delete=models.SET_NULL, null=True, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error_type = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    restriction_seconds = models.PositiveIntegerField(null=True, blank=True)

