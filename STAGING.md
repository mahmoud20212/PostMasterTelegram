# OnePost: local Docker staging (Phase 1 / 1.1)

This is a standalone Compose project, not a production override. Use **only**
`docker-compose.staging.yml` with project name `onepost-staging`. Do not combine
it with `docker-compose.yml`, copy production `.env`, restore a production DB,
or provide real Telegram/AWS credentials.

## Isolation and resource budget

- A separate Redis service and project-scoped named volumes hold Redis and
  `/staging-data/db.sqlite3`. No host directories or Docker socket are mounted.
- The network is `internal: true`. No host ports are published. Django listens
  only inside that network; there is no bot or Beat service.
- The staging entrypoint rejects Telegram/AWS credentials and external Redis,
  disables dotenv loading, and replaces `telethon.TelegramClient` with a
  constructor that always fails. Tests explicitly replace it with mocks.
  Even an accidentally queued publishing task cannot create a real client.
- Settings use dummy local-only Django credentials. Never use these settings
  for a deployed public service. Staging settings are not loaded in production.
- The dedicated Dockerfile has its own deny-by-default build ignore file;
  `.env`, SQLite databases, session files, `.git`, `.aws`, and virtualenvs are
  excluded. The existing production Dockerfile/Compose are unchanged.
- Django and worker have 512 MiB each; Redis has 192 MiB with 96 MiB cache limit
  and `noeviction`. Tests use one extra app container; stop web/worker first.
  This leaves headroom within about 3.7 GiB for Docker/WSL. Build-time memory
  use is not constrained by service limits; build before starting services.
- Runtime files live in named volumes or tmpfs; Git ignores local staging env,
  session files, SQLite sidecars, and `.staging/`. Only the env example is tracked.

## Windows PowerShell commands

Open Docker Desktop manually and select Linux containers/WSL 2. Starting Docker
Desktop may resume existing containers with restart policies: check those
separately before a rehearsal. Do not stop unrelated containers.

Run from the repository root:

```powershell
Set-Location 'C:\Users\hp\Desktop\PostMasterTelegram'
git status --short
docker version
docker compose version

if (-not (Test-Path -LiteralPath '.env.staging')) {
    Copy-Item -LiteralPath '.env.staging.example' -Destination '.env.staging'
}

function dcs {
    docker compose --env-file .env.staging -p onepost-staging -f docker-compose.staging.yml @args
    if ($LASTEXITCODE -ne 0) { throw 'Staging command failed; stop and inspect its output.' }
}

dcs config --quiet
dcs build django
dcs up -d staging-redis
dcs run --rm --no-deps django manage migrate --plan
dcs run --rm --no-deps django manage migrate --noinput
dcs run --rm --no-deps django manage check
dcs run --rm --no-deps django manage makemigrations --check --dry-run
dcs up -d django worker
dcs ps
dcs run --rm --no-deps django manage staging_probe
dcs logs --tail=100 worker django staging-redis
```

`build django` builds the shared `onepost-staging:local` image used by both
services. The probe must return `telegram_disabled: true`, the staging DB path,
and queue `onepost-staging`. This checks a real Redis -> Celery worker round trip
without messages or account fixtures. Timeout/failure is not success: inspect
logs and do not start publishing manually to troubleshoot it.

Optional internal HTTP check (there is intentionally no host URL):

```powershell
dcs exec django python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/admin/login/').status)"
```

## Tests: concurrency, Stop/Start, recovery, and safeguards

```powershell
dcs stop worker django
dcs run --rm --no-deps django manage test publishing staging --settings=config.test_settings --verbosity=2
```

The test settings use a temporary file-backed SQLite DB inside the disposable
container, not the persistent staging DB. They apply migrations to that test DB.
The publishing suites cover independent DB connections for concurrent Start
and claim, Stop -> Start, account binding, independent users, broker stalls and
lost replies, write locks, expiry, FloodWait/SlowMode, lifecycle cleanup,
legacy migration state, and scoped administrative recovery. Telegram is mocked.
The in-memory Celery test is not a Redis crash test; the separate probe above
checks Redis task transport only.

To run the complete suite:

```powershell
dcs run --rm --no-deps django manage test --settings=config.test_settings --verbosity=1
dcs up -d django worker
```

Do not seed real sessions. Manual recovery rehearsals need synthetic users and
run UUIDs. The automated tests create these safely and assert that the recovery
command refuses missing confirmation, another user's UUID, and stale UUIDs.
For a separate persistent fixture, stop the staging worker and any producer,
verify process exit, then use only a known synthetic UUID:

```powershell
dcs stop worker
dcs ps -a
# Replace placeholders only after verifying the stopped execution and fixture.
dcs run --rm --no-deps django manage recover_publishing_run SYNTHETIC_USER_ID --run-id SYNTHETIC_RUN_UUID --confirm-execution-stopped
```

This command does not create fixtures, restart a task, or reset its claim. Do not
pass the confirmation while a worker could still execute the target run.

## Logs and safe shutdown

```powershell
dcs logs --tail=200 worker django staging-redis
dcs stop worker django
dcs down --remove-orphans
```

`down` preserves the two staging volumes. It does not touch another Compose
project. Do not use global Docker prune or Redis purge. If a disposable reset is
explicitly desired later, first verify the project/volume labels; removing
volumes destroys staging records and is not part of the routine shutdown above.

## Limits / acceptance gates

- Docker Desktop/Engine must be running. Compose syntax validation alone does
  not prove images build, volume ownership works, or worker transport works.
- The build downloads the base image and requirements from package registries.
  Runtime services have no external network. Requirements remain ranged as in
  the project; record `pip freeze` inside the image for a reproducible rehearsal.
- `solo` remains unchanged. This rehearsal does not prove simultaneous live
  publishers or capacity for hundreds of users. SQLite still serializes writes.
- No real Telegram delivery, FloodWait response, or network authorization is
  tested. Those are mocked; attempting real clients is intentionally rejected.
- Do not kill the worker or interrupt Redis during the test suite. A real
  process-crash/Redis-interruption/rollback rehearsal needs explicit synthetic
  fixtures and an operator-controlled scenario. This task provides safe setup,
  mock coverage, and a transport probe, not evidence those failure drills passed.
- Before a production decision, rehearse the queued/reserved/active/unacknowledged
  rollback procedure in `publishing/PHASE1.md` with synthetic fixtures. No broker
  purge is required or recommended here. No changes to the publishing architecture.

## Validation recorded during preparation

Docker Compose configuration validation passed locally. Docker Engine was not
available (missing Docker Desktop Linux engine named pipe), so image build,
container migrations, volume permissions, network isolation at runtime, HTTP,
and the Redis probe could not be executed. Run the commands above after starting
Docker Desktop; do not treat these pending checks as passed.

The local publishing + staging safety suites passed: **54 tests**. Django system
checks, migration consistency, and `git diff --check` also passed. These were
Windows virtualenv checks, not container test results. The five staging tests
exercise opt-in, credential/broker rejection, dotenv suppression, and the
Telegram constructor guard, including a fresh engine import in a subprocess.

Dockerfile-specific ignore-file behavior follows the
[Docker build-context documentation](https://docs.docker.com/build/concepts/context/#dockerignore-files).
The image build also asserts that migrations/probe source exist and that no env,
SQLite, or session files were copied.
