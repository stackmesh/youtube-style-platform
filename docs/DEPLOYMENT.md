# Deployment

Supported hosts are Ubuntu 22.04 and 24.04 on `x86_64` or `aarch64`. The installer needs root or passwordless sudo, systemd, 20 GiB free on the first run, 2 GiB free on a repeat run, and at least 2 GiB of RAM. Below 4 GiB it warns and continues.

Development stays on `compose.yaml` and the Vite dev server. Production uses `deploy/compose.prod.yaml`, built frontend assets inside the Caddy image, and no published ports for PostgreSQL, Redis, or SeaweedFS.

## Install

Clone the repository to `/opt/youtube-clone`. Point DNS `A` or `AAAA` records for the site and `s3.<domain>` at the server before requesting certificates.

```bash
cd /opt/youtube-clone
sudo PUBLIC_DOMAIN=videos.example.com ACME_EMAIL=you@example.com bash deploy/ubuntu-up.sh
```

A local HTTP demo, which does not request a certificate and must not be exposed to the internet:

```bash
sudo DEMO_HTTP=1 bash deploy/ubuntu-up.sh
```

Success prints `deploy: OK <url>`. Failure prints `deploy: FAIL <code>: <what failed>. <what to do>.` and exits non-zero. The script does not continue after a failed check, does not run `docker compose down -v`, and does not delete volumes.

Run it again after pulling a new revision. An existing `.env` and `.env.seaweed-admin` are left in place. If `.env` exists and `pg_data`, `redis_data`, or `seaweed_data` is missing, the script stops with `deploy: FAIL volume` instead of creating an empty database.

`DEPLOY_CONFIGURE_UFW=1` allows OpenSSH, 80/tcp, and 443/tcp. Without that flag the host firewall is not changed.

## Configuration

Generated files are mode `600`. `JWT_SECRET`, the database password, the media key, and the storage-admin key are random. `S3_ADMIN_ACCESS_KEY` and `S3_ADMIN_SECRET_KEY` are only in `.env.seaweed-admin`. The API process exits if it sees them.

`CORS_ORIGINS` equals `PUBLIC_APP_URL`. TLS mode requires `COOKIE_SECURE=true`, `PUBLIC_APP_URL=https://<domain>`, and `S3_PUBLIC_ENDPOINT_URL=https://s3.<domain>`. `TRUSTED_HOSTS` lists the site host and the `s3` host. A new install writes `DOCS_ENABLED=false`. Caddy answers `/docs`, `/redoc`, and `/openapi.json` with HTTP 404, so the frontend fallback does not serve those paths.

An existing `.env` with `DOCS_ENABLED=true` makes the installer stop before it changes secrets or starts containers. The message is `deploy: FAIL docs`. To open Swagger for a maintenance window, set `DOCS_ENABLED=true` and run `DOCS_OPT_IN=1 bash deploy/ubuntu-up.sh`. When maintenance is finished, set `DOCS_ENABLED=false` and run the installer again. `DOCS_OPT_IN` is not written into `.env`.

Demo mode is the only place `COOKIE_SECURE=false` is accepted.

## Firewall

The installer does not change UFW unless `DEPLOY_CONFIGURE_UFW=1`. With that flag it sets the incoming policy to deny and the outgoing policy to allow, then allows OpenSSH, 80/tcp, and 443/tcp. PostgreSQL, Redis, and SeaweedFS ports are not allowed. Compose does not publish those ports.

## Images

| Image | Pin |
| --- | --- |
| PostgreSQL | `postgres:16.16-alpine@sha256:0adbf25a9e96d38823bb813f3ac0ed41fadb665c88f1df7fe6dbfb060cbdc106` |
| Redis | `redis:7-alpine@sha256:7b1753b4c5ad8458906c272a8132b8c15f5557b000379eb115fb239706a384b7` plus `apk upgrade zlib` in `deploy/redis/Dockerfile` |
| SeaweedFS | `chrislusf/seaweedfs:4.53@sha256:ccad23e18639c8b98cee17e7100b95830a06077b56d8e5b006d6dcf8ec5e94a0` |
| Caddy | `caddy:2.11.9-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0` |
| API and worker base | `python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df` |
| Frontend build | `node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a` |

The API, worker, and edge images run as uid 10001. The worker image adds FFmpeg. The edge image contains the Vite production build and does not run the dev server. Caddy listens on container ports 8080 and 8443, and Compose publishes the host HTTP and HTTPS ports to those ports. Certificate data stays in the `caddy_data` and `caddy_config` volumes. An existing volume created by the root Caddy image is given to uid 10001 before the edge starts. The files are not deleted.

## Image scan exception

The 2026-11-13 remediation record is `docs/security/2026-11-13-container-baseline.md`. PostgreSQL is pinned to 16.16 and the edge image uses Caddy 2.11.9 because those images removed findings that had upstream fixes. The first start of an existing PostgreSQL 16 data directory on 16.16 is a minor-version restart. Take a backup before that start. This change does not start the database by itself.

These findings remain because the current upstream image has no patched package:

- API and worker: Debian Bookworm `zlib1g` `1:1.2.13.dfsg-1` (CVE-2023-45853) and `libsqlite3-0` `3.40.1-2+deb12u2` (CVE-2025-7458). Bookworm security has no newer candidate. The API uses PostgreSQL. SQLite is present because the Python base image ships it. The worker keeps FFmpeg, so `libaom3`, `libmbedcrypto7`, `libglib2.0-0`, and `libxml2` stay where Bookworm has no fixed version.
- PostgreSQL 16.16: CVE-2025-68121 remains in `/usr/local/bin/gosu`, built with Go 1.24.6. The fixed Go releases are 1.24.13, 1.25.7, and 1.26.0-rc.3. `gosu` runs when the container starts.
- Redis 7.4.12 stays on the pinned base. `deploy/redis/Dockerfile` installs Alpine `zlib` `1.4-r0`, which is the fixed version for CVE-2026-4738. The Redis server binary is unchanged.
- Caddy 2.11.9: high CVE-2026-116587 in `github.com/go-jose/go-jose/v3` `v3.0.5` has no fixed version. High CVE-2026-78661 in `golang.org/x/net` `v0.60.0` is fixed in 0.62.0, and this Caddy release still vendors 0.60.0. `2.11.9-alpine` is the current Alpine tag.
- SeaweedFS 4.53, published 2026-11-08, is the newest release. `weed` includes `golang.org/x/net` `v0.61.0` (CVE-2026-78661), fixed in 0.62.0.
