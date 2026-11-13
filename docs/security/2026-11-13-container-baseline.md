# Container and dependency baseline, 2026-11-13

Trivy 0.58.2. Vulnerability database version 2, `UpdatedAt` 2026-11-13 00:58:46 UTC, `DownloadedAt` 2026-11-13 07:13:57 UTC. The scanner was `aquasec/trivy:0.58.2` with vulnerability and secret scanners enabled and severities `UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL`. Raw JSON stayed in `/tmp/trivy-08a` and `/tmp/trivy-08b1` and is not committed.

Image totals count every package occurrence. One CVE in both `libcrypto3` and `libssl3` is two rows. The worker image contains the API base packages plus FFmpeg libraries, so its total includes the API findings again. The Stage 08A edge image and the Caddy 2.8 base image reported the same totals because they share that base.

## Stage 08A baseline

| Image | Digest | Critical | High | Medium | Low |
| --- | --- | --- | ---: | ---: | ---: |
| youtube-prod-api | `sha256:141ad1daef0360491e0b556a30b4a5c9eeba8abab9ee71b8e32f671bcb8d1eca` | 2 | 96 | 144 | 109 |
| youtube-prod-worker | `sha256:c0c494870e06713c353e63d3a02158c2caae44bf7c4ac071259a0707f58a0ab3` | 8 | 300 | 506 | 251 |
| youtube-prod-edge | `sha256:c2a62df700600b9f6c5b4c1e0c43251fa7c448162eeacdd936273e833126d428` | 7 | 92 | 94 | 32 |
| `postgres:16-alpine` | `sha256:aa6cb33cf4c19c765cefcf48d96176258800870a9a1e930ad126200f9d825139` | 1 | 44 | 38 | 3 |
| `redis:7-alpine` | `sha256:7b1753b4c5ad8458906c272a8132b8c15f5557b000379eb115fb239706a384b7` | 0 | 0 | 1 | 0 |
| `caddy:2.8-alpine` | `sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17` | 7 | 92 | 94 | 32 |
| SeaweedFS 4.53 | `sha256:ccad23e18639c8b98cee17e7100b95830a06077b56d8e5b006d6dcf8ec5e94a0` | 0 | 1 | 1 | 0 |

Critical rows, deduplicated by vulnerability and package:

| Image | Package | Installed | Vulnerability | Fixed version | Applies |
| --- | --- | --- | --- | --- | --- |
| API, worker | `zlib1g` | `1:1.2.13.dfsg-1` | CVE-2023-45853 | none in Bookworm | The package is installed. The application does not call miniunzip on untrusted archives. |
| API, worker | `libsqlite3-0` | `3.40.1-2+deb12u2` | CVE-2025-7458 | none in Bookworm | Installed by the Python image. The application uses PostgreSQL. |
| Worker | `libaom3` | `3.6.0-1+deb12u3` | CVE-2023-6879 | none reported | Installed with FFmpeg. FFmpeg stays. |
| Worker | `libmbedcrypto7` | `2.28.3-1` | CVE-2025-47917, CVE-2026-34873, CVE-2026-34875 | none reported | Installed with FFmpeg. |
| Worker | `libglib2.0-0` | `2.74.6-2+deb12u9` | CVE-2026-58016 | none reported | Installed with FFmpeg. |
| Worker | `libxml2` | `2.9.14+dfsg-1.3~deb12u6` | CVE-2026-6653 | none reported | Installed with FFmpeg. |
| Edge, Caddy 2.8 | `libcrypto3`, `libssl3` | `3.3.2-r1` | CVE-2026-31789 | `3.3.7-r0` | Fixed by moving to Caddy 2.11.9. |
| Edge, Caddy 2.8 | `github.com/smallstep/certificates` | `v0.26.1` | CVE-2025-44005, CVE-2026-30836 | `0.29.0`, `0.30.0` | In the Caddy 2.8 binary. Absent from the 2.11.9 scan. |
| Edge, Caddy 2.8 | `google.golang.org/grpc` | `v1.63.2` | CVE-2026-33186 | `1.79.3` | In the Caddy 2.8 binary. Absent from the 2.11.9 scan. |
| Edge, Caddy 2.8 | Go stdlib | `v1.22.3` | CVE-2024-24790, CVE-2025-68121 | `1.22.4` and later | In the Caddy 2.8 binary. Absent from the 2.11.9 scan. |
| PostgreSQL | Go stdlib in `gosu` | `v1.24.6` | CVE-2025-68121 | `1.24.13`, `1.25.7`, `1.26.0-rc.3` | Still present in 16.16. |
| SeaweedFS | `golang.org/x/net` | `v0.61.0` | CVE-2026-78661 | `0.62.0` | In `/usr/bin/weed`. No newer SeaweedFS release. |

