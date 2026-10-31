# Durable publishing engine

Each Celery `publishing.publish_step` invocation handles at most one planned
message/group item. Intervals, cycle rests, quiet periods and FloodWait delays
are database timestamps; no worker sleeps through them. Telegram typing remains
disabled. No user capacity has been established by load testing.

## State and dispatch

- `PublishingRun.next_due_at` is the authoritative schedule. Stable message/group
  IDs are planned once per cycle, with persisted position and attempt sequence.
- The scheduler gives the oldest-dispatched eligible user the next turn. Each
  user can have one queued/running item. A global pending bound keeps notifications
  close to worker availability. Default limits are configuration, not capacity.
- A dispatch token and expiry are stored before publication. If publication
  fails or Redis loses a notification, a later pass issues a fresh token. Late
  deliveries of expired tokens cannot claim work. An accepted-but-unconfirmed
  publication may still execute its original valid token.
- A database constraint permits one open attempt per user across run generations.
  PostgreSQL session advisory locks additionally serialize actual execution for
  each user and Telegram identity. They use dedicated, unpooled connections and
  no database transaction spans Telegram I/O.
- SQLite is supported for local development on one host using OS file locks.
  Use PostgreSQL with a direct or session-pooled connection in production;
  transaction-pooling proxies are incompatible with session advisory locks.
- The default standalone scheduler does not occupy a Celery worker. Alternatively,
  set `PUBLISHING_USE_BEAT=true` and let one Beat instance dispatch every two
  seconds. Choose one scheduling entry point; concurrent ticks are still guarded.

## Delivery and recovery policy

Attempts move from `claimed` to `sending`, then to `success`, `failed`, `unknown`
or `skipped`. The sending marker and quota reservation commit before the send.

A confirmed failure is recorded, refunds its reservation, consumes the item and
schedules the next one. A timeout/network loss after the sending marker, or a
worker loss before its result is recorded, is `unknown`: it consumes the item,
retains its quota charge and is **never retried**. The run continues. This prefers
missing a post to duplicating one. A later ordinary cycle can still publish the
same content as a new scheduled item.

Expired pre-send attempts are failed and consumed. Recovery advances each attempt
once, atomically with its log and quota disposition. Recovery waits for the
execution lock to be released; lease expiry alone cannot establish worker death.
If a process is still hung, other users continue while that user's execution is
excluded. Supervise production workers and terminate stuck processes. Application
timeouts cannot forcibly kill a Windows worker thread. Linux prefork also has
soft/hard task limits configured below the recovery lease.

Stop invalidates the generation and its queued notification. Permissions and
session identity are checked again immediately before sending. An already
in-flight Telegram request can still finish after Stop. Its completion cannot
modify a replacement run. No design here makes a Telegram request atomic with a
database transaction; unknown outcomes are handled explicitly instead.

FloodWait is durable on the publishing account and resumes after its deadline.
Slow Mode is durable for the account/group pair; cooling items are skipped so
other groups can progress. Group rejection disables only that user's group.
PeerFlood and invalid authorization stop only the affected run/account. Existing
subscription and rolling-quota rules remain, including the existing subscription
expiry behavior. Unknowns count against the quota; refunds cannot alter a later
quota window. Account selection remains pinned until the next run.

Sleep now uses durable elapsed-time work/rest phases. Enabling it, saving both
durations, or manually starting publishing begins a full work phase. Intervals,
plan rests, FloodWait and process downtime count as elapsed time; process restart
preserves the phase, while manual Stop/Start starts a new work phase. Rest and
wake transitions are processed independently of the next send deadline, without
occupying a send worker. A step interrupted by rest before sending is deferred
without moving its cursor or charging quota. Phase notifications are delivered
through the transactional notification outbox; see notifications/README.md.
Each step reconnects
using the existing encrypted StringSession. Numeric/private peers retain the
existing requirement that Telethon can resolve their identifiers; there is no
new persistent entity/access-hash cache. Group extraction and account-management
connections are separate existing workflows, not scheduler tasks.

## Safe cutover

The migration is additive. It does not rewrite users, plans, settings, cursors,
quota counts or encrypted sessions, and does not switch database providers.
Keep the original `TELEGRAM_SESSION_ENCRYPTION_KEY` and authoritative database.
The local PostgreSQL and SQLite datasets may differ; do not overwrite either
with the other as part of installing this engine.

1. Back up the authoritative database and encryption key securely.
2. Stop the old bot and all old publishing workers; establish that no old send
   can still execute. Do not perform a mixed-version rollout.
3. Apply migrations, then optionally adopt active legacy runs using the explicit
   command below. Version-1 runs are excluded by default. Legacy tasks are no-ops.
   Old-engine sends had no durable attempt journal; resolve any uncertainty from
   those old executions before adoption. The new recovery policy applies to new
   journaled attempts.
4. Start the new worker, scheduler and bot against the same database and Redis.
   Do not purge shared Redis or reset claims by hand.

Windows/local commands (run the long-lived commands in separate terminals):

```powershell
.\.venv\Scripts\python.exe manage.py migrate
# Only after old execution is stopped, if adopting active version-1 runs:
.\.venv\Scripts\python.exe manage.py upgrade_publishing_runs --confirm-execution-stopped
.\.venv\Scripts\celery.exe -A config worker -l INFO --pool=threads --concurrency=4 --prefetch-multiplier=1 -Q publishing,notifications,celery
.\.venv\Scripts\python.exe manage.py run_publishing_scheduler
.\.venv\Scripts\celery.exe -A config beat -l INFO
.\.venv\Scripts\python.exe -m bot.main
```

Beat continues the existing subscription-expiry schedule; with the standalone
scheduler, leave `PUBLISHING_USE_BEAT` unset. `run_publishing_scheduler --once`
performs a single dispatch/recovery pass. New Start requests are durable even
when Redis is temporarily unavailable; publishing begins once dispatch succeeds.

For Linux production, set `DJANGO_SETTINGS_MODULE=config.settings.production`
for **every** process, and use a supervised prefork worker:

```sh
python manage.py migrate
celery -A config worker -l INFO --pool=prefork --concurrency=4 -Q publishing,notifications,celery
python manage.py run_publishing_scheduler
celery -A config beat -l INFO
python -m bot.main
```

## Verification

```powershell
.\.venv\Scripts\python.exe manage.py test --settings=config.settings.testing --noinput
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.settings.testing
```

Tests mock Telegram, create their own encryption material, and never send real
messages. `config.settings.testing` defaults to an in-memory SQLite database.
For PostgreSQL checks, explicitly set `ONEPOST_TEST_DATABASE_URL` to a PostgreSQL
endpoint where the test role may create/drop databases. Django creates a unique
temporary test database; this setting never selects the existing application
database as the test database. Do not use `--keepdb` with this randomized setup.

Coverage includes more due users than dispatch slots, parallel users, duplicate
workers/dispatchers, Stop isolation, confirmed failures, unknown sends, failure
after send before commit, process-loss lock release, database lock contention,
quota windows, rate limits, session replacement, subscriptions and migration
preservation. Celery/Redis deployment and real Telegram connectivity still need
staging verification; throughput and fairness latency need load measurements.
