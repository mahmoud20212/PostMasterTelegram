# Local production-image verification (Docker blockers only)

The production Dockerfile now installs `requirements-postgres.txt` in addition
to the existing base requirements, without `--upgrade`, and imports psycopg at
build time. SQLite remains the default in Django. No Compose service, publishing
architecture, planner rule, production database, or AWS configuration changes.

The root `.dockerignore` applies to `Dockerfile`. It denies the context by default,
allows application packages/requirements and runtime templates/static sources,
then excludes sensitive artifacts inside those directories too. Exclusions cover
env files, private keys/credentials, SQLite/sidecars, SQL/backups/dumps, Telegram
session files, virtualenvs, Git/AWS/editor metadata, caches and host logs.
Host log files are not copied; the image creates an empty writable `/app/logs`
owned by its non-root app user. Source code must never contain hardcoded real
credentials: filename rules are not a replacement for secret review.

Dockerfile.staging has its own ignore file and remains separate. `.gitignore`
does not control build context. The original production `docker-compose.yml`
is unchanged. Its existing bind mount still supplies runtime data; do not
mistake a clean image for an audit of arbitrary host mounts or runtime env files.

## Reproduce a local build and inspect all layers

Use only a verified local Docker Desktop context. In this Windows installation,
the `default` context points to `npipe:////./pipe/docker_engine`. Do not select a
remote/AWS context. This flag is per-command and does not change global context.

```powershell
docker context inspect default --format '{{.Endpoints.docker.Host}}'
docker --context default build --builder default -f Dockerfile -t onepost-production-review:local .
docker --context default image save --output .staging/production-image.tar onepost-production-review:local
.\.venv\Scripts\python.exe -B -m staging.image_audit .staging/production-image.tar
```

`image_audit` inspects every application-layer member in a docker-save archive,
including earlier layers, and rejects known forbidden paths, credential env
variable names, and a root default user. It never extracts archive paths or
prints file contents. Base-image dependencies outside `/app` are not local
application artifacts and are not classified by application filename rules.
Keep archives in ignored `.staging/`; they can be large. Do not commit them.

Validation also places synthetic canary files under an allowlisted application
directory before a test build (env/SQLite/dump/session/key/log/cache/metadata
names). They must be absent from every layer. Remove only that uniquely-created
canary directory after checking its resolved path stays under the workspace,
then rebuild and audit the final image. Never copy real credentials as canaries.

## Run checks without starting production services

Override the image CMD; never run its default Celery worker during verification.
Use `--network none` for driver/SQLite checks. For example:

```powershell
docker --context default run --rm --pull=never --network none --entrypoint python onepost-production-review:local -c "import psycopg; print('psycopg import: PASS')"
docker --context default run --rm --pull=never --network none -e DJANGO_DEBUG=True --entrypoint python onepost-production-review:local manage.py check
docker --context default run --rm --pull=never --network none --entrypoint python onepost-production-review:local manage.py test --settings=config.migration_fixture_settings --verbosity=1
```

These checks do not use real secrets. The SQLite test settings create a temporary
DB and destroy it; the default settings still select `/app/db.sqlite3` unless
PostgreSQL is explicitly enabled. Persist a real development SQLite DB via the
existing runtime mount; an ephemeral image/container is not a database backup.

For PostgreSQL, use the standalone isolated setup in `POSTGRES_STAGING.md`, with
a local override containing only:

```yaml
services:
  tools:
    image: onepost-production-review:local
    build: !reset null
```

Use both staging/override `-f` arguments, explicit project `onepost-pg-staging`,
and `run --no-deps --pull never --rm tools ...` (start the isolated DB first).
The local Compose version must support `!reset`; it removes the staging build
definition so the validation service cannot build a different image. This tests the production image
with the existing guarded staging entrypoint, read-only synthetic SQLite input,
and isolated PostgreSQL. Run system/migration checks and the full test suite
with `config.postgres_staging_settings`; do not run `--apply` on populated data.
Use a live `SELECT 1` connection probe and read-only `pg_transfer` verification.
No production Compose commands, migrations, web, bot, worker or Beat are started.

Compare installed package versions with the prior validated staging image to
confirm no unrelated upgrades. Retain the final image ID and test/layer-audit
results; use that reviewed image for a later approved preparation stage.

Passing these image checks removes the two Docker blockers. Actual cutover still
requires its own approval, consistent backup/write freeze, run/queue reconciliation,
production role/secrets review, memory/disk headroom and rollback gates documented
in `POSTGRES_STAGING.md`. No deploy or PostgreSQL production migration is performed.

## Recorded local verification (2026-10-29)

- Final image ID: `sha256:343fbd0bda94bd1e8723479db33024fa8016f12167099f55e7c3cb229d60114b`.
- Canary build and final rebuild both passed the complete application-layer audit.
  The final archive contained 12 layers / 198 application entries, with no forbidden
  artifact paths or baked credential variables. The default user is non-root.
- psycopg import passed; package versions exactly matched the previously validated
  PostgreSQL staging image. No unrelated packages were upgraded.
- SQLite remained the default. System checks and migration consistency passed.
- Inside this production image: SQLite suite 109 tests, 105 passed / 4 backend
  skips; PostgreSQL suite 109 tests, 105 passed / 4 backend skips.
- The production image connected to the isolated PostgreSQL DB with `SELECT 1`.
  Its system/migration checks and read-only synthetic migration-data verification
  passed. Telegram was disabled/mocked; the image's default worker was never run.
- The local staging project was shut down without deleting volumes. Archives,
  canary metadata and test override files remain ignored under `.staging/`.

These results remove the two image blockers for the next approved preparation
stage; they do not authorize AWS access or a production cutover.
