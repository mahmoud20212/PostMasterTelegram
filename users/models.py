from django.db import models


class User(models.Model):
    telegram_user_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=150, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=30, default="active")
    current_plan = models.CharField(max_length=50, default="free")
    is_publishing = models.BooleanField(default=False)
    daily_send_count = models.PositiveIntegerField(default=0)
    daily_send_reset_at = models.DateTimeField(null=True, blank=True)
    daily_limit_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.username or str(self.telegram_user_id)
