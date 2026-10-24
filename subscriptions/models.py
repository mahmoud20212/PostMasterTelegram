from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class SubscriptionPlan(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    features = models.JSONField(default=list, blank=True)
    is_free = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    max_accounts = models.PositiveIntegerField(null=True, blank=True)
    max_groups = models.PositiveIntegerField(null=True, blank=True)
    max_messages = models.PositiveIntegerField(null=True, blank=True)
    min_interval_seconds = models.PositiveIntegerField(default=30)
    max_interval_seconds = models.PositiveIntegerField(default=60)
    daily_send_limit = models.PositiveIntegerField(null=True, blank=True)
    cycle_rest_seconds = models.PositiveIntegerField(default=3600)

    def clean(self):
        if self.min_interval_seconds < 30:
            raise ValidationError({"min_interval_seconds": "Minimum interval is 30 seconds."})
        if self.max_interval_seconds < self.min_interval_seconds:
            raise ValidationError({"max_interval_seconds": "Maximum interval must not be below minimum."})

    class Meta:
        ordering = ["display_order", "id"]

    def __str__(self) -> str:
        return self.name


class PlanDuration(models.Model):
    CURRENCY_CHOICES = [("XTR", "Telegram Stars"), ("USD", "US Dollar")]
    PAYMENT_METHOD_CHOICES = [("telegram_stars", "Telegram Stars"), ("manual", "Manual payment")]
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE, related_name="durations")
    label = models.CharField(max_length=50)
    days = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, choices=CURRENCY_CHOICES, default="XTR")
    payment_method = models.CharField(max_length=50, choices=PAYMENT_METHOD_CHOICES, default="telegram_stars")
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "days", "id"]

    def clean(self):
        if self.price <= Decimal("0"):
            raise ValidationError({"price": "Price must be greater than zero."})
        if self.currency == "XTR" and self.price != self.price.to_integral_value():
            raise ValidationError({"price": "Telegram Stars price must be a whole number."})
        if self.payment_method == "telegram_stars" and self.currency != "XTR":
            raise ValidationError({"currency": "Telegram Stars require XTR currency."})
        if self.payment_method == "manual" and self.currency != "USD":
            raise ValidationError({"currency": "Manual payment requires USD currency."})

    def __str__(self) -> str:
        return f"{self.plan.name} - {self.label}"


class Subscription(models.Model):
    user = models.OneToOneField("users.User", on_delete=models.CASCADE, related_name="subscription")
    plan = models.CharField(max_length=50, default="free")
    plan_definition = models.ForeignKey(SubscriptionPlan, null=True, blank=True, on_delete=models.PROTECT, related_name="subscriptions")
    duration = models.ForeignKey("PlanDuration", null=True, blank=True, on_delete=models.PROTECT, related_name="active_subscriptions")
    status = models.CharField(max_length=30, default="active")
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    limits_snapshot = models.JSONField(default=dict)
    expired_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.user} - {self.plan_definition or self.plan}"


class PaymentTransaction(models.Model):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    STATUS_CHOICES = [(PENDING, "Pending"), (PAID, "Paid"), (FAILED, "Failed")]
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="payment_transactions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="payments")
    duration = models.ForeignKey(PlanDuration, on_delete=models.PROTECT, related_name="payments")
    payment_method = models.CharField(max_length=50)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10)
    invoice_payload = models.CharField(max_length=255, unique=True, null=True, blank=True)
    invoice_id = models.CharField(max_length=255, blank=True)
    provider_payment_id = models.CharField(max_length=255, blank=True)
    telegram_payment_charge_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user} - {self.plan} - {self.status}"
