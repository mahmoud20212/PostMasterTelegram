from datetime import timedelta

from asgiref.sync import sync_to_async
from django.test import TestCase
from django.utils import timezone

from messages.models import SavedMessage
from messages.services.messages import create_message
from subscriptions.models import PlanDuration, Subscription, SubscriptionPlan
from subscriptions.services.subscriptions import (
    SubscriptionLimitExceeded,
    converted_remaining_days,
    activate_paid_subscription,
    complete_demo_payment,
    get_subscription,
)
from telegram_groups.models import TelegramGroup
from users.models import User


class SubscriptionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(telegram_user_id=9001, current_plan="free")

    async def test_new_user_gets_default_free_plan_and_limits(self):
        subscription = await get_subscription(telegram_user_id=9001)

        self.assertEqual(subscription.plan_definition.code, "free")
        self.assertEqual(subscription.limits_snapshot, {"accounts": 1, "groups": 3, "messages": 1})

    async def test_free_plan_limits_message_creation(self):
        await create_message(telegram_user_id=9001, text="one")

        with self.assertRaises(SubscriptionLimitExceeded):
            await create_message(telegram_user_id=9001, text="two")

    async def test_expired_paid_subscription_deletes_groups_and_messages_but_keeps_accounts(self):
        free = await sync_to_async(SubscriptionPlan.objects.get)(code="free")
        paid = await sync_to_async(SubscriptionPlan.objects.create)(
            code="pro", name="Pro", max_accounts=None, max_groups=20, max_messages=20
        )
        await sync_to_async(Subscription.objects.create)(
            user=self.user,
            plan="pro",
            plan_definition=paid,
            status="active",
            start_date=timezone.now() - timedelta(days=31),
            end_date=timezone.now() - timedelta(days=1),
            limits_snapshot={"accounts": None, "groups": 20, "messages": 20},
        )
        await sync_to_async(TelegramGroup.objects.create)(
            user=self.user, identifier="@sample", canonical_identifier="username:sample"
        )
        await sync_to_async(SavedMessage.objects.create)(user=self.user, text="old")

        subscription = await get_subscription(telegram_user_id=9001)

        self.assertEqual(subscription.plan_definition_id, free.id)
        self.assertEqual(await sync_to_async(TelegramGroup.objects.filter(user=self.user).count)(), 0)
        self.assertEqual(await sync_to_async(SavedMessage.objects.filter(user=self.user).count)(), 0)

    def test_converts_remaining_value_to_new_plan_days(self):
        old = PlanDuration(plan_id=1, label="30 days", days=30, price=300)
        new = PlanDuration(plan_id=2, label="30 days", days=30, price=600)

        self.assertEqual(converted_remaining_days(remaining_days=15, old_duration=old, new_duration=new), 7)

    async def test_demo_payment_marks_paid_and_activates_subscription(self):
        plan = await sync_to_async(SubscriptionPlan.objects.create)(
            code="demo", name="Demo", max_accounts=2, max_groups=5, max_messages=5
        )
        duration = await sync_to_async(PlanDuration.objects.create)(
            plan=plan, label="يوم", days=1, price=20
        )

        payment, subscription = await complete_demo_payment(
            telegram_user_id=9001,
            duration_id=duration.id,
            payment_method="telegram_stars",
        )

        self.assertEqual(payment.status, payment.PAID)
        self.assertEqual(subscription.plan_definition_id, plan.id)

    async def test_same_plan_purchase_extends_existing_end_date(self):
        plan = await sync_to_async(SubscriptionPlan.objects.create)(
            code="basic", name="Basic", max_accounts=2, max_groups=5, max_messages=5
        )
        duration = await sync_to_async(PlanDuration.objects.create)(
            plan=plan, label="30 يوم", days=30, price=300
        )
        first = await activate_paid_subscription(telegram_user_id=9001, duration_id=duration.id)
        second = await activate_paid_subscription(telegram_user_id=9001, duration_id=duration.id)

        self.assertGreaterEqual((second.end_date - first.end_date).days, 29)

    async def test_different_plan_purchase_converts_remaining_value(self):
        old_plan = await sync_to_async(SubscriptionPlan.objects.create)(
            code="pro", name="Pro", max_accounts=5, max_groups=20, max_messages=20
        )
        new_plan = await sync_to_async(SubscriptionPlan.objects.create)(
            code="vip", name="VIP", max_accounts=None, max_groups=None, max_messages=None
        )
        old_duration = await sync_to_async(PlanDuration.objects.create)(
            plan=old_plan, label="30 يوم", days=30, price=300
        )
        new_duration = await sync_to_async(PlanDuration.objects.create)(
            plan=new_plan, label="يوم", days=1, price=20
        )
        await activate_paid_subscription(telegram_user_id=9001, duration_id=old_duration.id)
        subscription = await sync_to_async(Subscription.objects.get)(user=self.user)
        subscription.end_date = timezone.now() + timedelta(days=15)
        await sync_to_async(subscription.save)(update_fields=["end_date"])

        upgraded = await activate_paid_subscription(telegram_user_id=9001, duration_id=new_duration.id)

        self.assertEqual(upgraded.plan_definition_id, new_plan.id)
        self.assertGreaterEqual((upgraded.end_date - timezone.now()).days, 7)
        self.assertLess((upgraded.end_date - timezone.now()).days, 9)
