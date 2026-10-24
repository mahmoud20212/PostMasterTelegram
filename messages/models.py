from django.db import models


class SavedMessage(models.Model):
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="saved_messages")
    text = models.TextField()
    entities = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.text[:50]
