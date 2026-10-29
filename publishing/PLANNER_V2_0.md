# V2.0 — deterministic compatibility planner

## Scope

`services/planner.py` is a candidate specification of the current engine's
decisions. It is **not imported or called by the production engine**. No schema,
Celery task, queue, settings, Compose, bot interface, or Telegram send path changes.
It does not implement dispatch, execution claims, retries, or V2.1.

The module imports only the Python standard library. Frozen dataclasses contain
plain snapshots, not Django objects, querysets, session strings, or clients.
Functions do not mutate their inputs. All times are explicit arguments; interval
sampling takes an injected `randint(low, high)` callable with inclusive bounds.
The caller owns RNG state. Reuse a stored sampled interval instead of sampling
again for a retry in a future integration.

## Preserved rules and source mapping

| Planner function | Current implementation / preserved behavior |
| --- | --- |
| `normalize_policy` | `policy._normalise`: lower interval floor 30, upper at least lower, `None` daily limit means unlimited, nonnegative plan rest |
| `choose_interval` | `engine.calculate_interval`: inclusive random integer converted to float; cycle duration and collection sizes do not set the interval |
| `eligible_messages` | `get_publish_snapshot`: owner + active, ascending `created_at` |
| `eligible_groups` | owner + selected + status `pending`/`active`, ascending `created_at`; `can_publish=False` does not exclude pending groups |
| `cycle_pairs` | `build_cycle_pairs`: exactly one planned pair per eligible group, both indices wrap; not all messages to all groups |
| `advance_indices` | `advance_cycle_indices`: next positions after an advancing attempt; caller decides whether the V1 branch advances |
| `plan_cycle` | `_run`: future rest first, then quiet-hour check, then initialize/roll over cycle at elapsed duration >= configured duration |
| `before_deadline` | `_send_cycle`: now >= deadline prevents starting the next iteration |
| `interval_wait` | interval clipped to remaining deadline; no sleeping is performed |
| `remaining_cycle_wait` | `_run`: integer truncation of positive remainder; zero after deadline |
| `rest_after_cycle` | rest starts from actual completion time of cycle wait, only for an active run and nonzero rest |
| `restart_cycle` | `create_run`: reset start time, retain saved indices and pending `next_cycle_at` |
| `reserve_quota` / `release_quota` | reserve/release policy, including rolling reset and notification reset |
| `owns_attempt` | snapshot version of `run_policy`: bound run/user/account, status, session booleans, pause and subscription checks |

Collection IDs and ownership `user_id` values are **database PKs**, not Telegram
user IDs. Adapters must translate once consistently. Use actual UUID objects for
run identity. Choosing the publishing account and creating a UUID remain caller
responsibilities. A changed account selection does not change the bound account
snapshot. A new run rejects a stale task's UUID even for the same user/account.

`cycle_pairs` returns positions into the already-filtered, already-ordered
collections. For two messages, five groups, and initial indices `(1, 3)`, output
is `((1,3), (0,4), (1,0), (0,1), (1,2))`. The next saved indices after the last
advancing position are `(0,3)`.

V1 returns early on an empty messages or groups collection before invoking its
low-level pairing helper. The candidate models that sender-level behavior with
an empty tuple; it does not reproduce the helper's division-by-zero exception
when called directly with zero messages and positive groups. `advance_indices`
returns `None` for an empty collection because no position can advance.

Python sorting retains supplied order for equal timestamps. Production queries
currently specify only `created_at`, so cross-query/cross-database ordering of
ties is not guaranteed. **No new PK tie-breaker is silently introduced.**

## Composing the functions (future caller, not runtime wiring)

1. Read consistent ownership/policy/settings/target snapshots outside this module.
2. Evaluate `owns_attempt` against the exact expected run/user/account.
3. Call `plan_cycle` with explicit `now` and local wall clock. A rest/quiet
   decision schedules another evaluation; the function never sleeps.
4. Filter/order targets, generate pairs, and check each actual attempt against
   its current time/deadline and freshly revalidated permissions.
