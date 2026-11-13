# Backup and restore

Backups live in `/var/backups/youtube-clone/<UTC timestamp>/` with mode `700` on the directory and `600` on the dump and the media manifest. Set `BACKUP_ROOT` to another absolute directory when that path is not writable. The value must not contain `..` or a trailing slash. The timestamp directory name is the only path component the scripts accept under that root.

The media mirror includes `media.manifest.json` with the key, size, and SHA-256 of every object. `deploy/restore-rehearsal.sh` checks those hashes, loads the dump into a new database, copies the objects into a new bucket, reads the user count through SQLAlchemy, and reads one restored object through the application storage client. The application identity can read only the `media` bucket, so that object read uses a unique `acceptance/restore-probe` key which the rehearsal deletes before it exits. The scratch database and scratch bucket are removed at the end. The live database and the previous media objects are not deleted.

`bash deploy/backup.sh` refuses to run unless Postgres is ready. It runs `pg_dump -Fc` with the PostgreSQL 16 client inside the database container, checks the archive with `pg_restore -l`, and copies the private `media` bucket to `media/` in the same directory. The live database is not dropped, truncated, or overwritten. Retention keeps the newest 14 timestamp directories. A sample cron line is printed at the end.

The dump does not contain object bytes. Restore the database and the bucket mirror from the same timestamp. Restoring one without the other leaves videos whose rows and objects disagree.

## Restore

Restore is manual. There is no restore flag on `deploy/ubuntu-up.sh`.

1. Stop writes with `bash deploy/ops.sh shutdown` on the stack you are replacing. Do not delete its volumes until the new database and bucket have been checked.
2. Create a new empty database. Do not pass `--clean` against the live database.
3. `pg_restore --no-owner -d <new-database> db.dump` using the PostgreSQL 16 client. A host `pg_restore` older than the dump format cannot read the archive.
4. `SELECT count(*) FROM users` and `SELECT version_num FROM alembic_version`.
5. Point a disposable `DATABASE_URL` at that database and run `alembic current`.
6. Create a new private bucket, or restore into a fresh SeaweedFS volume, and upload the mirrored keys. `HEAD` one object before sending traffic.
7. Swap the application to the new database and bucket only after those checks. Drop the old database only when the operator has confirmed the new one.

The rehearsal helper `deploy/restore-rehearsal.sh <backup-dir>` loads the dump into a scratch database on the running Postgres container, counts `users`, and heads one mirrored object with a local file stat. It asks for the confirmation text `restore scratch` before it creates the scratch database. It does not drop the live database and does not delete the live bucket.

## Consistency

Take the backup while the API is up. A transaction committed after `pg_dump` starts can be absent from the dump while its object is already in the mirror, or the reverse. After restore, a processing job that was running can be requeued by the existing sweeper because its lock expires. That is the accepted recovery behavior. Quiesce uploads with `bash deploy/ops.sh shutdown` before a backup you intend to restore as a clean cut.
