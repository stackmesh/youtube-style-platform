#!/usr/bin/env bash
# Operational commands for the production project. None of these delete volumes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

cmd="${1:-status}"
case "$cmd" in
  status)
    compose_prod "$ROOT" ps
    ;;
  health)
    curl -fsS "http://127.0.0.1:${HTTP_PORT:-80}/health/ready"
    printf '\n'
    ;;
  logs)
    compose_prod "$ROOT" logs --tail 80 "${2:-api}"
    ;;
  backup)
    bash "$ROOT/deploy/backup.sh"
    ;;
  update)
    bash "$ROOT/deploy/backup.sh"
    compose_prod "$ROOT" up -d --build --remove-orphans
    curl -fsS "http://127.0.0.1:${HTTP_PORT:-80}/health/ready"
    printf '\n'
    printf 'Update finished. Application rollback is a previous git revision plus this command. Database rollback is a restore, not an Alembic downgrade.\n'
    ;;
  migrate)
    compose_prod "$ROOT" exec -T api python -m app.db.migrate
    ;;
  shutdown)
    compose_prod "$ROOT" stop
    printf 'Stopped. Volumes were kept. Start again with bash deploy/ubuntu-up.sh\n'
    ;;
  disk)
    df -h "$ROOT" /var/lib/docker /var/backups 2>/dev/null || df -h "$ROOT"
    docker system df
    ;;
  rollback)
    printf 'Application rollback: check out the previous release and run bash deploy/ops.sh update.\n'
    printf 'Database rollback: restore a backup into a new database. Do not downgrade Alembic on the live database.\n'
    printf 'See docs/BACKUP_AND_RESTORE.md.\n'
    ;;
  *)
    printf 'Usage: bash deploy/ops.sh status|health|logs|backup|update|migrate|shutdown|disk|rollback\n' >&2
    exit 1
    ;;
esac
