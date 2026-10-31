from django.contrib import admin

from subscriptions.models import PaymentTransaction, PlanDuration, Subscription, SubscriptionPlan
from subscriptions.services.subscriptions import _activate_paid_subscription_locked
from django.db import transaction
from django.utils import timezone


class PlanDurationInline(admin.TabularInline):
    model = PlanDuration
    extra = 0


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_free", "is_active", "min_interval_seconds", "max_interval_seconds", "daily_send_limit", "cycle_rest_seconds")
    list_filter = ("is_free", "is_active")
    search_fields = ("name", "code")
    inlines = [PlanDurationInline]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan_definition", "status", "start_date", "end_date", "expired_at")
    list_filter = ("status", "plan_definition")
    search_fields = ("user__telegram_user_id", "user__username")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "duration", "amount", "currency", "status", "created_at", "paid_at")
    list_filter = ("status", "payment_method", "currency")
    search_fields = ("user__telegram_user_id", "invoice_id", "provider_payment_id")
    readonly_fields = ("created_at", "paid_at")
    actions = ("mark_manual_paid",)

    @admin.action(description="Activate selected manual payments")
    def mark_manual_paid(self, request, queryset):
        activated = 0
        for payment in queryset.select_related("user", "duration"):
            if payment.payment_method != "manual" or payment.status != PaymentTransaction.PENDING:
                continue
            with transaction.atomic():
                locked = PaymentTransaction.objects.select_for_update().select_related("user", "duration").get(pk=payment.pk)
                if locked.status != PaymentTransaction.PENDING or locked.payment_method != "manual":
                    continue
                locked.status = PaymentTransaction.PAID
                locked.paid_at = timezone.now()
                locked.save(update_fields=["status", "paid_at"])
                _activate_paid_subscription_locked(user=locked.user, duration=locked.duration, event_key=f"payment:{locked.pk}")
                activated += 1
        self.message_user(request, f"Activated {activated} manual payment(s).")
