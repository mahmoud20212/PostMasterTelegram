from django.contrib import admin

from publishing.models import PublishLog, PublishingSettings


@admin.register(PublishingSettings)
class PublishingSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "cycle_duration_minutes", "next_cycle_at", "is_running")
    search_fields = ("user__telegram_user_id", "user__username")


@admin.register(PublishLog)
class PublishLogAdmin(admin.ModelAdmin):
    list_display = ("attempted_at", "user", "account", "group", "status", "error_type")
    list_filter = ("status", "error_type")
    search_fields = ("user__telegram_user_id", "group__identifier", "error_message")
    readonly_fields = ("attempted_at",)
