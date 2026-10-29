# V2.1 — PostgreSQL preparation, offline migration, and validation

## Safety boundary

This release does not change production Compose, run AWS commands, switch the
default database, add migrations, or wire any V2 planner/task into publishing.
SQLite is still selected unless `DATABASE_BACKEND=postgresql` is explicit.
PostgreSQL settings also require DB/user/password/host; there is no fallback
to a public server or implicit production URL.

`requirements-postgres.txt` adds psycopg 3 without editing the existing dependency
files. Dockerfile.staging selects it only for this rehearsal's build argument.
The subsequent Docker-blocker fix also installs the driver in the production
Dockerfile; see `PRODUCTION_IMAGE.md` for local production-image validation.
Setting env vars on an old image without that driver is still insufficient.

`docker-compose.postgres-staging.yml` is standalone: project
`onepost-pg-staging`, internal network, no published ports, no bot/worker/Beat or
Redis service, separate PostgreSQL/artifact volumes. Tools retain the staging
Telegram/credential guard. `/migration-input` is a read-only mount containing
only the chosen backup. Do not mount the repository, .aws, production .env or
an encryption key. Do not run raw Docker commands that bypass the entrypoint.

The staging database role is intentionally an isolated cluster superuser so it
can create disposable test DBs. Never reuse this role/password configuration
for a network-accessible production cluster. Production runtime should use a
non-superuser role; migration/restore administration must be separate.

## Transfer contract

`pg_transfer --apply --confirm-empty-staging-target`:

1. Allows only PostgreSQL staging settings, host `staging-postgres`, DB
   `onepost_staging`, and the read-only SQLite source alias.
2. Checks SQLite integrity/FKs and identical migration histories. Use a backup
   from the same application/schema version; do not run historical data migrations
   on the live source as an incidental step of moving databases.
3. Refuses unknown tables and nonempty target application data. Target should be
   freshly migrated, containing only generated ContentTypes/Permissions/free plan.
4. In one target transaction, replaces those generated seed records with the
   source records using their **original PKs**, then imports all managed models
   and auto-created M2M tables, including their PKs. No natural-key remapping.
5. Preserves auth password hashes, permission assignments, content-type references,
   Django sessions, encrypted Telegram sessions, subscription data, run UUIDs and
   claim/state flags. It does not decrypt, activate, or normalize runs. Django's
   target migration recorder remains the target's schema history, not copied IDs
   or application timestamps from the source recorder.
6. Preserves full datetime microseconds (the usual JSON encoder truncates them).
   Compares canonical values for **every serialized field and row**, plus counts.
7. Forces deferred FK constraints to be checked, checks constraint validation,
   resets sequences through Django's backend, and verifies sequences do not trail
   imported PKs. Unique/partial-unique/check constraints are the migrated schema's
   constraints; invalid source values cause a rollback, not silent conversion.

Without `--apply`, the same command is a read-only comparison and sequence check.
No fixture/session values or per-record hashes are printed: only model counts
and pass/fail. Treat exceptions and artifacts as sensitive anyway; do not use
`--traceback`, SQL debug logging, or print serialized fixtures. All writers must
be stopped during verification. This is a small offline-data tool, not a streaming
replicator, merge tool, or production-target importer. It deliberately rejects
arbitrary destination hosts. Sequence counters may advance in tests/failed
transactions; gaps are normal, but never allow counters below imported IDs.

## Local PowerShell rehearsal

Docker Desktop must use Linux containers. These commands operate only on the
named local staging project. Do not run them on the AWS host during preparation.
Preserve existing files: fixture and password creation refuse overwriting.

```powershell
Set-Location 'C:\Users\hp\Desktop\PostMasterTelegram'
New-Item -ItemType Directory -Force '.staging/pg-input' | Out-Null

# Only if .env.postgres-staging does not exist:
python -c "from pathlib import Path; import secrets; p=Path('.env.postgres-staging'); f=p.open('x', encoding='utf-8'); f.write('PG_STAGING_PASSWORD='+secrets.token_urlsafe(32)+'\n'); f.close()"

# Synthetic users, auth/M2M links, subscription, encrypted-session placeholder,
# publishing run and logs, using a temporary SQLite DB, NOT db.sqlite3:
.\.venv\Scripts\python.exe -B -m staging.make_sqlite_fixture .staging/pg-input/source.sqlite3

function pgstage {
    docker compose --env-file .env.postgres-staging -p onepost-pg-staging -f docker-compose.postgres-staging.yml @args
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL staging command failed' }
}

pgstage config --quiet
pgstage build tools
pgstage up -d --wait staging-postgres
pgstage run --rm tools manage migrate --noinput
pgstage run --rm tools manage check --database default
pgstage run --rm tools manage pg_transfer --apply --confirm-empty-staging-target
pgstage run --rm tools manage pg_transfer
pgstage run --rm tools manage showmigrations --plan
pgstage run --rm tools manage makemigrations --check --dry-run
pgstage run --rm tools manage test --settings=config.postgres_staging_settings --noinput --verbosity=1
pgstage stats --no-stream
```

