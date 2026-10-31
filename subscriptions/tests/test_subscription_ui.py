"""Subscription navigation with mocked Telegram, on either isolated test backend."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from asgiref.sync import SyncToAsync
from django.db import OperationalError, connections
from django.test import TransactionTestCase
from django.utils import timezone

from bot.handlers import subscription as screens
from bot.ui.manual_payment import manual_payment_text
from bot.ui.subscription import plan_text, subscription_text
from subscriptions.models import PaymentTransaction, PlanDuration, Subscription, SubscriptionPlan
from subscriptions.services.subscriptions import activate_manual_payment, create_pending_payment, get_subscription
from users.models import User


class SubscriptionJourneyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create(telegram_user_id=92001)
        self.plan = SubscriptionPlan.objects.create(code="journey-pro", name="🚀 Pro <VIP> & المحترفة")
        self.week = PlanDuration.objects.create(plan=self.plan, label="أسبوع", days=7,
            price=Decimal("1.00"), currency="USD", payment_method="manual")
        self.month = PlanDuration.objects.create(plan=self.plan, label="شهر", days=30,
            price=Decimal("3.00"), currency="USD", payment_method="manual")
        self.subscription = asyncio.run(get_subscription(telegram_user_id=self.user.telegram_user_id))

    def tearDown(self):
        SyncToAsync.single_thread_executor.submit(connections.close_all).result(timeout=10)

    def callback(self, data):
        return SimpleNamespace(data=data,
            from_user=SimpleNamespace(id=self.user.telegram_user_id, language_code="en-US"),
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(),
                answer_invoice=AsyncMock(return_value=SimpleNamespace(message_id=88))),
            answer=AsyncMock())

    def visit(self, data, *, callback=None):
        callback = callback or self.callback(data)
        callback.data = data
        handler = {
            "details": screens.show_subscription, "plans": screens.show_plans,
            "plan": screens.show_plan, "term": screens.choose_term,
            "term-method": screens.choose_method_term,
            "review": screens.review_subscription, "order": screens.show_order,
            "pay": screens.demo_payment, "duration": screens.choose_duration,
            "method": screens.choose_payment_method, "method-back": screens.back_to_payment_method,
        }[data.split(":")[1]]
        asyncio.run(handler(callback))
        callback.answer.assert_awaited_once()
        callback.message.answer.assert_not_awaited()
        return callback

    def text(self, callback):
        self.assertEqual(callback.message.edit_text.await_args.kwargs["parse_mode"], "HTML")
        return callback.message.edit_text.await_args.args[0]

    def buttons(self, callback):
        return [button for row in callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for button in row]

    def order(self, duration=None, user=None, method="manual"):
        return asyncio.run(create_pending_payment(telegram_user_id=(user or self.user).telegram_user_id,
            duration_id=(duration or self.week).pk, payment_method=method))

    def stars(self, **changes):
        values = dict(plan=self.plan, label="نجوم الأسبوع", days=7, price=50, currency="XTR", payment_method="telegram_stars")
        values.update(changes)
        return PlanDuration.objects.create(**values)

    def assert_unavailable(self, callback):
        callback.message.edit_text.assert_not_awaited()
        callback.message.answer_invoice.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    def test_single_method_journey_edits_one_message_and_creates_only_at_confirmation(self):
        current = self.visit("subscription:details")
        self.assertIn("اشتراكك الحالي", self.text(current))
        catalogue = self.visit("subscription:plans")
        plan = self.visit(self.buttons(catalogue)[0].callback_data)
        self.assertIn("&lt;VIP&gt; &amp; المحترفة", self.text(plan))
        self.assertNotIn("مميزات مخصصة", self.text(plan))
        self.assertIn("الحسابات والمجموعات والرسائل: غير محدودة", self.text(plan))
        self.assertEqual(self.text(plan).count("Pro"), 1)
        self.assertNotIn("\n\n\n", self.text(plan))
        terms = self.buttons(plan)
        self.assertIn("7 أيام · 1.00$", terms[0].text)
        review = self.visit(terms[0].callback_data)
        self.assertIn("مراجعة الاشتراك", self.text(review))
        self.assertIn("الدفع اليدوي عبر الدعم", self.text(review))
        self.assertIn("وسيلة الدفع: 💬 الدفع اليدوي عبر الدعم", self.text(plan))
        self.assertNotIn("Manual USD", self.text(review))
        self.assertFalse(any("تغيير وسيلة الدفع" in b.text for b in self.buttons(review)))
        self.assertFalse(PaymentTransaction.objects.exists())
        order = self.visit(self.buttons(review)[0].callback_data)
        self.assertIn("بانتظار الدفع", self.text(order))
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertIsNotNone(self.buttons(order)[0].url)
        self.assertEqual(self.buttons(order)[1].callback_data, "subscription:details")
        self.assertEqual(Subscription.objects.get(user=self.user).plan_definition_id, self.subscription.plan_definition_id)
        for screen in (current, catalogue, plan, review, order):
            screen.message.answer_invoice.assert_not_awaited()

    def test_multiple_methods_and_back_preserve_plan_day_and_offer(self):
        stars = self.stars()
        methods = self.visit(f"subscription:plan:{self.plan.pk}")
        self.assertIn("اختر وسيلة الدفع", self.text(methods))
        options = self.buttons(methods)
        self.assertEqual([button.text for button in options],
            ["💬 الدفع اليدوي عبر الدعم", "⭐ Telegram Stars", "⬅️ الباقات"])
        self.assertEqual(options[1].callback_data, f"subscription:method:telegram_stars:{self.plan.pk}")
        durations = self.visit(options[1].callback_data)
        self.assertEqual(len(self.buttons(durations)), 2)
        review = self.visit(self.buttons(durations)[0].callback_data)
        self.assertIn("50 نجمة", self.text(review))
        controls = self.buttons(review)
        self.assertEqual(controls[0].text, "⭐ المتابعة إلى الدفع")
        self.assertEqual(controls[1].callback_data, f"subscription:method:telegram_stars:{self.plan.pk}:7:{stars.pk}")
        selected_method = self.visit(controls[2].callback_data)
        self.assertTrue(self.buttons(selected_method)[1].text.startswith("✓ "))
        back = self.visit(controls[1].callback_data)
        self.assertIn("المدة المختارة: 7 أيام", self.text(back))
        self.assertTrue(self.buttons(back)[0].text.startswith("✓ "))
        self.assertEqual(self.buttons(back)[0].callback_data,
            f"subscription:term-method:telegram_stars:{self.plan.pk}:7:{stars.pk}")
        selected_method = self.visit(self.buttons(back)[-1].callback_data)
        self.assertTrue(self.buttons(selected_method)[1].text.startswith("✓ "))
        manual = self.visit(self.buttons(selected_method)[0].callback_data)
        self.assertTrue(self.buttons(manual)[0].text.startswith("✓ "))
        month_review = self.visit(self.buttons(manual)[1].callback_data)
        self.assertIn("30 يومًا", self.text(month_review))
        self.assertIn("3.00$", self.text(month_review))
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_disjoint_manual_and_stars_durations_start_with_method_buttons(self):
        PlanDuration.objects.filter(pk=self.month.pk).update(days=15, price=2)
        stars = self.stars(days=30, price=500)
        methods = self.visit(f"subscription:plan:{self.plan.pk}")
        manual = self.visit(self.buttons(methods)[0].callback_data)
        self.assertEqual([b.text for b in self.buttons(manual)],
            ["7 أيام · 1.00$", "15 يومًا · 2.00$", "⬅️ وسائل الدفع"])
        self.assertEqual(self.buttons(manual)[0].callback_data,
            f"subscription:term-method:manual:{self.plan.pk}:7")
        stars_durations = self.visit(self.buttons(methods)[1].callback_data)
        self.assertEqual([b.text for b in self.buttons(stars_durations)],
            ["30 يومًا · 500 نجمة", "⬅️ وسائل الدفع"])
        self.assertNotIn("$", " ".join(b.text for b in self.buttons(stars_durations)))
        review = self.visit(self.buttons(stars_durations)[0].callback_data)
        change_method = next(b for b in self.buttons(review) if b.text == "💳 تغيير وسيلة الدفع")
        methods_again = self.visit(change_method.callback_data)
        manual_again = self.visit(self.buttons(methods_again)[0].callback_data)
        self.assertNotIn("المدة المختارة", self.text(manual_again))
        self.assertFalse(any(b.text.startswith("✓ ") for b in self.buttons(manual_again)))
        manual_review = self.visit(self.buttons(manual_again)[0].callback_data)
        self.assertIn("7 أيام", self.text(manual_review))
        self.assertIn("1.00$", self.text(manual_review))
        stars_review = self.visit(f"subscription:review:{self.plan.pk}:30:{stars.pk}")
        self.assertIn("500 نجمة", self.text(stars_review))
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_method_removed_after_render_is_not_replaced_by_another_method(self):
        stars = self.stars()
        methods = self.visit(f"subscription:plan:{self.plan.pk}")
        PlanDuration.objects.filter(pk=stars.pk).update(is_active=False)
        self.assert_unavailable(self.visit(self.buttons(methods)[1].callback_data))
        self.assert_unavailable(self.visit(f"subscription:term-method:telegram_stars:{self.plan.pk}:7"))
        self.assert_unavailable(self.visit(f"subscription:review:{self.plan.pk}:7:{stars.pk}"))
        self.assert_unavailable(self.visit(f"subscription:pay:{self.plan.pk}:7:{stars.pk}"))
        current = self.visit(f"subscription:plan:{self.plan.pk}")
        self.assertIn("اختر مدة الاشتراك", self.text(current))
        self.assertNotIn("نجمة", " ".join(b.text for b in self.buttons(current)))
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_same_method_same_day_offers_are_not_dropped_and_labels_distinguish_them(self):
        special = PlanDuration.objects.create(plan=self.plan, label="عرض <خاص>", days=7,
            price=Decimal("0.75"), currency="USD", payment_method="manual")
        details = self.visit(f"subscription:plan:{self.plan.pk}")
        self.assertEqual(len(self.buttons(details)), 3)  # Two day groups and Back.
        self.assertIn("1.00$ / 0.75$", self.buttons(details)[0].text)
        methods = self.visit(self.buttons(details)[0].callback_data)
        self.assertIn("اختر عرض الاشتراك", self.text(methods))
        options = self.buttons(methods)[:-1]
        self.assertEqual(len(options), 2)
        self.assertIn("أسبوع", options[0].text)
        self.assertIn("عرض <خاص>", options[1].text)
        self.assertNotEqual(options[0].callback_data, options[1].callback_data)
        for button in options:
            review = self.visit(button.callback_data)
            self.assertIn("العرض:", self.text(review))
        self.assertIn("عرض &lt;خاص&gt;", self.text(review))
        change_offer = next(b for b in self.buttons(review) if b.text == "🏷 تغيير العرض")
        offers_again = self.visit(change_offer.callback_data)
        self.assertTrue(self.buttons(offers_again)[1].text.startswith("✓ "))
        duration_back = self.visit(self.buttons(offers_again)[-1].callback_data)
        self.assertTrue(self.buttons(duration_back)[0].text.startswith("✓ "))
        self.assertEqual(self.buttons(duration_back)[-1].callback_data, "subscription:plans")
        order = self.visit(self.buttons(review)[0].callback_data)
        payment = PaymentTransaction.objects.get()
        self.assertEqual(payment.duration_id, special.pk)
        self.assertEqual(payment.amount, Decimal("0.75"))
        self.assertIn("0.75$", self.text(order))

    def test_only_purchasable_plans_and_valid_enabled_offers_are_visible(self):
        SubscriptionPlan.objects.create(code="empty", name="No offers")
        invalid = SubscriptionPlan.objects.create(code="invalid", name="Invalid offers")
        self.stars(plan=invalid, price=Decimal("1.50"))
        self.stars(plan=invalid, currency="USD")
        self.stars(plan=invalid, days=0)
        self.stars(plan=invalid, price=0)
        self.stars(plan=invalid, payment_method="crypto")
        self.stars(is_active=False)
        free = self.subscription.plan_definition
        self.stars(plan=free)
        disabled = SubscriptionPlan.objects.create(code="disabled", name="Disabled", is_active=False)
        self.stars(plan=disabled)
        catalogue = self.visit("subscription:plans")
        self.assertEqual([b.callback_data for b in self.buttons(catalogue)],
            [f"subscription:plan:{self.plan.pk}", "subscription:details"])
        details = self.visit(f"subscription:plan:{self.plan.pk}")
        self.assertNotIn("نجمة", " ".join(b.text for b in self.buttons(details)))
        self.assertIn("مراجعة الاشتراك", self.text(self.visit(f"subscription:term:{self.plan.pk}:7")))

    def test_no_offers_shows_empty_catalogue_and_plan_without_broken_pay_buttons(self):
        PlanDuration.objects.filter(plan=self.plan).update(is_active=False)
        catalogue = self.visit("subscription:plans")
        self.assertIn("لا توجد باقات", self.text(catalogue))
        self.assertEqual(len(self.buttons(catalogue)), 1)
        details = self.visit(f"subscription:plan:{self.plan.pk}")
        self.assertIn("لا توجد مدد", self.text(details))
        self.assertEqual(len(self.buttons(details)), 1)
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_stale_offer_is_revalidated_at_every_step(self):
        PlanDuration.objects.filter(pk=self.week.pk).update(is_active=False)
        for data in (
            f"subscription:term:{self.plan.pk}:7",
            f"subscription:term-method:manual:{self.plan.pk}:7",
            f"subscription:review:{self.plan.pk}:7:{self.week.pk}",
            f"subscription:pay:{self.plan.pk}:7:{self.week.pk}",
            f"subscription:duration:manual:{self.week.pk}",
            f"subscription:pay:manual:{self.week.pk}",
        ):
            with self.subTest(data=data):
                self.assert_unavailable(self.visit(data))
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_plan_disabled_after_render_and_forged_selection_cannot_create_order(self):
        for prefix in ("review", "pay"):
            for plan_id, days in ((self.plan.pk + 100, 7), (self.plan.pk, 30)):
                self.assert_unavailable(self.visit(f"subscription:{prefix}:{plan_id}:{days}:{self.week.pk}"))
        SubscriptionPlan.objects.filter(pk=self.plan.pk).update(is_active=False)
        self.assert_unavailable(self.visit(f"subscription:pay:{self.plan.pk}:7:{self.week.pk}"))
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_invalid_offer_is_rejected_by_payment_service_too(self):
        for changes in ({"price": 0}, {"days": 0}, {"currency": "XTR"}):
            with self.subTest(changes=changes):
                PlanDuration.objects.filter(pk=self.week.pk).update(price=1, days=7, currency="USD")
                PlanDuration.objects.filter(pk=self.week.pk).update(**changes)
                with self.assertRaises((ValueError, PlanDuration.DoesNotExist)):
                    self.order()
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_renewal_and_current_plan_badge_differ_from_switching_plan(self):
        payment = self.order()
        asyncio.run(activate_manual_payment(payment_id=payment.pk))
        overview = self.visit("subscription:details")
        self.assertEqual(self.buttons(overview)[0].text, "🔄 تجديد الاشتراك")
        expiry = Subscription.objects.get(user=self.user).end_date
        self.assertIn(timezone.localtime(expiry).strftime("%d / %m / %Y"), self.text(overview))
        catalogue = self.visit("subscription:plans")
        self.assertIn("باقتك الحالية", self.buttons(catalogue)[0].text)
        details = self.visit(self.buttons(overview)[0].callback_data)
        self.assertIn("يمكنك تجديد اشتراكك", self.text(details))
        review = self.visit(self.buttons(details)[0].callback_data)
        self.assertIn("مراجعة تجديد الاشتراك", self.text(review))
        other = SubscriptionPlan.objects.create(code="other", name="Other")
        duration = self.stars(plan=other)
        switch = self.visit(f"subscription:review:{other.pk}:7:{duration.pk}")
        self.assertIn("مراجعة الاشتراك", self.text(switch))
        self.assertNotIn("تجديد", self.text(switch))
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_latest_pending_resume_is_scoped_to_owner_manual_and_pending(self):
        first = self.order()
        latest = self.order(duration=self.month)
        stars = self.stars()
        self.order(duration=stars, method="telegram_stars")
        other = User.objects.create(telegram_user_id=92002)
        other_order = self.order(user=other)
        overview = self.visit("subscription:details")
        resume = [b for b in self.buttons(overview) if ":order:" in (b.callback_data or "")]
        self.assertEqual(len(resume), 1)
        self.assertEqual(resume[0].callback_data, f"subscription:order:{latest.pk}")
        PaymentTransaction.objects.filter(pk=latest.pk).update(status="failed")
        overview = self.visit("subscription:details")
        self.assertIn(f"subscription:order:{first.pk}", [b.callback_data for b in self.buttons(overview)])
        denied = self.visit(f"subscription:order:{other_order.pk}")
        self.assert_unavailable(denied)
        self.assertNotIn(str(other_order.pk), denied.answer.await_args.args[0])
        self.assert_unavailable(self.visit(f"subscription:order:{PaymentTransaction.objects.get(payment_method='telegram_stars').pk}"))

    def test_pending_resume_keeps_original_amount_even_after_offer_disabled(self):
        payment = self.order()
        PlanDuration.objects.filter(pk=self.week.pk).update(price=9, is_active=False)
        order = self.visit(f"subscription:order:{payment.pk}")
        self.assertIn("1.00$", self.text(order))
        self.assertNotIn("9.00$", self.text(order))
        self.assertIsNotNone(self.buttons(order)[0].url)
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_paid_before_resume_displays_actual_state_without_payment_prompt(self):
        payment = self.order()
        overview = self.visit("subscription:details")
        resume = next(b for b in self.buttons(overview) if ":order:" in (b.callback_data or ""))
        asyncio.run(activate_manual_payment(payment_id=payment.pk))
        order = self.visit(resume.callback_data)
        self.assertIn("✅ مدفوع", self.text(order))
        self.assertNotIn("لإتمام اشتراكك", self.text(order))
        self.assertEqual(len(self.buttons(order)), 1)
        self.assertEqual(self.buttons(order)[0].text, "📜 عرض اشتراكي")
        self.assertIsNone(self.buttons(order)[0].url)
        overview = self.visit("subscription:details")
        self.assertFalse(any(":order:" in (b.callback_data or "") for b in self.buttons(overview)))

    def test_failed_canceled_rejected_orders_do_not_offer_payment_again(self):
        payment = self.order()
        for status in ("failed", "canceled", "rejected"):
            PaymentTransaction.objects.filter(pk=payment.pk).update(status=status)
            order = self.visit(f"subscription:order:{payment.pk}")
            self.assertEqual(len(self.buttons(order)), 1)
            self.assertNotIn("لإتمام اشتراكك", self.text(order))
            self.assertNotIn("بانتظار الدفع", self.text(order))

    def test_legacy_buttons_lead_to_valid_screens_without_creating_orders(self):
        self.stars()
        for data in (f"subscription:method:manual:{self.plan.pk}",
                     f"subscription:method:telegram_stars:{self.plan.pk}",
                     f"subscription:method-back:{self.week.pk}",
                     f"subscription:method-back:manual:{self.week.pk}"):
            self.assertIn("اختر مدة الاشتراك", self.text(self.visit(data)))
        review = self.visit(f"subscription:duration:manual:{self.week.pk}")
        self.assertIn("مراجعة الاشتراك", self.text(review))
        legacy_term = self.visit(f"subscription:term:{self.plan.pk}:7")
        self.assertIn("اختر وسيلة الدفع", self.text(legacy_term))
        self.assert_unavailable(self.visit(f"subscription:duration:telegram_stars:{self.week.pk}"))
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_repeated_edit_acknowledges_not_modified_without_extra_message(self):
        callback = self.callback("subscription:details")
        callback.message.edit_text.side_effect = TelegramBadRequest(
            method=EditMessageText(text="same", chat_id=1, message_id=1),
            message="Bad Request: message is not modified",
        )
        self.visit(callback.data, callback=callback)
        callback.answer.assert_awaited_once_with()

    def test_other_telegram_edit_errors_propagate_but_callback_is_acknowledged(self):
        callback = self.callback("subscription:details")
        callback.message.edit_text.side_effect = TelegramBadRequest(
            method=EditMessageText(text="same", chat_id=1, message_id=1), message="message can't be edited",
        )
        with self.assertRaises(TelegramBadRequest):
            self.visit(callback.data, callback=callback)
        callback.answer.assert_awaited_once_with()

    def test_html_optional_sections_actual_limits_and_subscription_name(self):
        self.plan.description = "  وصف <آمن> & clean  "
        self.plan.features = ["ميزة <أولى>", "", "  ", "English & عربي"]
        self.plan.max_accounts = 2
        self.plan.max_messages = 0
        text = plan_text(self.plan)
        self.assertIn("وصف &lt;آمن&gt; &amp; clean", text)
        self.assertIn("ميزة &lt;أولى&gt;", text)
        self.assertIn("English &amp; عربي", text)
        self.assertIn("الحسابات: 2", text)
        self.assertIn("الرسائل: 0", text)
        self.assertIn("المجموعات: غير محدودة", text)
        self.assertNotIn("\n\n\n", text)
        self.subscription.plan_definition = self.plan
        self.subscription.end_date = timezone.now() + timedelta(days=7)
        self.assertIn("&lt;VIP&gt;", subscription_text(self.subscription))
        payment = self.order()
        payment.status = "<unknown>"
        self.assertIn("&lt;unknown&gt;", manual_payment_text(payment))

    def test_stars_only_skips_method_screen_and_official_invoice_remains_separate(self):
        PlanDuration.objects.filter(plan=self.plan).update(is_active=False)
        stars = self.stars()
        durations = self.visit(f"subscription:plan:{self.plan.pk}")
        self.assertIn("وسيلة الدفع: ⭐ Telegram Stars", self.text(durations))
        self.assertEqual(self.buttons(durations)[-1].callback_data, "subscription:plans")
        review = self.visit(self.buttons(durations)[0].callback_data)
        self.assertIn("مراجعة الاشتراك", self.text(review))
        self.assertIn("Telegram Stars", self.text(review))
        self.assertFalse(PaymentTransaction.objects.exists())
        invoice_screen = self.visit(self.buttons(review)[0].callback_data)
        invoice = invoice_screen.message.answer_invoice.await_args.kwargs
        payment = PaymentTransaction.objects.get()
        self.assertEqual(payment.duration_id, stars.pk)
        self.assertEqual((payment.currency, payment.status, payment.invoice_id), ("XTR", "pending", "88"))
        self.assertEqual(invoice["prices"][0].amount, 50)
        self.assertIn("فاتورة الدفع جاهزة", self.text(invoice_screen))

    def test_stars_invoice_failure_marks_only_that_order_failed_and_acknowledges(self):
        manual = self.order()
        stars = self.stars()
        callback = self.callback(f"subscription:pay:{self.plan.pk}:7:{stars.pk}")
        callback.message.answer_invoice.side_effect = RuntimeError("mock Telegram failure")
        self.visit(callback.data, callback=callback)
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
        self.assertEqual(PaymentTransaction.objects.get(payment_method="telegram_stars").status, "failed")
        manual.refresh_from_db()
        self.assertEqual(manual.status, "pending")

    def test_malformed_context_is_rejected_without_database_writes(self):
        for data in ("subscription:plan:-1", "subscription:plan:not-an-id", "subscription:term:1",
                     f"subscription:method:crypto:{self.plan.pk}",
                     f"subscription:term-method:crypto:{self.plan.pk}:7",
                     f"subscription:term-method:telegram_stars:{self.plan.pk}:7",
                     f"subscription:term-method:manual:{self.plan.pk}:30:{self.week.pk}:extra",
                     f"subscription:term-method:manual:{self.plan.pk + 100}:7",
                     "subscription:review:1:7:99999999999999999999999999999999",
                     "subscription:pay:1:7:0", "subscription:order:0"):
            with self.subTest(data=data):
                self.assert_unavailable(self.visit(data))
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_sqlite_contention_keeps_screen_retryable_and_acknowledges_without_creating_order(self):
        callback = self.callback(f"subscription:pay:{self.plan.pk}:7:{self.week.pk}")
        with patch.object(screens, "create_pending_payment", AsyncMock(side_effect=OperationalError("database table is locked"))), \
             patch.object(screens, "is_sqlite_contention", return_value=True):
            self.visit(callback.data, callback=callback)
        self.assert_unavailable(callback)
        self.assertIn("بعد لحظات", callback.answer.await_args.args[0])
        self.assertFalse(PaymentTransaction.objects.exists())
