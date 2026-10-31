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
    # Version 1 rows are intentionally not adopted while old workers may exist.
    scheduler_version = models.PositiveSmallIntegerField(default=1)
    next_due_at = models.DateTimeField(null=True, blank=True)
    last_dispatched_at = models.DateTimeField(null=True, blank=True)
    dispatch_token = models.UUIDField(null=True, blank=True)
    dispatch_expires_at = models.DateTimeField(null=True, blank=True)
    dispatch_error = models.CharField(max_length=100, blank=True)
    cycle_plan = models.JSONField(default=list, blank=True)
    cycle_position = models.PositiveIntegerField(default=0)
    cycle_deadline = models.DateTimeField(null=True, blank=True)
    next_sequence = models.PositiveBigIntegerField(default=0)
    work_rest_phase = models.CharField(max_length=10, default="off")
    phase_started_at = models.DateTimeField(null=True, blank=True)
    phase_ends_at = models.DateTimeField(null=True, blank=True)
    phase_version = models.PositiveBigIntegerField(default=0)
    cycle_resume_pending = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user"], condition=models.Q(active=True), name="one_active_publishing_run"),
        ]
        indexes = [models.Index(fields=["active", "scheduler_version", "next_due_at"], name="publishing_due_idx")]


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


class PublishingAttempt(models.Model):
    """One item, consumed once even when its network outcome is unknown."""

    OPEN_STATUSES = ("claimed", "sending")
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(PublishingRun, on_delete=models.CASCADE, related_name="attempts")
    user = models.ForeignKey("users.User", on_delete=models.CASCADE)
    account = models.ForeignKey("telegram_accounts.TelegramAccount", on_delete=models.SET_NULL, null=True)
    group = models.ForeignKey("telegram_groups.TelegramGroup", on_delete=models.SET_NULL, null=True)
    message = models.ForeignKey("saved_messages.SavedMessage", on_delete=models.SET_NULL, null=True)
    position = models.PositiveIntegerField()
    sequence = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, default="claimed")
    started_at = models.DateTimeField(auto_now_add=True)
    sending_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    target_identifier = models.CharField(max_length=255)
    message_text = models.TextField()
    message_entities = models.JSONField(default=list)
    session_fingerprint = models.CharField(max_length=64)
    next_message_index = models.PositiveIntegerField()
    next_group_index = models.PositiveIntegerField()
    interval_seconds = models.PositiveIntegerField()
    cycle_rest_seconds = models.PositiveIntegerField()
    quota_reserved = models.BooleanField(default=False)
    quota_reset_at = models.DateTimeField(null=True, blank=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    error_type = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "sequence"], name="one_attempt_per_run_sequence"),
            models.UniqueConstraint(fields=["user"], condition=models.Q(status__in=["claimed", "sending"]), name="one_open_attempt_per_user"),
        ]
        indexes = [models.Index(fields=["status", "expires_at"], name="publishing_attempt_exp_idx")]


class PublishingCooldown(models.Model):
    account = models.ForeignKey("telegram_accounts.TelegramAccount", on_delete=models.CASCADE)
    group = models.ForeignKey("telegram_groups.TelegramGroup", on_delete=models.CASCADE)
    until = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "group"], name="one_account_group_cooldown")]


class PublishLog(models.Model):
    STATUS_CHOICES = [("success", "Success"), ("failed", "Failed"), ("unknown", "Unknown"), ("skipped", "Skipped")]

    attempt = models.OneToOneField(PublishingAttempt, on_delete=models.SET_NULL, null=True, blank=True)

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="publish_logs")
    account = models.ForeignKey("telegram_accounts.TelegramAccount", on_delete=models.SET_NULL, null=True, blank=True)
    group = models.ForeignKey("telegram_groups.TelegramGroup", on_delete=models.SET_NULL, null=True, blank=True)
    message = models.ForeignKey("saved_messages.SavedMessage", on_delete=models.SET_NULL, null=True, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error_type = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    restriction_seconds = models.PositiveIntegerField(null=True, blank=True)