The test runner creates `test_onepost_staging`; it does not use imported rows as
test fixtures. It covers Start/Stop, ownership, subscriptions, account binding,
quota, planner compatibility and fake Telegram sends. SQLite-specific lock tests
skip on PostgreSQL; the PostgreSQL sequence/partial-unique test runs there.
`pg_transfer` separately verifies the imported persistent dataset. This proves
byte/value preservation of session ciphertext, not Telegram authorization or
ability to decrypt with a production key. Keep production keys out of staging.

Do not run `--apply` a second time on a populated target; it must refuse. Do not
use flush, drop tables, or disable constraints to make an import succeed. Use a
new local project/volume for another rehearsal. End with:

```powershell
pgstage down --remove-orphans
```

Volumes remain. No global Docker prune, broker purge, or `down -v` is needed.
Never include `.env.postgres-staging`, `.staging/`, SQLite snapshots or dumps in
Git, Docker build context, reports or shared logs. NTFS permissions and Linux
file ownership must restrict backup access; a .gitignore entry is not encryption.

## Backup and restore rehearsal

SQLite: use `python -m staging.sqlite_snapshot SOURCE_PATH NEW_BACKUP_PATH` on
an explicitly chosen database. The source connection uses `mode=ro`; output must
not exist. The SQLite backup API includes committed WAL data without copying a
live file incorrectly. It does not stop application writers. Production cutover
still requires an application-wide write freeze, not merely a consistent backup.

The mounted source must be a complete backup file, not a live WAL database.
The migration alias uses `immutable=1` and ignores sidecars. Never mount a live
source or copy only its main file while writes/WAL exist. Keep a checksum and a
second encrypted backup off the server before a future production migration.

PostgreSQL: use custom-format logical dumps, with ownership restricted and a
separate copy outside the data volume. Avoid binary redirection in Windows
PowerShell; let pg_dump write its own file, then use Compose cp:

```powershell
pgstage exec -T staging-postgres pg_dump -U onepost_staging -d onepost_staging -Fc -f /tmp/onepost.pgdump
pgstage cp staging-postgres:/tmp/onepost.pgdump .staging/onepost.pgdump
Get-FileHash .staging/onepost.pgdump -Algorithm SHA256
pgstage down

# Second isolated project; run only after first cluster is stopped to save RAM.
function pgrestorestage {
    docker compose --env-file .env.postgres-staging -p onepost-pg-restore -f docker-compose.postgres-staging.yml @args
    if ($LASTEXITCODE -ne 0) { throw 'Restore rehearsal failed' }
}
pgrestorestage up -d --wait staging-postgres
pgrestorestage cp .staging/onepost.pgdump staging-postgres:/tmp/onepost.pgdump
# Restore into the fresh initdb database; do NOT migrate it before full restore.
pgrestorestage exec -T staging-postgres pg_restore -U onepost_staging -d onepost_staging --no-owner --exit-on-error /tmp/onepost.pgdump
pgrestorestage run --rm tools manage pg_transfer
pgrestorestage down
```

The restore project must be NEW and empty. If it already contains data, stop and
choose a new project name; do not use `--clean` against an existing target. This
rehearsal restores application data and schema; PostgreSQL cluster roles and
external encryption/Django keys require their own protected backup procedures.

## Future production cutover procedure — NOT performed by this task

1. Obtain deployment approval and rehearse the exact image/driver/PostgreSQL
   version. Confirm backups can be restored, free disk, RAM headroom, permissions,
   access controls, DB role, WAL/log growth and application connection budget.
2. Stop accepting user changes, stop bot/web writers and Beat, stop publishing
   runs and let worker execution exit. Resolve in-flight/unknown sends and old
   broker queued/reserved/unacknowledged tasks as in `publishing/PHASE1.md`.
   Queue payload compatibility does not make a claimed V1 run automatically safe
   to resume against a new database. Do not purge unrelated tasks.
3. Take a final SQLite backup and record checksum, migration history, counts and
   stopped run state. Keep the original untouched. No writers may resume on it.
4. Import that backup in a protected offline rehearsal target with identical
   schema/image; validate counts, fields, FKs, permissions and sequences. Sensitive
   final data needs an explicitly approved protected environment, not a casual
   laptop copy. This staging-only command cannot point directly to production.
5. Produce a verified PG dump from that offline target; restore it to the approved
   empty production cluster during maintenance. Switch ALL services together with
   explicit PostgreSQL env and the driver-enabled image. No mixed SQLite/PG writers.
6. Check migrations/checks, auth/admin permissions, data counts, account ownership,
   subscription dates and quota. Start services in a controlled order with publishing
   initially stopped. Reconcile historical active/claimed runs explicitly; do not
   reset `task_claimed` blindly. User Start creates a new run only after old execution
   is confirmed stopped. Perform authorized operational smoke tests separately.
7. Preserve the SQLite backup and PG dump through the acceptance period. Monitor
   DB connections, lock waits, RSS, OOM events, task state and broker backlog.

