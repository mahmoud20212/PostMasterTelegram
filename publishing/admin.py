from django.contrib import admin

from publishing.models import PublishLog, PublishingSettings, PublishingRun, PublishingAttempt


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


@admin.register(PublishingRun)
class PublishingRunAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "active", "scheduler_version", "next_due_at", "last_dispatched_at", "dispatch_error")
    list_filter = ("active", "scheduler_version")
    readonly_fields = tuple(field.name for field in PublishingRun._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PublishingAttempt)
class PublishingAttemptAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "run", "sequence", "status", "started_at", "finished_at", "error_type")
    list_filter = ("status", "error_type")
    # Do not expose saved content snapshots in the admin list or debug logs.
    exclude = ("message_text", "message_entities", "session_fingerprint")
    readonly_fields = tuple(field.name for field in PublishingAttempt._meta.fields
                            if field.name not in ("message_text", "message_entities", "session_fingerprint"))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
