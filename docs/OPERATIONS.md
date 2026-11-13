# Operations

All commands assume the checkout that owns `.env` and the `youtube-prod` Compose project.

| Task | Command |
| --- | --- |
| Status | `bash deploy/ops.sh status` |
| Readiness | `bash deploy/ops.sh health` |
| Logs | `bash deploy/ops.sh logs api` |
| Backup | `bash deploy/backup.sh` |
| Apply an update | `bash deploy/ops.sh update` |
| Migrate only | `bash deploy/ops.sh migrate` |
| Stop, keep volumes | `bash deploy/ops.sh shutdown` |
| Disk | `bash deploy/ops.sh disk` |
| Rollback guidance | `bash deploy/ops.sh rollback` |

`update` takes a backup, rebuilds images, starts the stack, and waits for `/health/ready`. It does not delete media or run `down -v`.

Application rollback checks out the previous Git revision and runs `update` again. That does not undo a committed database migration. Database rollback restores a backup into a new database, as described in [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md). Do not run `alembic downgrade` on the live database.

A migration that fails before commit is rolled back by PostgreSQL. The API does not listen, and the installer prints `deploy: FAIL migrate`. A migration that committed stays committed. Recovery is restore-from-backup.

Graceful stop sends SIGTERM and waits for the Compose stop grace period. The worker finishes its signal handler and stops claiming jobs. The API stops after the entrypoint process receives the signal.

Troubleshooting:

- `deploy: FAIL privilege` means the shell is not root and `sudo -n` failed.
- `deploy: FAIL exposure` means a private port is listening on a public address. Remove that host publish.
- Ready stays 503 until the worker heartbeat is fresh. `bash deploy/ops.sh logs worker` should show `worker heartbeat updated`.
- Presigned playback needs `s3.<domain>` to resolve to this server. Demo mode uses `s3.localhost`, which must resolve to the edge.
