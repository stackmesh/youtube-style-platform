#!/usr/bin/env bash
# Timestamped PostgreSQL custom dump and a mirror of the private media bucket.
# Does not delete or overwrite the live database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

fail() {
  fail_line "$1" "$2"
  exit 1
}

ready="$(curl -fsS "http://127.0.0.1:${HTTP_PORT:-80}/health/ready" 2>/dev/null || true)"
if [[ "$ready" != *'"postgres":"ok"'* ]]; then
  fail backup "PostgreSQL is not ready. Start the stack and run this again. Nothing was deleted."
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="/var/backups/youtube-clone"
if [[ ! "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  fail backup "The timestamp ${stamp} is not a safe directory name."
fi
dest="${base}/${stamp}"
case "$dest" in
  /var/backups/youtube-clone/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) fail backup "Refusing to write outside /var/backups/youtube-clone." ;;
esac
if [[ -e "$dest" ]]; then
  fail backup "${dest} already exists. Wait one second and run the backup again."
fi
mkdir -p "$dest"
chmod 700 "$base" "$dest"

cid="$(compose_prod "$ROOT" ps -q postgres)"
if [[ -z "$cid" ]]; then
  fail backup "The postgres container is not running. Nothing was deleted."
fi
if ! docker exec "$cid" sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB" -f /tmp/db.dump && pg_restore -l /tmp/db.dump > /tmp/restore-list.txt'; then
  fail backup "pg_dump or pg_restore -l failed. ${dest} was kept for inspection. The live database was not changed."
fi
docker cp "$cid:/tmp/db.dump" "$dest/db.dump"
docker cp "$cid:/tmp/restore-list.txt" "$dest/restore-list.txt"
docker exec "$cid" rm -f /tmp/db.dump /tmp/restore-list.txt
chmod 600 "$dest/db.dump" "$dest/restore-list.txt"
if [[ ! -s "$dest/db.dump" || ! -s "$dest/restore-list.txt" ]]; then
  fail backup "The archive or its restore list is empty. ${dest} was kept. The live database was not changed."
fi

api_cid="$(compose_prod "$ROOT" ps -q api)"
if [[ -z "$api_cid" ]]; then
  fail backup "The API container is not running, so the bucket was not mirrored. The database archive is in ${dest}."
fi
docker exec "$api_cid" python -m app.infrastructure.mirror_bucket /tmp/media-backup
docker cp "$api_cid:/tmp/media-backup" "$dest/media"
docker exec "$api_cid" rm -rf /tmp/media-backup
chmod -R go-rwx "$dest/media" || true

mapfile -t old < <(find "$base" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
keep=14
if [[ "${#old[@]}" -gt "$keep" ]]; then
  remove=$((${#old[@]} - keep))
  for name in "${old[@]:0:remove}"; do
    if [[ "$name" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
      rm -rf "${base}/${name}"
    fi
  done
fi

printf 'deploy: OK backup %s\n' "$dest"
printf 'Sample cron, daily at 02:15 server local time, retention 14 dumps:\n'
printf '15 2 * * * cd %s && bash deploy/backup.sh >> /var/log/youtube-clone-backup.log 2>&1\n' "$ROOT"
printf 'Restore is manual. Read docs/BACKUP_AND_RESTORE.md. Do not pass a restore flag to ubuntu-up.sh.\n'
