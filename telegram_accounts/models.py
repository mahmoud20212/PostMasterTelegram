from django.db import models


class TelegramAccount(models.Model):
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="telegram_accounts")
    telegram_user_id = models.BigIntegerField(null=True, blank=True)
    phone_number = models.CharField(max_length=30, unique=True)
    username = models.CharField(max_length=150, blank=True)
    encrypted_session = models.TextField(blank=True)
    status = models.CharField(max_length=30, default="disconnected")
    last_error = models.TextField(blank=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)
    is_publishing_account = models.BooleanField(default=False)
    paused_until = models.DateTimeField(null=True, blank=True)
    restriction_type = models.CharField(max_length=50, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)

