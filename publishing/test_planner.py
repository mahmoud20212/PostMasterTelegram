"""Pure tests: runnable with unittest without Django setup or external services."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from random import Random
from unittest import TestCase
from uuid import UUID

from publishing.services import planner as p


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
RUN = UUID(int=1)


class PlannerTests(TestCase):
    def test_rotation_visits_each_group_once_and_wraps_messages(self):
        pairs = p.cycle_pairs(message_count=2, group_count=5, message_index=1, group_index=3)
        self.assertEqual(pairs, ((1, 3), (0, 4), (1, 0), (0, 1), (1, 2)))
        self.assertEqual(len({group for _, group in pairs}), 5)
        cursor = p.advance_indices(message_position=1, group_position=2, message_count=2, group_count=5)
        self.assertEqual(cursor, (0, 3))
        self.assertEqual(p.cycle_pairs(message_count=2, group_count=5, message_index=cursor[0], group_index=cursor[1])[0], (0, 3))

    def test_empty_collections_do_not_divide_by_zero(self):
        for messages, groups in ((0, 0), (0, 3), (3, 0)):
            with self.subTest(messages=messages, groups=groups):
                self.assertEqual(p.cycle_pairs(message_count=messages, group_count=groups), ())
                self.assertIsNone(p.advance_indices(message_position=0, group_position=0, message_count=messages, group_count=groups))
        self.assertEqual(p.eligible_messages([], user_id=1), ())
        self.assertEqual(p.eligible_groups([], user_id=1), ())

    def test_eligibility_ordering_and_input_immutability(self):
        pending = p.Group(1, 1, NOW, can_publish=False)
        active = p.Group(2, 1, NOW - timedelta(seconds=1), status="active")
        inputs = [pending, replace(pending, id=3, status="disabled"), active,
                  replace(pending, id=4, user_id=2), replace(pending, id=5, selected=False),
                  replace(pending, id=6, status="unauthorized")]
        before = list(inputs)
        self.assertEqual(p.eligible_groups(inputs, user_id=1), (active, pending))
        self.assertEqual(inputs, before)
        message = p.Message(1, 1, NOW)
        self.assertEqual(p.eligible_messages([message, replace(message, id=2, active=False),
                                            replace(message, id=3, user_id=2)], user_id=1), (message,))

    def test_timestamp_ties_retain_supplied_order_not_new_pk_order(self):
        groups = [p.Group(9, 1, NOW), p.Group(2, 1, NOW)]
        self.assertEqual([g.id for g in p.eligible_groups(groups, user_id=1)], [9, 2])

    def test_policy_bounds_and_injected_randomness(self):
        policy = p.normalize_policy(minimum=5, maximum=1, daily_limit=0, rest_seconds=-10)
        self.assertEqual(policy, p.Policy(30, 30, 0, 0))
        calls = []

        def upper(low, high):
            calls.append((low, high))
            return high

        self.assertEqual(p.choose_interval(p.Policy(30, 60), randint=upper), 60.0)
        self.assertEqual(calls, [(30, 60)])
        a, b = Random(71), Random(71)
        self.assertEqual([p.choose_interval(p.Policy(30, 90), randint=a.randint) for _ in range(10)],
                         [p.choose_interval(p.Policy(30, 90), randint=b.randint) for _ in range(10)])

    def plan(self, state=p.CycleState(), now=NOW, **kwargs):
        return p.plan_cycle(now=now, local_now=now, state=state, duration_minutes=5, **kwargs)

    def test_new_cycle_and_exact_boundary(self):
        initial = self.plan()
        self.assertEqual(initial.deadline, NOW + timedelta(minutes=5))
        before = self.plan(initial.state, NOW + timedelta(minutes=5, microseconds=-1))
        self.assertEqual(before.state.started_at, NOW)
        boundary = self.plan(initial.state, NOW + timedelta(minutes=5))
        self.assertEqual(boundary.state.started_at, NOW + timedelta(minutes=5))

    def test_deadline_is_exclusive_and_interval_is_clipped(self):
        deadline = NOW + timedelta(seconds=2.5)
        self.assertTrue(p.before_deadline(now=NOW, deadline=deadline))
        self.assertFalse(p.before_deadline(now=deadline, deadline=deadline))
        self.assertEqual(p.interval_wait(now=NOW, deadline=deadline, interval=30), 2.5)
        self.assertEqual(p.remaining_cycle_wait(now=NOW, deadline=deadline), 2)
        self.assertEqual(p.interval_wait(now=deadline + timedelta(seconds=1), deadline=deadline, interval=30), 0)

    def test_rest_precedes_quiet_and_expired_rest_is_cleared(self):
        night = NOW.replace(hour=23)
        state = p.CycleState(next_cycle_at=night + timedelta(seconds=10))
        decision = self.plan(state, night, sleep_enabled=True)
        self.assertEqual((decision.action, decision.wait_seconds), ("rest", 10))
        decision = self.plan(state, state.next_cycle_at, sleep_enabled=True)
        self.assertEqual((decision.action, decision.wait_seconds), ("quiet", 60))
        self.assertIsNone(decision.state.next_cycle_at)

    def test_subsecond_rest_preserves_v1_integer_truncation(self):
        decision = self.plan(p.CycleState(next_cycle_at=NOW + timedelta(microseconds=500000)))
        self.assertEqual((decision.action, decision.wait_seconds), ("rest", 0))

    def test_rest_begins_at_actual_wait_completion(self):
        completion = NOW + timedelta(seconds=3)
        self.assertEqual(p.rest_after_cycle(now=completion, rest_seconds=60, run_active=True), completion + timedelta(seconds=60))
        self.assertIsNone(p.rest_after_cycle(now=completion, rest_seconds=60, run_active=False))
        self.assertIsNone(p.rest_after_cycle(now=completion, rest_seconds=0, run_active=True))

    def test_midnight_preserves_known_26_hour_quiet_bug(self):
        night = NOW.replace(hour=23)
        midnight = (night + timedelta(days=1)).replace(hour=0)
        self.assertFalse(p.legacy_working_time(local_now=night, work_hours=22, sleep_hours=4))
        self.assertTrue(p.legacy_working_time(local_now=midnight, work_hours=22, sleep_hours=4))

    def test_timezone_uses_explicit_local_clock_not_implicit_conversion(self):
        instant = NOW.replace(hour=14)
        local = instant.astimezone(timezone(timedelta(hours=3)))
        self.assertTrue(p.legacy_working_time(local_now=instant, work_hours=16, sleep_hours=8))
        self.assertFalse(p.legacy_working_time(local_now=local, work_hours=16, sleep_hours=8))
        decision = p.plan_cycle(now=instant, local_now=local, state=p.CycleState(), duration_minutes=5, sleep_enabled=True)
        self.assertEqual(decision.action, "quiet")

    def test_quiet_hours_ignore_minutes_and_date(self):
        self.assertEqual(p.legacy_working_time(local_now=NOW.replace(hour=15, minute=0), work_hours=16, sleep_hours=8),
                         p.legacy_working_time(local_now=NOW.replace(hour=15, minute=59), work_hours=16, sleep_hours=8))

    def test_quota_at_limit_reset_and_unlimited(self):
        state = p.Quota(2, NOW + timedelta(seconds=1), NOW)
        self.assertFalse(p.reserve_quota(state, now=NOW, limit=2).allowed)
        self.assertEqual(p.reserve_quota(state, now=NOW, limit=None).state.count, 3)
        reset = p.reserve_quota(state, now=state.reset_at, limit=2)
        self.assertTrue(reset.allowed)
        self.assertEqual(reset.state, p.Quota(1, state.reset_at + timedelta(days=1), None))
        self.assertFalse(p.reserve_quota(p.Quota(), now=NOW, limit=0).allowed)

    def test_quota_midnight_does_not_reset_rolling_window(self):
        night = NOW.replace(hour=23, minute=59)
        reserved = p.reserve_quota(p.Quota(), now=night, limit=1)
        midnight = night + timedelta(minutes=1)
        self.assertFalse(p.reserve_quota(reserved.state, now=midnight, limit=1).allowed)
        self.assertEqual(reserved.state.reset_at, night + timedelta(days=1))
        self.assertEqual(p.release_quota(reserved.state).count, 0)
        self.assertEqual(p.release_quota(p.Quota()).count, 0)

    def test_stop_start_retains_cursors_and_rest_but_resets_cycle_start(self):
        old = p.CycleState(NOW, NOW + timedelta(hours=1), 7, 3)
        restarted = p.restart_cycle(old, now=NOW + timedelta(minutes=1))
        self.assertEqual((restarted.message_index, restarted.group_index, restarted.next_cycle_at), (7, 3, old.next_cycle_at))
        self.assertNotEqual(restarted.started_at, old.started_at)
        self.assertEqual(self.plan(restarted, NOW + timedelta(minutes=1)).action, "rest")

    def test_ownership_rejects_stopped_stale_cross_user_and_switched_accounts(self):
        owner = p.Ownership(RUN, 1, 10, 1)
        valid = dict(run_id=RUN, user_id=1, account_id=10, now=NOW)
        self.assertTrue(p.owns_attempt(owner, **valid))
        for changes in ({"run_id": None}, {"run_id": UUID(int=2)}, {"user_id": 2}, {"account_id": 11}):
            self.assertFalse(p.owns_attempt(owner, **(valid | changes)))
        for field in ("run_active", "user_publishing", "session_present", "session_matches", "plan_present"):
            self.assertFalse(p.owns_attempt(replace(owner, **{field: False}), **valid))
        restarted = replace(owner, run_id=UUID(int=2))
        self.assertFalse(p.owns_attempt(restarted, **valid))

    def test_ownership_pause_and_subscription_exact_boundaries(self):
        owner = p.Ownership(RUN, 1, 10, 1, paused_until=NOW)
        args = dict(run_id=RUN, user_id=1, account_id=10, now=NOW)
        self.assertTrue(p.owns_attempt(owner, **args))
        self.assertFalse(p.owns_attempt(replace(owner, paused_until=NOW + timedelta(seconds=1)), **args))
        self.assertFalse(p.owns_attempt(replace(owner, subscription_end=NOW), **args))
        self.assertFalse(p.owns_attempt(replace(owner, account_status="unauthorized"), **args))
        self.assertFalse(p.owns_attempt(replace(owner, account_user_id=2), **args))
