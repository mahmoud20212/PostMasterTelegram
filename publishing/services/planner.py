"""V2.0 compatibility planner: values in, values out; no I/O or implicit clock.

The live sender reuses cycle_pairs. Legacy wall-clock helpers remain for
compatibility; live elapsed-time work/rest phases are in services.work_rest.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable, Sequence
from uuid import UUID


@dataclass(frozen=True)
class Policy:
    min_interval_seconds: int = 30
    max_interval_seconds: int = 30
    daily_send_limit: int | None = None
    cycle_rest_seconds: int = 0


def normalize_policy(*, minimum=30, maximum=30, daily_limit=None, rest_seconds=0) -> Policy:
    minimum = max(30, int(minimum or 30))
    return Policy(minimum, max(minimum, int(maximum or minimum)), daily_limit,
                  max(0, int(rest_seconds or 0)))


def choose_interval(policy: Policy, *, randint: Callable[[int, int], int]) -> float:
    """Inclusive bounds, matching V1. Caller owns RNG state and samples once."""
    minimum = max(30, int(policy.min_interval_seconds or 30))
    maximum = max(minimum, int(policy.max_interval_seconds or minimum))
    return float(randint(minimum, maximum))


@dataclass(frozen=True)
class Message:
    id: int
    user_id: int
    created_at: datetime
    active: bool = True


@dataclass(frozen=True)
class Group:
    id: int
    user_id: int
    created_at: datetime
    selected: bool = True
    status: str = "pending"
    can_publish: bool = False  # Deliberately not an eligibility requirement.


def eligible_messages(messages: Sequence[Message], *, user_id: int) -> tuple[Message, ...]:
    return tuple(sorted((m for m in messages if m.user_id == user_id and m.active),
                        key=lambda m: m.created_at))


def eligible_groups(groups: Sequence[Group], *, user_id: int) -> tuple[Group, ...]:
    # Python sort retains input order for timestamp ties. V1 has no PK tie-breaker.
    return tuple(sorted((g for g in groups if g.user_id == user_id and g.selected
                         and g.status in ("pending", "active")), key=lambda g: g.created_at))


def cycle_pairs(*, message_count: int, group_count: int,
                message_index: int = 0, group_index: int = 0) -> tuple[tuple[int, int], ...]:
    # V1's sender returns before calling its helper on either empty collection.
    if message_count <= 0 or group_count <= 0:
        return ()
    return tuple(((message_index + offset) % message_count,
                  (group_index + offset) % group_count) for offset in range(group_count))


def advance_indices(*, message_position: int, group_position: int,
                    message_count: int, group_count: int) -> tuple[int, int] | None:
    if message_count <= 0 or group_count <= 0:
        return None
    return ((message_position + 1) % message_count, (group_position + 1) % group_count)


@dataclass(frozen=True)
class CycleState:
    started_at: datetime | None = None
    next_cycle_at: datetime | None = None
    message_index: int = 0
    group_index: int = 0


@dataclass(frozen=True)
class CycleDecision:
    action: str  # ready / rest / quiet
    state: CycleState
    deadline: datetime | None
    wait_seconds: int = 0


def legacy_working_time(*, local_now: datetime, work_hours: int, sleep_hours: int) -> bool:
    """Intentionally preserve V1's hour-of-day modulo, including its limitations."""
    return local_now.hour % (max(1, work_hours) + max(1, sleep_hours)) < max(1, work_hours)


def plan_cycle(*, now: datetime, local_now: datetime, state: CycleState,
               duration_minutes: int, sleep_enabled=False, work_hours=16,
               sleep_hours=8) -> CycleDecision:
    if state.next_cycle_at is not None:
        if now < state.next_cycle_at:
            return CycleDecision("rest", state, None,
                                 int((state.next_cycle_at - now).total_seconds()))
        state = replace(state, next_cycle_at=None)
    if sleep_enabled and not legacy_working_time(local_now=local_now, work_hours=work_hours, sleep_hours=sleep_hours):
        return CycleDecision("quiet", state, None, 60)
    duration = timedelta(minutes=duration_minutes)
    if state.started_at is None or (now - state.started_at).total_seconds() >= duration.total_seconds():
        state = replace(state, started_at=now)
    return CycleDecision("ready", state, state.started_at + duration)


def restart_cycle(state: CycleState, *, now: datetime) -> CycleState:
    """Start resets cycle start, but V1 retains cursors and pending rest."""
    return replace(state, started_at=now)


def before_deadline(*, now: datetime, deadline: datetime) -> bool:
    return now < deadline


def interval_wait(*, now: datetime, deadline: datetime, interval: float) -> float:
    return max(0, min(interval, (deadline - now).total_seconds()))


def remaining_cycle_wait(*, now: datetime, deadline: datetime) -> int:
    return max(0, int((deadline - now).total_seconds()))


def rest_after_cycle(*, now: datetime, rest_seconds: int, run_active: bool) -> datetime | None:
    """now is actual completion of the wait, not a predicted cycle deadline."""
    return now + timedelta(seconds=rest_seconds) if run_active and rest_seconds else None


@dataclass(frozen=True)
class Quota:
    count: int = 0
    reset_at: datetime | None = None
    notified_at: datetime | None = None


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    state: Quota


def reserve_quota(state: Quota, *, now: datetime, limit: int | None) -> QuotaDecision:
    if state.reset_at is None or state.reset_at <= now:
        state = Quota(0, now + timedelta(days=1), None)
    if limit is not None and state.count >= limit:
        return QuotaDecision(False, state)
    return QuotaDecision(True, replace(state, count=state.count + 1))


def release_quota(state: Quota) -> Quota:
    return replace(state, count=max(0, state.count - 1))


@dataclass(frozen=True)
class Ownership:
    run_id: UUID
    user_id: int
    account_id: int | None
    account_user_id: int | None
    run_active: bool = True
    user_publishing: bool = True
    user_status: str = "active"
    account_status: str = "connected"
    session_present: bool = True
    session_matches: bool = True
    paused_until: datetime | None = None
    subscription_status: str = "active"
    plan_present: bool = True
    subscription_end: datetime | None = None


def owns_attempt(state: Ownership, *, run_id: UUID | None, user_id: int,
                 account_id: int | None, now: datetime) -> bool:
    """Snapshot predicate only: no claim, locking, liveness or sending guarantee."""
    return bool(
        run_id is not None and state.run_id == run_id and state.user_id == user_id
        and account_id is not None and state.account_id == account_id
        and state.account_user_id == user_id and state.run_active and state.user_publishing
        and state.user_status == "active" and state.account_status == "connected"
        and state.session_present and state.session_matches
        and (state.paused_until is None or state.paused_until <= now)
        and state.subscription_status == "active" and state.plan_present
        and (state.subscription_end is None or state.subscription_end > now)
    )