`apt-cache policy` inside `python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df` showed the installed and candidate versions of `zlib1g` and `libsqlite3-0` were the same, and `apt-get -s upgrade` changed zero packages. An `apt-get upgrade` layer would not change those libraries.

## Changes after that baseline

| Image | Old pin | New pin | Reason |
| --- | --- | --- | --- |
| PostgreSQL | `16-alpine@sha256:aa6cb33cf4c1…` | `16.16-alpine@sha256:0adbf25a9e96…` | Same major version. High findings fell from 44 to 25. Nineteen high rows in `krb5-libs` and `libxslt` are gone. `postgres --version` reports 16.16. |
| Caddy and edge | `2.8-alpine@sha256:af32e97399fe…` | `2.11.9-alpine@sha256:003a97808e78…` | `caddy validate` accepted `deploy/caddy/Caddyfile.prod`. Critical findings fell from 7 to 0 and high findings from 92 to 2. |
| Redis | `7-alpine@sha256:7b1753b4c5ad…` | same server, `zlib` `1.4-r0` | The pinned image is already Redis 7.4.12. Alpine 3.22 publishes `zlib` `1.4-r0`, so `deploy/redis/Dockerfile` upgrades that package only. |
| SeaweedFS 4.53 | unchanged | unchanged | GitHub releases on 2026-11-13 still list 4.53, published 2026-11-08, as the newest release. |
| API and worker base | unchanged | unchanged | Bookworm has no newer candidate for the critical libraries. FFmpeg stays. |

PostgreSQL 16.16 scan of digest `sha256:0adbf25a9e96d38823bb813f3ac0ed41fadb665c88f1df7fe6dbfb060cbdc106`: 1 critical, 25 high, 31 medium, 3 low, 2 unknown. The remaining critical row is CVE-2025-68121 in `/usr/local/bin/gosu`. The remaining high rows are the same Go stdlib in that binary.

Caddy 2.11.9 scan of digest `sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0`: 0 critical, 2 high, 4 medium, 0 low, 2 unknown. The two high rows were already present in Caddy 2.8 at older versions: CVE-2026-116587 in `go-jose` moved from `v3.0.3` to `v3.0.5` and still has no fixed version, and CVE-2026-78661 in `golang.org/x/net` moved from `v0.25.0` to `v0.60.0` while the fix remains `0.62.0`. The OpenSSL critical CVE-2026-31789 and the zlib high CVE-2026-22184 are gone. Medium CVE-2026-4738 remains in `zlib` `1.3.2-r1`. Docker Hub on 2026-11-13 shows `2.11.9-alpine`, `2.11-alpine`, `2-alpine`, and `alpine` sharing that digest.

The newer `redis:7-alpine` digest `sha256:7f7d30b7fdd0…` is also Redis 7.4.12 and still contains `zlib` `1.3.2-r1`. `apk policy` inside the pinned image shows the fixed package `1.4-r0` in Alpine 3.22. The derived image installs that package and leaves the Redis server at 7.4.12.

## Frontend

`npm view` and `npm audit` against `https://registry.npmjs.org` returned `ETIMEDOUT` on 2026-11-13. The lockfile was not changed. Installed versions from `frontend/package-lock.json`:

| Package | Installed | Production dependency |
| --- | --- | --- |
| vite | 6.4.6 | no |
| vitest | 2.1.9 | no |
| vite-node | 2.1.9 | no |
| tinypool | 1.1.1 | no |
| esbuild | 0.25.12 | no |
| `@vitest/mocker` | 2.1.9 | no |
| react-router | 6.30.6 | yes |
| react-router-dom | 6.30.6 | yes |

