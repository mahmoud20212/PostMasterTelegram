import uuid

from django.db import models


class BotNotification(models.Model):
    """Transactional event/outbox. Ambiguous deliveries are never resent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("users.User", on_delete=models.CASCADE)
    run = models.ForeignKey("publishing.PublishingRun", null=True, blank=True, on_delete=models.SET_NULL)
    event_key = models.CharField(max_length=255, unique=True)
    kind = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    phase_version = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    available_at = models.DateTimeField()
    status = models.CharField(max_length=12, default="pending")
    token = models.UUIDField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    finished_at = models.DateTimeField(null=True, blank=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    error_type = models.CharField(max_length=100, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "available_at"], name="notification_due_idx")]
