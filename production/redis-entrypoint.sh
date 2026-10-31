#!/bin/sh
set -eu
password="$(cat /run/secrets/redis_password)"
case "$password" in ''|*[!0-9a-f]*) echo 'Invalid Redis secret' >&2; exit 1;; esac
exec /usr/local/bin/docker-entrypoint.sh redis-server \
    --requirepass "$password" --appendonly yes --appendfsync everysec --maxmemory-policy noeviction