When the registry answers, read `fixAvailable` for each of those packages. Apply the lowest version that the advisory marks as fixed and that stays compatible with React 18. Stay on React Router 6 if a 6.x fix exists. Then run `npm ci`, `npm audit`, `npm test`, `npx tsc --noEmit`, and `npx vite build`. Do not use `npm audit fix --force`.

## Rebuilt production images

The same Trivy 0.58.2 database was used after `image --download-db-only`. Trivy kept `UpdatedAt` 2026-11-13 00:58:46 UTC. API and worker counts match the baseline because their Debian packages did not change. `dpkg` inside the new images still reports the versions in the critical table above. FFmpeg in the worker is `5.1.9-0+deb12u1`.

| Image | Image ID | Critical | High | Medium | Low | Unknown |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| youtube-prod-api | `sha256:36efc0813ae5084150cfb97364e49ffee16c462be2827d5e8cc23195843744cb` | 2 | 96 | 144 | 109 | 4 |
| youtube-prod-worker | `sha256:a6ee725cef4f16039f8d833ff1e1aa35067b918e27b0d16503bef7707b05e0b2` | 8 | 300 | 506 | 251 | 46 |
| youtube-prod-edge | `sha256:543bc6d8eb530b2a00844aa17e799c71276944ac0aa14742b3232fc3f2898a70` | 0 | 2 | 4 | 0 | 2 |
| `postgres:16.16-alpine` | `sha256:0adbf25a9e96d38823bb813f3ac0ed41fadb665c88f1df7fe6dbfb060cbdc106` | 1 | 25 | 31 | 3 | 2 |
| `redis:7-alpine` | `sha256:7b1753b4c5ad8458906c272a8132b8c15f5557b000379eb115fb239706a384b7` | 0 | 0 | 1 | 0 | 0 |
| `caddy:2.11.9-alpine` | `sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0` | 0 | 2 | 4 | 0 | 2 |
| SeaweedFS 4.53 | `sha256:ccad23e18639c8b98cee17e7100b95830a06077b56d8e5b006d6dcf8ec5e94a0` | 0 | 1 | 1 | 0 | 2 |

The edge image and the Caddy 2.11.9 base report the same counts in that table. The edge image now runs as uid 10001. The Redis row is the base image before `deploy/redis/Dockerfile` installed `zlib` `1.4-r0`. The derived image scan reported no vulnerabilities.

## Runtime verification

The backup at `tmp/acceptance-backups/20261113T071313Z` is mode `700`. `db.dump` is mode `600`, 58277 bytes, and `pg_restore -l` from PostgreSQL 16.16 reads it. The archive was dumped from PostgreSQL 16.15 and contains 2 users and Alembic revision `0007_indexes`. The volume `youtube-prod_pg_data` is mounted at `/var/lib/postgresql/data` and its `PG_VERSION` is `16`.

PostgreSQL 16.16 started on that volume. The log says `database system is ready to accept connections` and contains no fatal or incompatibility error. `pg_isready` accepts connections. The `youtube` role is present. The user count is 2 and `alembic_version` is `0007_indexes`.

The edge process is uid 10001. It listens on container ports 8080 and 8443. The existing Caddy volumes were given to uid 10001 without deleting files, and that user can write `/data/caddy` and `/config/caddy`.

`deploy/redis/Dockerfile` keeps Redis 7.4.12 and installs `zlib` `1.4-r0`. A Trivy 0.58.2 scan of `youtube-prod-redis:7.4.12` reported no vulnerabilities. The Redis server process runs as uid 999.

These findings still need a newer upstream release:

- Caddy 2.11.9 remains the current Alpine tag and still vendors `golang.org/x/net` `v0.60.0` (CVE-2026-78661, fixed in 0.62.0).
- SeaweedFS 4.53, published 2026-11-08, remains the newest release. `weed` still contains `golang.org/x/net` `v0.61.0`.
- PostgreSQL 16.16 is the newest 16 image checked. `/usr/local/bin/gosu` is 1.19, built with Go 1.24.6, so CVE-2025-68121 remains.

## Volumes

No Docker volume was deleted, recreated, pruned, or reset. PostgreSQL 16.16 was started only after the backup listed above was readable. The volume `youtube-prod_pg_data` still records its original creation time, `2026-11-13T16:56:38+10:00`.
