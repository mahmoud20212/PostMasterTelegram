#!/bin/sh
set -eu
APP_DB_PASSWORD="$(cat /run/secrets/db_password)"
export APP_DB_PASSWORD
psql --username "$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1 <<'SQL'
\getenv app_password APP_DB_PASSWORD
SELECT format('CREATE ROLE onepost LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE', :'app_password') \gexec
CREATE DATABASE onepost OWNER onepost;
SQL
unset APP_DB_PASSWORD
