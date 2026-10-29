# Fresh PostgreSQL production deployment (manual cutover only)

This is a NEW, EMPTY OnePost installation, not a data migration. No old users,
subscriptions, encrypted Telegram sessions, groups, runs or history are imported.
V1 publishing and its solo worker remain unchanged. Never merge this Compose file
with another file, reuse staging credentials, or set external volumes/networks.

## Files and trust boundaries

`docker-compose.production.yml` uses project `onepost-fresh`, PostgreSQL 16,
Redis 7, one Gunicorn worker, bot, solo Celery worker and Beat. There is no build
directive and no repository mount. Only web binds `127.0.0.1:18000`; configure a
trusted host TLS reverse proxy separately. PostgreSQL/Redis have no host ports
and only join the internal data network. Application services also need outbound
internet for Telegram. Do not attach other containers to the data network.
Redis has no password: isolation assumes a trusted Docker host/network boundary.

Secrets are file mounts from `.production/secrets/`, never Compose environment
values or image contents. The entrypoint reads them at runtime. Docker/root can
still inspect process environments: restrict host/Docker access. Never print
secret files, `docker inspect` process environments, DB rows/session values, or
verbose traces into tickets. Do not enable shell tracing. Local Compose secrets
are NOT an encrypted secrets vault. Protect off-host copies and backups.

The bootstrap PostgreSQL administrator is available only to PostgreSQL; apps
receive `onepost_app` with LOGIN, CONNECT and schema USAGE/CREATE, but no
SUPERUSER/CREATEDB/CREATEROLE/REPLICATION. The app owns its migrated objects;
this is not a separate restricted DML-only runtime role. PostgreSQL initialization
runs only against a new volume. A partial failed first initialization must be
investigated/restored by an operator; restarting does not rerun init scripts.
Changing a password file does not change an existing database role password.

## Build and transfer on a workstation (PowerShell)

Use a clean reviewed checkout with the intended dependency changes resolved.
Do not silently ship unrelated working-tree edits. Match EC2 CPU architecture
(`linux/amd64` for x86 or `linux/arm64` for Graviton) before building/pulling.

```powershell
docker --context default build --builder default --platform linux/amd64 -t onepost-fresh:release -f Dockerfile .
New-Item -ItemType Directory -Force .production | Out-Null
docker --context default save -o .production/application.tar onepost-fresh:release
.venv/Scripts/python.exe -m staging.image_audit .production/application.tar
docker --context default pull --platform linux/amd64 postgres:16-alpine
docker --context default pull --platform linux/amd64 redis:7-alpine
docker --context default save -o .production/infrastructure.tar postgres:16-alpine redis:7-alpine
Get-FileHash .production/application.tar,.production/infrastructure.tar -Algorithm SHA256
.venv/Scripts/python.exe -m unittest production.test_configuration -v
```

Record immutable image IDs/digests and archive checksums for the release. Transfer
archives and only Compose, this runbook, `.env.production.example` and
`production/init-postgres.sh` into a NEW server directory using the operator's
approved secure transfer method. Ensure init script uses LF line endings. Transfer
no old `.env`, repository, staging volumes or Telegram session files. Do not build
on EC2. Check disk space for BOTH archives, extracted images, old images, volumes
and backup headroom first. Transfer/load one archive at a time if necessary;
move archives off-server after checksum verification. Do not prune old assets.

## Server preparation (Ubuntu; commands are instructions, not executed here)

Keep the old deployment running during planning. Before adding the full new
stack, check `free -m`, `df -h`, `docker stats --no-stream` and `docker system df`.
The configured memory ceilings total approximately 1312 MiB excluding Docker,
kernel and old services. They are limits, not reservations. With only 916 MiB
available and no swap, running both full stacks is unsafe. Arrange a maintenance
window; stop old application processes ONLY at the later handoff. If the new
stack lacks headroom, resize the host or arrange operator-approved swap first.
Do not treat these limits as measured capacity guarantees. PostgreSQL allows 20
connections; connections close after requests. Redis uses 48 MiB maxmemory with
noeviction in a 96 MiB container. Queue/result growth may cause write failures;
monitor it. V1 long-running tasks still serialize users. Do not increase workers
or change the publishing engine here.

```bash
umask 077
mkdir -p .production/secrets
cp .env.production.example .env.production
# Edit allowed hosts and image tag only; leave handoff=pending.
docker load -i .production/application.tar
docker load -i .production/infrastructure.tar
docker image inspect onepost-fresh:release --format '{{.Id}}'
```

