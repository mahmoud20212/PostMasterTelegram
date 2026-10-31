import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from asgiref.sync import SyncToAsync
from django.contrib import admin
from django.db import OperationalError, connections
from django.test import TransactionTestCase

from bot.handlers.subscription import demo_payment, process_pre_checkout, process_successful_payment, show_subscription
from bot.keyboards.subscription import manual_payment_keyboard, manual_payment_support_url
from bot.ui.manual_payment import manual_payment_text
from notifications.models import BotNotification
from subscriptions.admin import PaymentTransactionAdmin
from subscriptions.models import PaymentTransaction, PlanDuration, Subscription, SubscriptionPlan
from subscriptions.services.subscriptions import activate_manual_payment, create_pending_payment, get_subscription
from users.models import User


class ManualPaymentTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create(telegram_user_id=91001)
        self.plan = SubscriptionPlan.objects.create(code="manual-pro", name="🚀 Pro <VIP> & Co", max_groups=20)
        self.duration = PlanDuration.objects.create(plan=self.plan, label="أسبوع", days=7,
            price=Decimal("1.00"), currency="USD", payment_method="manual")
        self.stars = PlanDuration.objects.create(plan=self.plan, label="7 days", days=7,
            price=50, currency="XTR", payment_method="telegram_stars")
        self.free = asyncio.run(get_subscription(telegram_user_id=self.user.telegram_user_id))

    def tearDown(self):
        SyncToAsync.single_thread_executor.submit(connections.close_all).result(timeout=10)

    def order(self, duration=None, user=None, method="manual"):
        return asyncio.run(create_pending_payment(telegram_user_id=(user or self.user).telegram_user_id,
            duration_id=(duration or self.duration).pk, payment_method=method))

    def callback(self, language="en", method="manual", duration=None):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=self.user.telegram_user_id, language_code=language),
            data=f"subscription:pay:{method}:{(duration or self.duration).pk}",
            message=SimpleNamespace(answer=AsyncMock(), answer_invoice=AsyncMock(return_value=SimpleNamespace(message_id=44)), edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

    def test_new_manual_order_is_pending_and_does_not_activate_subscription(self):
        payment = self.order()
        self.assertEqual((payment.status, payment.payment_method, payment.amount, payment.currency),
                         ("pending", "manual", Decimal("1.00"), "USD"))
        self.assertEqual(payment.plan_id, self.plan.pk)
        self.assertEqual(payment.duration_id, self.duration.pk)
        self.assertEqual(Subscription.objects.get(user=self.user).plan_definition_id, self.free.plan_definition_id)
        self.assertFalse(BotNotification.objects.exists())

    def test_repeated_requests_reuse_pending_order_and_original_quoted_amount(self):
        first = self.order()
        self.duration.price = Decimal("9.99")
        self.duration.save(update_fields=["price"])
        second = self.order()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertEqual(second.amount, Decimal("1.00"))
        self.assertIn("1.00$", manual_payment_text(second, "en"))
        self.assertNotIn("9.99$", manual_payment_text(second, "en"))

    def test_legacy_duplicate_pending_orders_reuse_oldest_without_deleting_history(self):
        first = self.order()
        duplicate = PaymentTransaction.objects.create(user=self.user, plan=self.plan, duration=self.duration,
            payment_method="manual", amount=2, currency="USD", status="pending")
        self.assertEqual(self.order().pk, first.pk)
        self.assertTrue(PaymentTransaction.objects.filter(pk=duplicate.pk, status="pending").exists())
        self.assertEqual(PaymentTransaction.objects.count(), 2)

    def test_different_users_and_durations_do_not_share_orders(self):
        first = self.order()
        other = User.objects.create(telegram_user_id=91002)
        second = self.order(user=other)
        longer = PlanDuration.objects.create(plan=self.plan, label="month", days=30, price=3, currency="USD", payment_method="manual")
        third = self.order(duration=longer)
        self.assertEqual(len({first.pk, second.pk, third.pk}), 3)

    def test_terminal_orders_allow_new_order_without_activating(self):
        for status in ("paid", "failed", "canceled", "rejected"):
            with self.subTest(status=status):
                old = self.order()
                PaymentTransaction.objects.filter(pk=old.pk).update(status=status)
                new = self.order()
                self.assertNotEqual(old.pk, new.pk)
                self.assertEqual(new.status, "pending")
        self.assertEqual(Subscription.objects.get(user=self.user).plan_definition_id, self.free.plan_definition_id)

    def test_manual_request_cannot_use_stars_or_inactive_duration(self):
        with self.assertRaises(PlanDuration.DoesNotExist):
            self.order(duration=self.stars)
        self.duration.is_active = False
        self.duration.save(update_fields=["is_active"])
        with self.assertRaises(PlanDuration.DoesNotExist):
            self.order()
        self.assertEqual(PaymentTransaction.objects.count(), 0)

    def test_arabic_only_html_escapes_dynamic_values_for_every_language(self):
        payment = self.order()
        with self.assertNumQueries(0):
            english = manual_payment_text(payment, "en-US")
            arabic = manual_payment_text(payment, "ar-PS")
        self.assertEqual(english, arabic)
        self.assertIn("<b>طلب الاشتراك</b>", english)
        self.assertIn("7 أيام", english)
        self.assertIn("1.00$", english)
        self.assertIn(f"<code>#{payment.pk}</code>", english)
        self.assertIn("⏳ بانتظار الدفع", english)
        self.assertIn("&lt;VIP&gt; &amp; Co", english)
        self.assertNotIn("<VIP>", english)
        self.assertIn("7 أيام", arabic)
        self.assertIn("بانتظار الدفع", arabic)
        self.assertEqual(manual_payment_text(payment, None), arabic)
        self.assertEqual(manual_payment_text(payment, "fr"), arabic)
        payment.status = "<unsafe>"
        payment.currency = "<USD>"
        escaped = manual_payment_text(payment, "en")
        self.assertIn("&lt;unsafe&gt;", escaped)
        self.assertIn("&lt;USD&gt;", escaped)

    def test_day_count_and_status_come_from_database(self):
        payment = self.order()
        for days, arabic in ((1, "يوم واحد"), (2, "يومان"), (30, "30 يومًا")):
            payment.duration.days = days
            self.assertIn(arabic, manual_payment_text(payment, "en"))
        payment.status = "paid"
        self.assertIn("✅ مدفوع", manual_payment_text(payment, "en"))
        self.assertNotIn("بانتظار الدفع", manual_payment_text(payment, "en"))
        self.assertNotIn("للحصول على تفاصيل الدفع", manual_payment_text(payment))

    def test_keyboard_opens_existing_support_contact_with_encoded_order_draft(self):
        payment = self.order()
        for language in ("ar", "en"):
            with self.subTest(language=language), patch("bot.keyboards.main.DEVELOPER_URL", "https://t.me/ExampleSupport?profile&text=old"), self.assertNumQueries(0):
                keyboard = manual_payment_keyboard(payment.pk, language)
            support, back = [row[0] for row in keyboard.inline_keyboard]
            url = urlsplit(support.url)
            self.assertEqual((url.netloc, url.path), ("t.me", "/ExampleSupport"))
            self.assertIn(f"#{payment.pk}", parse_qs(url.query)["text"][0])
            self.assertNotIn("profile", parse_qs(url.query, keep_blank_values=True))
            self.assertIsNone(support.callback_data)
            self.assertEqual(back.callback_data, "subscription:details")
            self.assertEqual(support.text, "💬 إتمام الدفع عبر الدعم")
        payment.refresh_from_db()
        self.assertEqual(payment.status, "pending")
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_support_url_handles_username_and_tg_links_and_preserves_external_urls(self):
        for configured in ("@ExampleSupport", "tg://resolve?domain=ExampleSupport"):
            with self.subTest(configured=configured), patch("bot.keyboards.main.DEVELOPER_URL", configured):
                self.assertIn("#123", parse_qs(urlsplit(manual_payment_support_url(123, "en")).query)["text"][0])
        for configured in ("https://support.example/help", "https://t.me/+invite_code", "https://t.me/m/business_slug"):
            with self.subTest(configured=configured), patch("bot.keyboards.main.DEVELOPER_URL", configured):
                self.assertEqual(manual_payment_support_url(123), configured)

    def test_handler_reopening_shows_same_order_in_html_without_activating(self):
        for language in ("en", "ar"):
            callback = self.callback(language)
            asyncio.run(demo_payment(callback))
            args = callback.message.edit_text.await_args
            self.assertEqual(args.kwargs["parse_mode"], "HTML")
            self.assertIn("بانتظار الدفع", args.args[0])
            callback.message.answer.assert_not_awaited()
            callback.message.answer_invoice.assert_not_awaited()
            callback.answer.assert_awaited_once_with()
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertFalse(BotNotification.objects.exists())
        self.assertEqual(Subscription.objects.get(user=self.user).plan_definition_id, self.free.plan_definition_id)

    def test_back_navigation_returns_to_subscription_without_new_order(self):
        payment = self.order()
        callback = self.callback()
        callback.data = manual_payment_keyboard(payment.pk, "en").inline_keyboard[1][0].callback_data
        asyncio.run(show_subscription(callback))
        callback.message.edit_text.assert_awaited_once()
        self.assertEqual(callback.data, "subscription:details")
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "pending")

    def test_invalid_payment_callback_never_reaches_demo_activation(self):
        for data in (f"subscription:pay:crypto:{self.duration.pk}", "subscription:pay:manual:invalid", "subscription:pay:manual"):
            callback = self.callback()
            callback.data = data
            asyncio.run(demo_payment(callback))
            self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
            callback.message.answer.assert_not_awaited()
        self.assertEqual(PaymentTransaction.objects.count(), 0)
        self.assertFalse(BotNotification.objects.exists())

    def test_stale_manual_option_is_reported_without_creating_order(self):
        self.duration.is_active = False
        self.duration.save(update_fields=["is_active"])
        callback = self.callback()
        asyncio.run(demo_payment(callback))
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_existing_admin_action_confirms_payment_and_activates_exactly_once(self):
        payment = self.order()
        approval = PaymentTransactionAdmin(PaymentTransaction, admin.site)
        with patch.object(approval, "message_user"):
            approval.mark_manual_paid(SimpleNamespace(), PaymentTransaction.objects.filter(pk=payment.pk))
            first_end = Subscription.objects.get(user=self.user).end_date
            approval.mark_manual_paid(SimpleNamespace(), PaymentTransaction.objects.filter(pk=payment.pk))
        payment.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(Subscription.objects.get(user=self.user).plan_definition_id, self.plan.pk)
        self.assertEqual(Subscription.objects.get(user=self.user).end_date, first_end)
        self.assertEqual(BotNotification.objects.filter(event_key=f"payment:{payment.pk}").count(), 1)

    def test_admin_cannot_activate_failed_manual_order(self):
        payment = self.order()
        PaymentTransaction.objects.filter(pk=payment.pk).update(status="failed")
        with self.assertRaises(ValueError):
            asyncio.run(activate_manual_payment(payment_id=payment.pk))
        self.assertEqual(Subscription.objects.get(user=self.user).plan_definition_id, self.free.plan_definition_id)

    def test_stars_orders_and_invoices_keep_existing_behavior(self):
        manual = self.order()
        for _ in range(2):
            callback = self.callback(method="telegram_stars", duration=self.stars)
            asyncio.run(demo_payment(callback))
            invoice = callback.message.answer_invoice.await_args.kwargs
            self.assertEqual(invoice["currency"], "XTR")
            self.assertEqual(invoice["prices"][0].amount, 50)
            callback.message.answer.assert_not_awaited()
        self.assertEqual(PaymentTransaction.objects.filter(payment_method="telegram_stars", status="pending").count(), 2)
        manual.refresh_from_db()
        self.assertEqual(manual.status, "pending")

    def test_stars_precheckout_and_successful_payment_still_activate(self):
        payment = self.order(duration=self.stars, method="telegram_stars")
        query = SimpleNamespace(invoice_payload=payment.invoice_payload, from_user=SimpleNamespace(id=self.user.telegram_user_id),
            total_amount=50, currency="XTR", answer=AsyncMock())
        asyncio.run(process_pre_checkout(query))
        query.answer.assert_awaited_once_with(ok=True)
        message = SimpleNamespace(from_user=SimpleNamespace(id=self.user.telegram_user_id), answer=AsyncMock(),
            successful_payment=SimpleNamespace(invoice_payload=payment.invoice_payload, telegram_payment_charge_id="mock-stars-charge"))
        asyncio.run(process_successful_payment(message))
        payment.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.telegram_payment_charge_id, "mock-stars-charge")
        self.assertEqual(Subscription.objects.get(user=self.user).plan_definition_id, self.plan.pk)
        self.assertEqual(BotNotification.objects.filter(event_key=f"payment:{payment.pk}").count(), 1)

    def test_concurrent_manual_requests_reuse_one_pending_order(self):
        def request(_):
            try:
                return create_pending_payment.__wrapped__(telegram_user_id=self.user.telegram_user_id,
                    duration_id=self.duration.pk, payment_method="manual").pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(request, range(12)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(PaymentTransaction.objects.filter(payment_method="manual", status="pending").count(), 1)

    def test_contention_after_insert_rolls_back_before_retry_without_duplicate_order(self):
        create = PaymentTransaction.objects.create
        first = True

        def interrupted_insert(**kwargs):
            nonlocal first
            payment = create(**kwargs)
            if first:
                first = False
                raise OperationalError("database table is locked")
            return payment

        with patch("subscriptions.services.subscriptions.PaymentTransaction.objects.create", side_effect=interrupted_insert), \
             patch("subscriptions.services.subscriptions.is_sqlite_contention", return_value=True), \
             patch("subscriptions.services.subscriptions.time.sleep"):
            payment = self.order()
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertEqual(PaymentTransaction.objects.get().pk, payment.pk)
        self.assertEqual(payment.status, "pending")
        self.assertFalse(BotNotification.objects.exists())
