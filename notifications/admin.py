from django.contrib import admin

from notifications.models import BotNotification


@admin.register(BotNotification)
class BotNotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "status", "created_at", "finished_at", "error_type")
    list_filter = ("kind", "status")
    readonly_fields = tuple(field.name for field in BotNotification._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