Create these seven nonempty UTF-8 files with no BOM, readable by container UID
1000 (and by the PostgreSQL image user for its two secrets). Compose bind secrets
may ignore uid/mode metadata; verify actual access. A dedicated root-controlled
deployment directory with restrictive parent ACLs is required. Do not make
secrets world-accessible just to fix a permissions error.

On Ubuntu, one option is root-owned files with mode 0600 in a mode-0700 parent,
then POSIX ACL read grants to UID 1000 for application files, and to the actual
PostgreSQL image UID for the two PostgreSQL files. Discover that UID with
`docker run --rm --entrypoint id postgres:16-alpine postgres` (no services started).
Use `setfacl` only after reviewing the UID and file list. Docker mounts individual
files, so the container does not need traversal access to the host secret parent.
Verify readability from each intended container without displaying contents.
The bootstrap secret must never be granted to application services.

* `postgres_admin_password`: newly generated random bootstrap password.
* `postgres_password`: DIFFERENT new random application password.
* `django_secret_key`: new high-entropy Django key.
* `telegram_bot_token`: a distinct test/new bot token, OR the old bot token only
  after exclusive polling ownership is established at handoff.
* `telegram_api_id`, `telegram_api_hash`: approved Telegram application credentials.
* `telegram_session_encryption_key`: NEW Fernet key; keep a protected backup.

Use a secure password manager/editor or file-writing generator; never put secret
values in shell arguments/history. Generate Fernet with cryptography's
`Fernet.generate_key()` and write directly to the file. Never import old session
records into this fresh installation. The admin and app passwords must not be
the old staging superuser password.

Define a shell helper in the NEW directory to prevent accidental file merging:

```bash
dc() { docker compose --env-file .env.production -p onepost-fresh -f docker-compose.production.yml "$@"; }
dc config --quiet
# Verify absence of existing fresh volumes; do not proceed if they contain data.
docker volume ls --filter name=onepost-fresh
# Start only new infrastructure, when sufficient RAM/disk is available.
dc up -d --wait fresh-postgres fresh-redis
dc run --rm --no-deps web python -m production.verify
dc run --rm --no-deps web python manage.py check --deploy
dc run --rm --no-deps web python manage.py makemigrations --check --dry-run
dc run --rm --no-deps web python manage.py migrate --plan
dc run --rm --no-deps web python manage.py migrate --noinput
dc run --rm --no-deps web python manage.py showmigrations --plan
dc run --rm --no-deps web python manage.py createsuperuser
dc up -d --wait web
dc ps
dc logs --tail 50 web fresh-postgres fresh-redis
```

Review `check --deploy` warnings, configure HTTPS proxy, allowed hosts, secure
cookies and an appropriate HSTS policy before public traffic. TLS must set
`X-Forwarded-Proto: https`; do not expose Gunicorn directly. Static files are
collected per web start into writable tmpfs and served by WhiteNoise. Logs go to
stdout with Docker rotation (5 MiB x2 per service). `/app/logs` is writable tmpfs
for base settings initialization. Beat state is disposable `/tmp`; restarting
Beat can repeat periodic expiration checks (existing task semantics). Health
checks validate DB identity/privileges and Redis reachability; they do not prove
Telegram access or publishing progress. Docker restart policies restart exited
containers, not merely unhealthy ones; monitor unhealthy status/OOMs externally.

## Handoff: manual, maintenance window

Before handoff, test the admin login through TLS, static assets, fresh empty
application tables and subscription setup. No live profile is needed for these.
Do not enqueue publishing jobs during preflight. Local automated tests mock
Telegram; a real message rehearsal requires separate explicit authorization.

1. Record old deployment directory/project, image IDs, proxy configuration, users
   and active runs. Disable new Start requests and stop old bot polling first.
2. Stop publishing through existing Stop operations and allow in-flight sends to
   complete. A send already accepted by Telegram cannot be undone. Stop old Beat
   and gracefully stop old worker; V1 tasks can be long-lived. Do not blindly kill
   a sending worker: delivery outcome may be ambiguous. Resolve uncertain runs
   manually before accepting duplicate-message risk.
3. Verify ALL old bot/worker/Beat processes, including other hosts/process managers,
   have exited. Back up old SQLite with SQLite's online backup API after writers
   stop (not a live raw copy ignoring WAL). Back up old env/encryption key securely
   and preserve old Redis volume/queue state. Do not delete/drain it silently.
4. Old queued tasks and run IDs belong to the old DB only. Never attach old Redis
   or replay its queue into the fresh stack. Keep old application services stopped
   while the new token owner is active. Stop old web to free memory if necessary.
