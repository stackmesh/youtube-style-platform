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

`CORS_ORIGINS` equals `PUBLIC_APP_URL`. TLS mode requires `COOKIE_SECURE=true`, `PUBLIC_APP_URL=https://<domain>`, and `S3_PUBLIC_ENDPOINT_URL=https://s3.<domain>`. `TRUSTED_HOSTS` lists the site host and the `s3` host. `DOCS_ENABLED` defaults to `false`, and the generated Caddy site does not route `/docs`. Set `DOCS_ENABLED=true` before the first install to publish Swagger.

Demo mode is the only place `COOKIE_SECURE=false` is accepted.

## Firewall

The installer does not change UFW unless `DEPLOY_CONFIGURE_UFW=1`. With that flag it sets the incoming policy to deny and the outgoing policy to allow, then allows OpenSSH, 80/tcp, and 443/tcp. PostgreSQL, Redis, and SeaweedFS ports are not allowed. Compose does not publish those ports.

## Images

| Image | Pin |
| --- | --- |
| PostgreSQL | `postgres:16-alpine@sha256:aa6cb33cf4c19c765cefcf48d96176258800870a9a1e930ad126200f9d825139` |
| Redis | `redis:7-alpine@sha256:7b1753b4c5ad8458906c272a8132b8c15f5557b000379eb115fb239706a384b7` |
| SeaweedFS | `chrislusf/seaweedfs:4.53@sha256:ccad23e18639c8b98cee17e7100b95830a06077b56d8e5b006d6dcf8ec5e94a0` |
| Caddy | `caddy:2.8-alpine@sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17` |
| API and worker base | `python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df` |
| Frontend build | `node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a` |

The API and worker images run as uid 10001. The worker image adds FFmpeg. The edge image contains the Vite production build and does not run the dev server.

## Image scan exception

Trivy 0.58.2 on 2026-11-13 reported critical and high findings in the pinned base images. They are operating-system and runtime libraries, not application packages. Debian Bookworm packages in the API and worker images, including zlib and sqlite, have no fixed version in that scan. The worker image adds FFmpeg libraries with the same condition. Caddy 2.8 and Postgres 16 Alpine also carry upstream Go and Alpine fixes that require a newer image than the pinned digest. Those findings are accepted until a deliberate base-image bump. Redis 7 Alpine had one medium finding and no high or critical finding. SeaweedFS 4.53 had one high finding in `golang.org/x/net`.
