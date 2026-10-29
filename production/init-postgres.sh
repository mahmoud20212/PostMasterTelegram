#!/bin/sh
set -eu
# Only the official image's first-boot init runs this, on a NEW volume.
# Read values in psql without shell interpolation or SQL string concatenation.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\set app_password `cat /run/secrets/postgres_password`
CREATE ROLE onepost_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
REVOKE ALL ON DATABASE onepost_fresh FROM PUBLIC;
GRANT CONNECT ON DATABASE onepost_fresh TO onepost_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO onepost_app;
SQL
