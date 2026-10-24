from django.db import models


class TelegramGroup(models.Model):
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="telegram_groups")
    identifier = models.CharField(max_length=255)
    canonical_identifier = models.CharField(max_length=255)
    title = models.CharField(max_length=255, blank=True)
    is_selected = models.BooleanField(default=True)
    can_publish = models.BooleanField(default=False)
    status = models.CharField(max_length=30, default="pending")
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "canonical_identifier"], name="unique_user_group_identifier"),
        ]

    def __str__(self) -> str:
        return self.title or self.identifier
