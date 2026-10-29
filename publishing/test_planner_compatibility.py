"""Compare the candidate planner with real V1 helpers/ORM rules, without sends."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from random import Random
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from uuid import UUID

from django.test import SimpleTestCase, TestCase

from publishing.services import engine, planner as p, policy, runs
from publishing.test_session_safety import SessionFixtures
from telegram_groups.models import TelegramGroup
from users.models import User


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


class PlannerHelperCompatibilityTests(SimpleTestCase):
    def test_cycle_decisions_match_current_runner_without_network_or_db(self):
        for started, next_cycle, sleep_enabled, hour in (
            (None, None, False, 12),
            (NOW - timedelta(minutes=5), None, False, 12),
            (NOW - timedelta(minutes=4), None, False, 12),
            (NOW, NOW + timedelta(minutes=1), True, 12),
            (NOW, NOW, False, 12),
            (NOW, None, True, 23),
        ):
            now = NOW.replace(hour=hour)
            state = p.CycleState(started, next_cycle)
            candidate = p.plan_cycle(now=now, local_now=now, state=state, duration_minutes=5,
                                     sleep_enabled=sleep_enabled)
            account = SimpleNamespace(pk=1, encrypted_session="mocked-session")
            snapshot = dict(account=account, cycle_started_at=started, next_cycle_at=next_cycle,
                            cycle_duration_minutes=5, sleep_enabled=sleep_enabled, work_hours=16, sleep_hours=8)
            client = Mock(connect=AsyncMock(), disconnect=AsyncMock(), is_user_authorized=AsyncMock(return_value=True))
            with self.subTest(state=state, hour=hour), ExitStack() as stack:
                stack.enter_context(patch.object(engine, "claim_run", new=AsyncMock(return_value=True)))
                stack.enter_context(patch.object(engine, "get_publish_snapshot", new=AsyncMock(return_value=snapshot)))
                stack.enter_context(patch.object(engine, "run_policy", new=AsyncMock(return_value={"cycle_rest_seconds": 0})))
                sequence = [True, False, False] if candidate.action == "ready" else [True, False]
                stack.enter_context(patch.object(engine, "run_is_active", new=AsyncMock(side_effect=sequence)))
                stack.enter_context(patch.object(engine, "finish_run_async", new=AsyncMock()))
                sent = stack.enter_context(patch.object(engine, "_send_cycle", new=AsyncMock()))
                wait = stack.enter_context(patch.object(engine, "_wait_for_next_cycle", new=AsyncMock()))
                set_start = stack.enter_context(patch.object(engine, "set_cycle_started_at", new=AsyncMock()))
                set_next = stack.enter_context(patch.object(engine, "set_next_cycle_at", new=AsyncMock()))
                stack.enter_context(patch.object(engine.os, "getenv", return_value="123"))
                cipher = stack.enter_context(patch("cryptography.fernet.Fernet"))
                cipher.return_value.decrypt.return_value = b"mocked"
                stack.enter_context(patch.object(engine, "StringSession"))
                stack.enter_context(patch.object(engine, "TelegramClient", return_value=client))
                stack.enter_context(patch.object(engine.timezone, "now", return_value=now))
                stack.enter_context(patch.object(engine.timezone, "localtime", return_value=now))
                async_to_sync(engine._run)(101, UUID(int=1))
                if candidate.action == "ready":
                    sent.assert_awaited_once()
                    self.assertEqual(sent.await_args.args[3], candidate.deadline)
                    self.assertEqual(set_start.await_count, int(candidate.state.started_at != started))
                else:
                    sent.assert_not_awaited()
                    self.assertEqual(wait.await_args.args[1], candidate.wait_seconds)
                self.assertEqual(set_next.await_count, int(next_cycle is not None and next_cycle <= now))

    def test_distribution_matches_current_helper_across_indices_and_sizes(self):
        for message_count in (1, 2, 7):
            for group_count in (0, 1, 4, 11):
                for message_index, group_index in ((0, 0), (2, 3), (19, 21)):
                    args = dict(message_count=message_count, group_count=group_count,
                                message_index=message_index, group_index=group_index)
                    with self.subTest(**args):
                        self.assertEqual(list(p.cycle_pairs(**args)), engine.build_cycle_pairs(**args))

    def test_seeded_intervals_and_policy_match_real_implementation(self):
        for low, high in ((0, 0), (10, 5), (30, 60), (60, 90)):
            a, b = Random(91), Random(91)
            plan = SimpleNamespace(min_interval_seconds=low, max_interval_seconds=high,
                                   daily_send_limit=3, cycle_rest_seconds=120)
            normalized = p.normalize_policy(minimum=low, maximum=high, daily_limit=3, rest_seconds=120)
            self.assertEqual(policy._normalise(plan), dict(min_interval_seconds=normalized.min_interval_seconds,
                             max_interval_seconds=normalized.max_interval_seconds,
                             daily_send_limit=normalized.daily_send_limit, cycle_rest_seconds=normalized.cycle_rest_seconds))
            with patch.object(engine.random, "randint", side_effect=a.randint):
                expected = [engine.calculate_interval(min_interval_seconds=low, max_interval_seconds=high) for _ in range(20)]
            self.assertEqual(expected, [p.choose_interval(normalized, randint=b.randint) for _ in range(20)])

    def test_quiet_modulo_matches_current_implementation_including_invalid_hours(self):
        for hour in range(24):
            for work, sleep in ((16, 8), (22, 4), (24, 24), (0, 0)):
                now = NOW.replace(hour=hour)
                self.assertEqual(p.legacy_working_time(local_now=now, work_hours=work, sleep_hours=sleep),
                                 engine.is_working_time(now=now, work_hours=work, sleep_hours=sleep))

    def test_quota_reset_matches_current_reset_function(self):
        for reset_at in (None, NOW - timedelta(seconds=1), NOW, NOW + timedelta(seconds=1)):
            user = SimpleNamespace(daily_send_count=3, daily_send_reset_at=reset_at, daily_limit_notified_at=NOW)
            policy._reset_if_needed(user, NOW)
            # limit=0 prevents reservation; reset is still persisted by V1.
            actual = p.reserve_quota(p.Quota(3, reset_at, NOW), now=NOW, limit=0).state
            self.assertEqual(actual, p.Quota(user.daily_send_count, user.daily_send_reset_at, user.daily_limit_notified_at))


class PlannerDatabaseCompatibilityTests(SessionFixtures, TestCase):
    def test_selection_matches_current_snapshot_without_telegram(self):
        TelegramGroup.objects.create(user=self.user, identifier="@disabled", canonical_identifier="username:disabled", status="disabled")
        TelegramGroup.objects.create(user=self.user, identifier="@notselected", canonical_identifier="username:notselected", is_selected=False)
        self.make_user(202)
        groups = [p.Group(g.pk, g.user_id, g.created_at, g.is_selected, g.status, g.can_publish)
                  for g in TelegramGroup.objects.order_by("created_at")]
        snapshot = engine.get_publish_snapshot.func(101)
        self.assertEqual([g.id for g in p.eligible_groups(groups, user_id=self.user.pk)], [g.pk for g in snapshot["groups"]])
        self.assertEqual(p.eligible_groups(groups, user_id=self.user.pk)[0].can_publish, False)

    def test_quota_matches_actual_reservation_and_release(self):
        for limit, count, reset_at in ((0, 0, None), (2, 1, NOW + timedelta(days=1)),
                                      (2, 2, NOW + timedelta(days=1)), (None, 100, NOW + timedelta(days=1)),
                                      (2, 2, NOW)):
            User.objects.filter(pk=self.user.pk).update(daily_send_count=count, daily_send_reset_at=reset_at, daily_limit_notified_at=NOW)
            expected = p.reserve_quota(p.Quota(count, reset_at, NOW), now=NOW, limit=limit)
            with patch.object(policy.timezone, "now", return_value=NOW):
                allowed = policy.reserve_send_slot.func(101, limit)
            self.user.refresh_from_db()
            self.assertEqual(allowed, expected.allowed)
            self.assertEqual((self.user.daily_send_count, self.user.daily_send_reset_at, self.user.daily_limit_notified_at),
                             (expected.state.count, expected.state.reset_at, expected.state.notified_at))
            policy.release_send_slot.func(101)
            self.user.refresh_from_db()
            self.assertEqual(self.user.daily_send_count, p.release_quota(expected.state).count)

    def test_start_stop_and_account_binding_match_current_run_validation(self):
        run = self.start()
        snapshot = engine.get_publish_snapshot.func(101, run.pk)
        owner = p.Ownership(run.pk, self.user.pk, snapshot["account"].pk, self.user.pk)
        args = dict(run_id=run.pk, user_id=self.user.pk, account_id=self.account.pk, now=NOW)
        self.assertTrue(p.owns_attempt(owner, **args))
        self.assertIsNotNone(runs.run_policy.func(101, run.pk, self.account.pk))
        runs.finish_run(101)
        new = self.start()
        self.assertIsNone(runs.run_policy.func(101, run.pk, self.account.pk))
        self.assertFalse(p.owns_attempt(replace(owner, run_id=new.pk), **args))
        self.assertIsNotNone(runs.run_policy.func(101, new.pk, self.account.pk))