5. Evaluate quota at actual reservation time; persist atomically outside the
   module. Advance only on the corresponding V1 execution branch.
6. Use actual completion times to compute waits/rest, not guessed network
   duration. Do not predeclare all planned pairs as sent or quota-reserved.

These are separate decisions deliberately: static plans cannot predict typing,
network duration, Stop, quota races, or edits to selected targets. Membership in
a plan is not authorization to send. The planner alone provides no locks,
fencing, idempotency, liveness, database concurrency protection, or recovery.

## Exact boundaries

- `now == next_cycle_at`: clear pending rest and evaluate the work schedule.
- `now == started_at + duration`: begin a fresh cycle at `now`, without catch-up.
- `now == deadline`: do not begin a new send iteration. V1 does not recheck
  deadline after typing; this module does not change sending behavior.
- Future rest under one second still returns `rest` with a zero-second integer
  wait, matching V1's truncation (and possible repeated evaluation).
- Quota resets when reset time is missing or `reset_at <= now`; new reset is
  `now + timedelta(days=1)`, not midnight. Unlimited plans still increment count.
- At count == limit, reservation fails. Limit zero rejects even after resetting.
- Release subtracts one with floor zero; it does not reset the daily window.
- `paused_until == now` permits an otherwise valid account.
- `subscription_end == now` rejects sending. Ownership does not perform the
  subscription-expiration service's destructive side effects.
- Stop/Start does not clear the saved rest, rotate immediately, or bypass quota.

Supply consistent datetime types. UTC-aware instants match production
`timezone.now()` for deadlines, subscription expiry, and quota. `local_now` is
explicit because V1 calls `timezone.localtime()` separately. No hidden timezone
conversion, system clock, DST policy, or environment lookup is added.

## Quiet-hours behavior: preserved limitations / product decisions

`legacy_working_time` intentionally mirrors:

```text
local_now.hour % (max(1, work_hours) + max(1, sleep_hours)) < max(1, work_hours)
```

- It uses only the hour of the day, not an elapsed duration or an anchor date.
- With 22 work hours + 4 sleep hours, hour 23 sleeps but the next midnight works
  again, before a continuous four-hour sleep completes. This is now a regression
  fixture, not an endorsement of the behavior.
- Minutes, seconds, date, and duration since Start are ignored.
- Global Django timezone is UTC in current settings. The user-specific timezone
  field and `sleep_start`/`sleep_end` are not used by the active engine path.
- An explicit caller conversion changes the wall hour and can change the result;
  the planner must be given the same local clock as the old engine for parity.
- DST can skip/repeat wall hours; V2.0 introduces no new resolution policy.

Before a future rollout, decide continuous anchored cycles versus daily windows,
which timezone controls them, and whether existing users opt into new semantics.
Do not replace this legacy predicate under the same behavior version.

Other unresolved decisions: PK ordering for timestamp ties; numeric cursor
behavior after target deletion/addition; attempts versus successful deliveries
for quota accounting; failed/cooldown attempts advancing cursors (as V1 currently
does in some branches); a quota window ledger for releases across resets;
subsecond timing truncation; and strict deadline enforcement after typing.
These require separate versioned changes, not incidental fixes in V2.0.

## Validation

Pure tests can run without Django setup:

```powershell
.\.venv\Scripts\python.exe -B -m unittest publishing.test_planner -v
```

Compatibility and regression suites use a temporary test DB only:

```powershell
.\.venv\Scripts\python.exe -B manage.py test --settings=config.test_settings --verbosity=1
.\.venv\Scripts\python.exe -B manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe -B manage.py makemigrations --check --dry-run --settings=config.test_settings
git diff --check
```

Compatibility tests compare real V1 distribution, seeded intervals, policy,
quiet-hour predicates, ORM group selection, quota reservations/releases, and
run ownership. The current `_run` is also exercised with network/DB boundaries
mocked to compare cycle decisions and captured deadlines. No Telegram delivery
is attempted. Tests do not prove distributed scheduler reliability or authorize
implementation of V2.1.
