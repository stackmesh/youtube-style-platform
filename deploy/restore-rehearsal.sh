#!/usr/bin/env bash
# Load a backup into a scratch database and stat one mirrored object.
# Requires the operator to type: restore scratch
# Does not drop the live database or delete the live bucket.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

backup="${1:-}"
if [[ ! "$backup" =~ ^/var/backups/youtube-clone/[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "deploy: FAIL restore: pass one timestamp directory under /var/backups/youtube-clone." >&2
  exit 1
fi
if [[ ! -s "$backup/db.dump" ]]; then
  echo "deploy: FAIL restore: db.dump is missing or empty." >&2
  exit 1
fi
printf 'Type "restore scratch" to load this dump into a new database: '
read -r confirm
if [[ "$confirm" != "restore scratch" ]]; then
  echo "deploy: FAIL restore: confirmation was not received. Nothing was changed." >&2
  exit 1
fi

cid="$(compose_prod "$ROOT" ps -q postgres)"
if [[ -z "$cid" ]]; then
  echo "deploy: FAIL restore: postgres is not running." >&2
  exit 1
fi
name="restore_$(basename "$backup" | tr '[:upper:]' '[:lower:]')"
docker cp "$backup/db.dump" "$cid:/tmp/restore.dump"
docker exec "$cid" sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -c \"CREATE DATABASE ${name};\""
docker exec "$cid" sh -c "pg_restore --no-owner -d ${name} /tmp/restore.dump"
count="$(docker exec "$cid" sh -c "psql -U \"\$POSTGRES_USER\" -d ${name} -Atc \"SELECT count(*) FROM users;\"")"
docker exec "$cid" rm -f /tmp/restore.dump
printf 'scratch database %s user count %s\n' "$name" "$count"

object="$(find "$backup/media" -type f | head -n 1 || true)"
if [[ -n "$object" ]]; then
  stat -c '%n %s' "$object"
  printf 'headed mirrored object by local stat\n'
else
  printf 'media mirror has no objects to head\n'
fi
printf 'Drop the scratch database with: docker exec %s dropdb -U "$POSTGRES_USER" %s\n' "$cid" "$name"
printf 'The live database and live bucket were not modified.\n'