5. Only after exclusive token ownership is verified, edit `.env.production` to
   `ONEPOST_POLLING_HANDOFF=confirmed-exclusive-token`. With a DIFFERENT token,
   old polling can coexist logically, but RAM constraints still apply.
6. `dc --profile live up -d --wait` starts the new bot/worker/Beat. Inspect logs
   privately, check health and process counts, then switch the TLS proxy upstream
   to `127.0.0.1:18000`. Verify command/button behavior, account reconnection,
   subscription configuration, and controlled Start/Stop only in an approved
   message rehearsal. Users must re-register/reconnect; no entitlements transfer.

The profile and runtime acknowledgement prevent default accidental startup. They
are an operator gate, NOT a distributed lock against the old bot or arbitrary
`docker exec` commands. No new deployment can guarantee exclusivity against an
unchanged old instance; an operator must verify it. Telegram conflict errors are
not an acceptable coordination mechanism. Never scale bot, worker or Beat.

## Backup and rollback

Back up new PostgreSQL and encryption keys before accepting meaningful traffic:

```bash
dc exec -T fresh-postgres pg_dump -U onepost_bootstrap -d onepost_fresh -Fc > .production/fresh-backup.pgdump
# Check archive structure without printing table data.
dc exec -T fresh-postgres pg_restore --list < .production/fresh-backup.pgdump > .production/fresh-backup-index.txt
```

These commands use Unix shell binary redirection. Protect/encrypt backups and
copy off-host; they contain sensitive data. Restore rehearsals must target a
separate empty recovery database/project, never the old SQLite or an occupied
fresh DB. Keep role credentials/ownership compatible and use `pg_restore
--exit-on-error --single-transaction`; inspect the plan first. Do not delete a
volume to repair a failed restore. Test restore before relying on a backup.

Rollback is NOT data synchronization: new accounts/subscriptions/history will
not appear in old SQLite. Announce the consistency boundary before cutover.

1. Disable new Starts; Stop new publishing through the bot and resolve in-flight
   outcomes. Stop new bot and Beat, then gracefully stop worker and web using
   `dc --profile live stop bot celery-beat celery-worker web`. If a worker cannot
   stop cleanly, investigate before allowing either stack to send again.
2. Verify new polling and sending processes have stopped. Back up new PostgreSQL
   and keys; preserve both PostgreSQL and Redis volumes and all logs. Stop new
   infrastructure if needed for RAM with `dc stop fresh-postgres fresh-redis`.
   Never use `down -v`, volume deletion, or system prune.
3. Restore the old proxy upstream and old deployment ONLY after reviewing old
   queued tasks and active-run flags. Old Redis can still contain pending tasks;
   restarting its worker blindly may resume unwanted sends. Use the old release's
   documented Stop/reconciliation procedure or keep its worker paused until safe.
4. Restart only the old token owner, verify smoke tests, and document lost/new
   writes. A future attempt must inspect preserved fresh volumes; this runbook
   never automatically wipes or reimports them.

## Local-only validation

`production.test_configuration` renders Compose without starting services and
checks no public DB/Redis ports, isolated volumes/networks, non-root apps, limits,
secret references and fail-closed live gates. `python -m production.verify` tests
actual SQL and Redis connectivity and asserts the app role is not privileged.
Use a different project such as `onepost-fresh-localcheck`, throwaway secret files
and an override removing web ports for local rehearsals. Never enable `live` or
use real Telegram credentials. Run existing tests with `config.test_settings`
for isolated SQLite; PostgreSQL tests require a separate test database with a
test-only CREATEDB role, NOT production's restricted app role. Do not grant
CREATEDB to production just to run Django's test runner.

## Recorded local validation

The `onepost-fresh-localcheck` project used fresh local-only secret files and
volumes, no public web port, and no bot/worker/Beat startup. The production image
built successfully; app-role SQL/Redis checks, new-database migrations, migration
consistency and admin login HTTP checks passed. `check --deploy` returned only
security.W004 (HSTS remains a TLS deployment decision). Host SQLite suite: 112
tests, 108 passed / 4 PostgreSQL-only skips. Host Compose safety suite: 3 passed.
Guarded PostgreSQL publishing/staging suite: 93 tests, 89 passed / 4 SQLite-only
skips. No real Telegram clients or messages were used. Final image layer audit
also passed; local rehearsal services were stopped with volumes retained.
Temporary test-only bootstrap credentials are permitted only for the isolated
Django test database; the real services continue to use `onepost_app`.