Rollback BEFORE new PG writes: stop all new services, verify no running sender,
restore old image/env to the untouched SQLite source and reconcile queues/runs.
Rollback AFTER new PG writes: **do not switch to the old SQLite file**; it loses
new data. Freeze writes and recover PG from backup/repair, or perform a separately
reviewed reverse migration with reconciliation. No automatic reverse transfer or
dual writing is implemented. Database rollback is independent of future Engine V2.

## Conservative resource starting point (2 vCPU / 1.9 GiB RAM)

Current idle available memory is only about 916 MiB; DB size 308 KB does not make
PostgreSQL memory-free. The staging starting values are:

| Setting | Value |
| --- | --- |
| shared_buffers | 64 MB |
| work_mem | 1 MB per operation, not a global cap |
| maintenance_work_mem | 16 MB |
| autovacuum workers / memory | 1 / 8 MB |
| max_connections | 12 total; reserve admin slots, budget application connections |
| temp_buffers / wal_buffers | 2 MB / 4 MB |
| min_wal_size / max_wal_size | 32 MB / 128 MB (soft thresholds, not disk caps) |
| staging PostgreSQL / tools memory limits | 320 MiB / 384 MiB |

Keep fsync/full_page_writes/synchronous_commit enabled; do not trade durability for
RAM. `CONN_MAX_AGE=0` limits request persistence, but does not automatically close
connections held by long-lived bot/async threads; measure those too. Do not add
many sender processes on this host. A 320 MiB container can still OOM; this is a
test budget, not a proven production guarantee. No swap means OOM has no buffer.

With 6.7 GB free, measure Docker images/build cache, DB/WAL, logs and multiple
backups BEFORE deploying. Keep material safety headroom (e.g. 2 GB or more after
the rehearsal), but decide thresholds from actual image/volume size and growth.
Do not build images or run the full staging suite alongside live production on
this small EC2. Monitor `docker stats`, `free -m`, `df -h`, `docker system df`,
`pg_stat_activity` and `pg_database_size`; no global pruning or server changes
are part of this preparation. Increase capacity or defer cutover if headroom fails.

## Backend compatibility findings fixed during rehearsal

Real PostgreSQL tests initially exposed duplicate successful claims for one run.
Separate row-lock tests also reproduced stale cleanup stopping a newer run and
stale cursor writes replacing newer-run state. The previous joined UPDATE shapes
were safe under SQLite's serialized writer behavior but did not provide the same
fencing under PostgreSQL READ COMMITTED after waiting on a modified row.

The narrow compatibility fix keeps claim predicates (`active`, `task_claimed`)
on the updated run table, using a separate user subquery rather than a join.
Ownership-dependent cleanup/settings/legacy-recovery writes acquire the user
write lock first, then re-read current run ownership inside the transaction.
SQLite still starts these transitions with a write and keeps bounded retries.
No schema, public function signature, Telegram payload, scheduling rule, planner,
Celery task, or queue change is introduced by this fix.

New PostgreSQL tests hold row locks across Stop/Start to exercise these races.
SQLite-only retry-policy tests are explicitly skipped on PostgreSQL rather than
asserting that PostgreSQL errors should behave like SQLite lock errors. These
compatibility fixes must ship in the reviewed application image before any future
PostgreSQL production cutover; a settings-only switch on the old image is unsafe.

## Recorded validation (2026-10-29, local Docker Desktop)

- SQLite suite: 107 tests, successful, 4 PostgreSQL-only tests skipped.
- PostgreSQL 16 container suite: 107 tests, successful, 4 SQLite-only tests skipped.
- Django checks and migration consistency: passed on both backends; no new migrations.
- Synthetic SQLite -> PostgreSQL transfer: all 20 managed/through-table datasets
  matched by counts and canonical field values, including PKs, M2M link PKs,
  content types, permissions, payment decimals, datetimes, sessions and run state.
- Reapplying import to a populated staging target: explicitly refused. A following
  verification passed, confirming the refusal did not replace the imported data.
- Custom-format pg_dump copied without Windows binary redirection; restored to
  a second fresh isolated project. Full source comparison, constraints, sequences,
  Django checks and migration consistency passed after restore.
- Compose configuration and Git diff checks passed. Image build assertions excluded
  env/session/SQLite files. All Telegram calls remained disabled or mocked.
- One post-test local idle sample was about 52.73 MiB for the PostgreSQL container
  under a 320 MiB limit. This is NOT an AWS memory/load benchmark or capacity proof.
- Both rehearsal projects were shut down without deleting volumes. Synthetic input,
  local-only credentials and dump are ignored under `.staging/`/`.env.postgres-staging`.

The original AWS data and resource headroom were not inspected or changed. The
subsequent Docker-blocker fix adds a root production-effective `.dockerignore`
and PostgreSQL driver installation; `PRODUCTION_IMAGE.md` records canary and
all-layer image inspection plus tests using that production image. Runtime env
and bind mounts still require their own review. Local env, dumps and snapshots
must remain outside the image. Actual cutover requires separate approval.

References: [Django serialization](https://docs.djangoproject.com/en/5.2/topics/serialization/),
[PostgreSQL memory](https://www.postgresql.org/docs/16/runtime-config-resource.html),
[sequence semantics](https://www.postgresql.org/docs/16/functions-sequence.html).
