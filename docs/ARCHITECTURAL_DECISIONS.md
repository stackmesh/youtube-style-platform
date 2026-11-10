# Architectural Decisions

**Status: decisions for a system that has not been implemented.**

Each record is the choice the other documents follow. If a later document disagrees with this file, this file is corrected in the same change. The design was reviewed for that kind of drift; the resolutions are in section 12.

## ADR-001 — Modular monolith

**Decision.** One FastAPI codebase and one worker process from that codebase. Modules separate identity, catalogue, uploads, processing, playback, engagement, and moderation. No network calls between those modules.

**Alternatives.** Microservices per feature. A second "media service" separate from the API.

**Why.** The deployment target is one Ubuntu host. Network splits would add partial failure, distributed transactions, and operational cost without a team boundary that needs them. Module boundaries still keep routers thin and FFmpeg out of the request process.

**Limitations.** A bad deploy restarts the whole API. That is acceptable at this size. A scale-out path is described in [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) and is not built.

## ADR-002 — PostgreSQL is the job queue

**Decision.** Processing jobs are rows in `video_processing_jobs`, claimed with `FOR UPDATE SKIP LOCKED`. Redis does not hold the queue.

**Alternatives.** Celery, RQ, Dramatiq, or ARQ with Redis as the broker.

**Why.** The dangerous failure is "object stored, job message lost" or "job message stored, database status not updated". Putting the job insert in the same transaction as the status change removes that split. A Redis restart cannot drop work. Idempotency is a unique partial index, which a broker does not provide by itself.

**Limitations.** Job throughput is that of one PostgreSQL instance. That is far above one FFmpeg process. The team does not get Celery's prefork ecosystem. Scheduled work is a loop in the worker, not Celery Beat.

## ADR-003 — Presigned multipart uploads

**Decision.** Yes. The browser uploads parts directly to the S3 gateway with presigned `PUT` URLs. The API handles initiate, presign, complete, and abort.

**Alternatives.** Proxy the file through FastAPI. Single presigned `PUT` for the whole object. TUS protocol.

**Why.** Video files are large. Proxying them through the API ties up workers and tempts the process to buffer. A single `PUT` does not give natural part progress or retries of one part. Multipart S3 is supported by MinIO and needs no extra protocol. TUS is a reasonable alternative and was rejected because it adds a second upload protocol on top of S3, which MinIO would still store.

**Limitations.** The browser must be able to reach the S3 host. CORS and `Host`-preserving proxy configuration are part of the design, not optional polish. Part size is fixed at 16 MiB and is signed into each part URL (ADR-031). Uploads expire after 24 hours; longer pause-and-resume is out of scope.

## ADR-004 — Private playback through API playlists and segment redirects

**Decision.** The bucket stays private. The player loads playlists from the API with a path-scoped cookie. Segment routes check access and respond `302` to a two-minute presigned `GET`. The API does not stream segment bytes.

**Alternatives.**

- Public bucket or public prefix for HLS. Rejected. Private and unlisted videos would be world-readable if the id leaked, and a public prefix tends to spread.
- Presigned URLs written into the playlist at session start. Rejected for VOD. A 60 minute video would need URLs that live at least that long, so a revoke would be ignored for the rest of the hour.
- API proxies every segment byte. Rejected. It makes the Python process the bandwidth bottleneck and duplicates MinIO.
- Nginx `auth_request` plus a signing module in the proxy. Rejected for the first release. It needs extra modules or a custom image, and the redirect design uses stock Caddy.

**Why.** Access is checked at the moment a segment is requested, which is what makes hide and revoke real. The presign is only a short transfer ticket. Playlist files are small enough to pass through the API.

**Limitations.** About ten API calls per minute per viewer, more during seek. That fits the capacity target and will not fit a large CDN-shaped audience. iOS native HLS must follow the redirect. If a device does not, the fallback in [VIDEO_PIPELINE.md](VIDEO_PIPELINE.md) embeds longer-lived presigns for that user agent only. That fallback is not enabled until a test shows the redirect path failing. A segment already being downloaded can finish after a revoke.

## ADR-005 — Bearer access token, hashed refresh token, session epoch

**Decision.** Access token is a 15 minute HS256 JWT kept in memory. Refresh token is an opaque 14 day secret, stored as a SHA-256 hash, delivered in an `HttpOnly` cookie, and rotated. The JWT carries `epoch`. `users.session_epoch` increments on logout, deactivation, and ban. Role is not a JWT claim. Every authenticated request loads the user row.

**Alternatives.** Server sessions only. JWT in `localStorage`. JWT role claims with no database read. Redis denylist of `jti` instead of an epoch column.

**Why.** The video element cannot send `Authorization`, so some cookie is required for playback, but mutations stay on the bearer token to keep CSRF simple. `localStorage` is readable from any XSS. Role claims go stale; a primary-key read does not, and one host can afford it. An epoch column avoids a denylist round trip and makes logout a single transaction with refresh revocation. A Redis denylist would make every request depend on Redis for correctness of logout. Redis is already allowed to be empty of durable session state (ADR-024).

**Limitations.** HS256 means anyone who can read `JWT_SECRET` can mint tokens. Only the API container has that secret. Moving to asymmetric signatures is future work if a second verifier appears. The user-row read is on the hot path by design.

## ADR-006 — One channel per user

**Decision.** `channels.user_id` is unique. Registration creates the channel. Handles are immutable in the first release.

**Alternatives.** Multiple channels per user. Channel fields on `users` with no channel table.

**Why.** The product talks about channels, subscribers, and playlists as channel concepts. A separate table keeps that from crowding the credential table. Multiple channels multiply grants, moderation, and UI scope for no stated requirement.

**Limitations.** A user cannot run a second channel without a second account. Handle changes, if added later, need redirects from the old handle. The column can stay immutable until that requirement exists.

## ADR-007 — PostgreSQL full-text search

**Decision.** Video title and description use a generated `tsvector` with the `simple` configuration. Channel name and handle use `pg_trgm`. No Elasticsearch or OpenSearch.

**Alternatives.** A search engine service. `ILIKE '%query%'` only.

**Why.** The requirement is title and channel search with sort and pagination, on one server. PostgreSQL already runs. Another search cluster would be a second index to keep consistent with hides and visibility. `simple` avoids wrong stemming for mixed languages. Trigram covers handle prefixes, which full-text tokenization does poorly.

**Limitations.** No typo tolerance on video titles beyond what `simple` tokenization gives. No relevance tuning beyond `ts_rank`. Search quality is adequate for the catalogue size of one server and is not YouTube-grade retrieval.

## ADR-008 — Cursor pagination

**Decision.** Feeds, search, comments, and moderation lists use opaque cursors. Offset pagination is not offered.

**Alternatives.** Page numbers. Infinite offset.

**Why.** The home feed is ordered by `published_at`. Inserts at the top shift offsets and cause duplicate or skipped cards. Cursors composed of the sort key plus the id are stable.

**Limitations.** Clients cannot jump to page 50. The UI uses "load more", which matches the product. A cursor is not signed; it is not an authorization token.

## ADR-009 — Denormalized public counters

**Decision.** `view_count`, `like_count`, `dislike_count`, `comment_count`, and `subscriber_count` are columns updated in the same transaction as the underlying write, using SQL arithmetic.

**Alternatives.** Count at read time. Periodic rollups. Redis counters flushed later.

**Why.** Channel pages and cards show these numbers on every request. Counting comments or subscriptions on read gets slower as tables grow, and the tables are the ones that grow. Transactional increments stay correct under Read Committed without a cache invalidation story.

**Limitations.** A bug in the increment logic drifts the number. Check constraints stop negative values. A later repair command can recompute from source rows; it is not part of the first release. If a repair is needed, recompute and compare before trusting the column.

## ADR-010 — Argon2id

**Decision.** Passwords are hashed with Argon2id, 64 MiB, time cost 3, parallelism 1.

**Alternatives.** bcrypt, scrypt, PBKDF2.

**Why.** Argon2id is the current standard choice for password hashing and resists GPU and side-channel tradeoffs better than bcrypt's 72 byte input limit and PBKDF2. Parameters are sized so a small server can still log someone in quickly. They are configuration, so a slow host can drop memory to 32 MiB without a schema change.

**Limitations.** Login is intentionally CPU-heavy. The rate limits in the security design are what keep that from becoming a denial of service. There is no password-reset flow in the first release (ADR-018).

## ADR-011 — 404 when a private video is not visible

**Decision.** Callers who fail the video ACL receive `404 VIDEO_NOT_FOUND`, including on comments, playback, and segments.

**Alternatives.** `403` for private videos. `403` for hidden and `404` for private.

**Why.** `403` confirms the id exists. UUIDs are hard to guess, and 404 removes a trivial existence check from reports, logs, and shared links. Moderators use different routes that return 403 for role failures and 404 when a private video is outside their review scope.

**Limitations.** Owners debugging a grant mistake see the same 404 as a bad link when they test while logged out. The studio page, called as the owner, shows the real status.

## ADR-012 — Append-only moderation audit

**Decision.** Every hide, restore, dismiss, role change, deactivation, reactivation, and moderator view of a private or hidden video inserts `moderation_actions`. A trigger rejects `UPDATE` and `DELETE`.

**Alternatives.** Application convention only. A log file. An external audit product.

**Why.** Moderators are a privileged group. A log file on one container is easy to lose. A convention without a trigger is bypassed by the same database role the app uses. The trigger is a small amount of mechanism for a real requirement: moderation history.

**Limitations.** A superuser can disable the trigger. The application role should not be a superuser. The Compose database user owns the tables, so this is a real residual risk on a single database login. A later hardening step can split a migration role from the runtime role. That split is not in the first release because it complicates Alembic. The limitation is accepted and recorded.

**Consequence for operators.** Database dumps contain the audit log. Treat dumps as sensitive.

## ADR-013 — Grants survive visibility changes

**Decision.** `video_access_grants` stay in place when visibility changes. The ACL consults active grants only when visibility is `private`. Grant and visibility changes increment `acl_version`. New videos default to `private`.

**Alternatives.** Delete grants when a video becomes public. Separate permission rows per visibility. Store an allow-list that is the only gate for every visibility.

**Why.** Owners flip videos between private and public while collaborating. Deleting grants would be surprising, and re-adding them is easy to get wrong. Keeping them inert is reversible. Default private means a draft is not public before the owner chooses otherwise.

**Limitations.** A grant the owner forgot becomes active again the moment the video returns to private. The grant list is visible to the owner so they can revoke first. The API must show that list in the studio UI before this behavior is safe to use. That UI is part of stage 3.

## ADR-014 — Caddy with a separate S3 hostname

**Decision.** Caddy serves the app on `https://<domain>` and reverse-proxies the SeaweedFS S3 gateway on `https://s3.<domain>`, preserving `Host`. The SeaweedFS admin UI is not published. Demo mode without a domain uses HTTP and insecure cookies, and the script says so.

**Alternatives.** Nginx with `auth_request`. MinIO published on port 9000. Path prefix `/s3` on the app host.

**Why.** Caddy obtains certificates with little configuration, which is what makes one-command TLS realistic. MinIO's supported reverse-proxy style is a dedicated host, not a subpath. A subpath breaks presigning often enough that it should not be the default. Nginx was a strong alternative and loses on certificate setup, not on proxy features. The playback design does not need `auth_request` (ADR-004), so Nginx's main advantage is unused.

**Limitations.** Production expects a DNS name and control of `s3.<domain>`. IP-only hosting is a demo. Presigned URLs include the S3 host; a wrong `S3_PUBLIC_BASE_URL` fails all uploads and segment redirects. The health check should presign and `HEAD` a canary only if that test stays inside the Docker network. The ready check in this design verifies the bucket exists, not a public presign. A broken public URL is a deployment test in stage 12, not a liveness failure.

## ADR-015 — Two image targets

**Decision.** One backend Dockerfile builds an `api` target without FFmpeg and a `worker` target with FFmpeg. The frontend is built into the edge image.

**Alternatives.** One image for API and worker. FFmpeg on the host instead of in the worker image.

**Why.** The API must not be the place a command runs FFmpeg. A smaller API image also starts faster. Host FFmpeg would break the "one command, clean Ubuntu" promise by adding a host package the Compose file does not control.

**Limitations.** Two builds. Shared Python code is still one source tree, copied into both images, so logic does not fork. The worker and API must be built from the same git revision. The deploy script builds them together.

## ADR-016 — Synchronous worker

**Decision.** The worker is a synchronous Python process. FFmpeg is a subprocess. The API is asynchronous FastAPI. Shared integration code is synchronous and the API calls it with `asyncio.to_thread` when a request must wait on object storage.

**Alternatives.** Async worker. Celery prefork.

**Why.** Transcoding blocks on a process and on disk. Async would not overlap usefully inside one transcode. A synchronous claim loop is easier to reason about for locks and `finally` cleanup.

**Limitations.** The API must not call FFmpeg. Thumbnail re-encode uses Pillow inside a thread, and the 2 MB cap keeps that short. If object-storage calls are ever made directly on the event loop, that is a defect.

## ADR-017 — View deduplication table

**Decision.** A view counts when playback has started and the viewer key has not been recorded for that video in the last 6 hours. Anonymous keys are a daily-salted hash of IP and user agent. Rows older than 48 hours are deleted.

**Alternatives.** Count every metadata `GET`. Redis HyperLogLog only. No anonymous dedupe.

**Why.** Counting metadata fetches inflates views from bots and from the studio page. A table keeps the rule durable across a Redis flush. The salt limits how long an IP-derived value is useful.

**Limitations.** Determined clients can inflate views. This is not an advertising-grade counter. The privacy tradeoff of hashing IP and user agent is an open confirmation in section 11. If it is rejected, anonymous views are counted once per playback session id instead, which under-counts less aggressively across sessions and over-counts refreshes after the session expires.

## ADR-018 — No email verification or password reset in the first release

**Decision.** Registration does not prove control of the inbox. There is no SMTP integration and no reset flow.

**Alternatives.** Add an SMTP service and verification tokens now.

**Why.** The brief does not provide a mail server, and a fake verification step would block registration without adding trust. Quotas and rate limits are the abuse controls instead.

**Limitations.** Disposable identities are easy. Account recovery requires an operator with database access. This is a product gap, called out in section 11, not an accident.

## ADR-019 — HLS ladder and full-attempt retry

**Decision.** MPEG-TS HLS, 6 second aligned GOPs, audio muxed into each rendition, renditions at 360p, 720p, and 1080p when the source is tall enough. Preset `veryfast`. A retry wipes the work directory and runs the whole attempt again. Output object keys are deterministic.

**Alternatives.** fMP4/CMAF. Separate audio rendition. Step-level resume. GPU encode. ABR ladders chosen per title.

**Why.** MPEG-TS plays in `hls.js` and in Safari without a CMAF packaging step. Muxed audio makes the master playlist a simple list and avoids audio-only variant bugs in older players. It costs disk. Full-attempt retry is idempotent and short to implement. Step resume saves CPU after a crash in the upload phase and adds partial-state bugs. CPU waste is acceptable when concurrency is 1 and automatic attempts are capped at 3.

**Limitations.** Storage per video is higher than a shared-audio ladder. A retry repeats a finished encode. There is no AV1 or VP9. Maximum practical duration is the configured 60 minutes, and the FFmpeg wall clock is capped at 3 hours.

## ADR-020 — Soft delete videos, hard delete playlist rows

**Decision.** Videos get `deleted_at`. Objects are removed later by the sweeper. Video rows stay. Playlists and playlist items are deleted outright because they hold no audit requirement and no bytes.

**Alternatives.** Hard delete videos immediately. Soft delete everything.

**Why.** Object deletion can fail and must be retryable. Keeping the row preserves moderation history references. Playlists are user organization, not evidence.

**Limitations.** Soft-deleted video rows accumulate. That is acceptable. A purge of rows whose storage was released and whose reports are closed is future work. User accounts are deactivated, not deleted, so a full erasure request cannot be completed by the product today.

## ADR-021 — UUID primary keys

**Decision.** Primary keys are UUID v7 generated in the application, except `playback_sessions.id`, which is UUID v4.

**Alternatives.** Serial integers. UUID v4 for every table.

**Why.** Serial ids leak the size of the catalogue and the order of creation in public URLs. UUID v7 keeps index inserts mostly sequential. Playback session ids are capabilities in a cookie, so they use random v4 and are not derived from the video id.

**Limitations.** UUIDs are larger than integers. The application must use one generator. The database does not generate v7 by itself on PostgreSQL 16.

## ADR-022 — Rate limits fail closed

**Decision.** If Redis is down, routes that have a rate limit return 503. They do not skip the limit.

**Alternatives.** Fail open. An in-process limit as backup.

**Why.** Fail open during a Redis outage is the moment a credential-stuffing run or upload flood does the most harm. Readiness already fails without Redis, so the API is not advertising itself as healthy. An in-process backup would diverge across two Uvicorn workers and is not worth a second implementation.

**Limitations.** A Redis outage blocks login, upload, comments, and search. Metadata reads that are not rate limited could still be served, but the edge should stop routing when readiness fails. Compose uses the ready check. A misconfigured proxy that ignores readiness would still see 503 on limited routes.

## ADR-023 — Re-encode thumbnails and avatars in the API

**Decision.** Custom thumbnails and avatars are uploaded to the API, decoded with Pillow, limited to 2 MB and 1920×1080, and stored as new JPEGs. Video binaries are never uploaded this way.

**Alternatives.** Presign thumbnail uploads the same way as video. Trust the browser MIME type.

**Why.** These files are small enough to inspect. Re-encoding strips metadata and rejects files that are not actually images. Presigning them would store the attacker's bytes untouched.

**Limitations.** Pillow has had decoder bugs. The size and dimension caps reduce the blast radius. The work runs in a thread and is rate limited with the authenticated user limit.

## ADR-024 — What Redis is for

**Decision.** Redis stores rate-limit counters, the worker heartbeat, and the anonymous view-dedupe salt. It does not store jobs, refresh tokens, playback sessions, or feed caches.

**Alternatives.** Use Redis as the primary session store. Cache the home feed for 10 seconds.

**Why.** Durable state belongs in PostgreSQL, which is backed up. A feed cache would serve hidden videos until TTL and would need an invalidation path on every hide. The catalogue queries are indexed for the first release's traffic. Caching can be added when a measurement says the feed is slow.

**Limitations.** The home feed hits PostgreSQL on every request. That is a stated trade.

## ADR-025 — Reserve quota at upload start

**Decision.** `bytes_reserved` increases by the declared size when a multipart upload starts, under a row lock. Completion moves the actual size into `bytes_committed` and releases the reservation. Abort, expiry, and validation failure release the reservation. Soft delete decreases `bytes_committed` immediately. HLS expansion may push `bytes_committed` above `bytes_limit`; the user then cannot start another upload until they delete media.

**Alternatives.** Check quota only at completion. Charge the maximum ladder size up front.

**Why.** Checking only at the end allows many parallel uploads to pass and then fail, or worse, to succeed and overshoot without bound. Charging the worst-case HLS size up front would block legitimate uploads of small files on a nearly full quota. Allowing transcode overage avoids throwing away a finished encode.

**Limitations.** A user can be over the limit after HLS expansion. The studio must show that state. A declared size that does not match the stored parts is rejected exactly, not within a percentage, so the declaration cannot be used to reserve a small quota and store a large object (ADR-031). The upload cap is 2 GB by default and cannot be configured above 8 GB.

## ADR-026 — No generic unit of work or repository framework

**Decision.** Services call repositories and commit the session they were given. There is no plugin system, no generic CRUD base that hides queries, and no domain-event bus.

**Alternatives.** A full clean-architecture port with interfaces for every repository from day one. Domain events for "video ready".

**Why.** The team needs clear transaction boundaries, not indirection. Interfaces are introduced when a test double needs one, which is already the case for the object store and the clock. A bus would duplicate the job row.

**Limitations.** Some repositories will be concrete and easy to misuse from a router. Code review and the import rule in [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) are the guard. A lint import-linter rule is optional in stage 11 and not required for the design to be valid.

## ADR-027 — Home feed is global and newest-first

**Decision.** The home page lists public, ready, visible, non-deleted videos by `published_at` descending. Subscriptions do not filter the home feed. There is no recommendation ranker.

**Alternatives.** Subscription feed. "Trending" by views in a window. A simple scored blend.

**Why.** The brief asks for a feed with thumbnails, titles, channel names, views, dates, and pagination. A chronological public feed meets that and is explainable. A ranked feed needs watch history, which is out of scope, and it is harder to test.

**Limitations.** The home page does not feel personalized. Subscribed channels are reached from the channel page. This is an open confirmation only in the sense that the product owner might want a subscription feed; the design does not include one.

## ADR-028 — Moderator access to private videos is narrow and audited

**Decision.** Moderators and admins can open a video through the moderation API only when it is hidden or has at least one report, including a resolved report. If that video is private or hidden, the play writes `view_private_video`. They cannot browse private videos that were never reported.

**Alternatives.** Moderators can open every private video. Moderators cannot open private videos at all, so private-video reports are metadata-only.

**Why.** A report on a private video cannot be reviewed if the moderator cannot watch it. Standing access to every private video is a larger privacy break than the feature needs.

**Limitations.** A user can report a private video they were granted access to, which then lets a moderator watch it. That is the intended review path, and it is audited. Harassment via false reports is limited by the report rate limit and by dismiss.

## ADR-029 — Single private bucket, prefix layout

**Decision.** One bucket `media` holds originals, HLS, thumbnails, and avatars under fixed prefixes.

**Alternatives.** One bucket per type. One bucket per video.

**Why.** Lifecycle and credentials are simpler with one bucket on one storage instance. Prefixes are enough to delete a video's objects. Per-video buckets would create an operability problem at modest catalogue sizes.

**Limitations.** A lifecycle rule cannot easily expire originals without expiring HLS, because both share a bucket. The design does not expire originals. If disk pressure appears, a prefix-filtered rule can be added later without moving HLS.

## ADR-030 — Dislike counts are public

**Decision.** `dislike_count` is returned on video detail to every caller who can see the video.

**Alternatives.** Store dislikes and show them only to the owner, which is closer to current YouTube.

**Why.** The brief asks for like and dislike functionality and does not ask to hide the result. Showing the number is simpler to explain and to test.

**Limitations.** Public dislike counts change creator behavior and can be brigaded. If the product owner rejects this, the API can omit the field for non-owners without a schema change. Confirmation is listed below.

## ADR-031 — Upload part URLs are bound to an exact length

**Decision.** Every presigned `UploadPart` URL signs `Content-Length`. Non-final parts are exactly 16 MiB. The final part is the exact remainder of `expected_size`. `part_count` is at most 512. Complete calls `ListParts`, requires those sizes and a matching sum, then requires `HEAD` to return `expected_size`. There is no percentage tolerance. Quota reserved at initiate is the maximum those URLs can store. Concurrent completes serialize on the session row. A completed session returns the same video and does not insert a second job. Expired and untracked multipart uploads are aborted by the sweeper, with a one-day object-storage lifecycle rule behind it.

**Alternatives.** Trust the client's ETag list. Allow a size tolerance. Sign URLs without `Content-Length` and rely on complete-time rejection. Proxy the file through the API so size is observed on the way through.

**Why.** A presigned `PUT` that does not sign the length can store a multi-gigabyte part while the quota reservation stays small. Complete-time rejection would delete it later and would still have consumed disk. Signing the length makes MinIO refuse the `PUT`. `ListParts` catches a part that was stored by any other bug. The tolerance that was in the first draft of this design was removed because it was a quota bypass.

**Limitations.** The browser and Caddy must pass `Content-Length` through unchanged. That is an implementation-time test, not an assumption. If MinIO or the presign helper silently omits the header from `SignedHeaders`, the stage 4 oversized `PUT` test fails the build. A bucket quota still caps total disk use and does not replace the signature.

## ADR-032 — Separate storage admin and application credentials

**Status.** Superseded by ADR-034. The credential split remains. The storage server is SeaweedFS, not MinIO.

**Decision.** The MinIO process and a one-shot init container hold root credentials. Init creates user `media-app` and attaches the bucket policy in [SECURITY_DESIGN.md](SECURITY_DESIGN.md). The API and the worker receive only `media-app`. The API exits if root credentials are in its environment. The bucket policy has no `Principal: "*"` and no `s3:*`. The console is off. Ports 9000 and 9001 are not published. CORS allows one origin, methods `GET`, `PUT`, and `HEAD`, and exposes `ETag` only.

**Alternatives.** One root key for every container. A public-read policy for HLS. Console published on localhost only.

**Why.** Root can change policy, create users, and read every bucket. The frontend and the API do not need that. A leaked API environment should not be an account that can make the bucket public. Presigned URLs remain the only browser capability, and they are minted by `media-app` after an authorization check.

**Limitations.** The MinIO server itself still has root in its environment. That is how MinIO boots. Host access to the container environment is full administrative access. Init must stay idempotent so a repeat deploy does not rotate `media-app` out from under running processes.

## ADR-033 — The install script is not allowed to destroy data

**Decision.** `bash deploy/ubuntu-up.sh` checks OS, Docker, disk, memory, and environment before it starts containers. It creates persistent volumes on first install and refuses to recreate `pg_data`, `redis_data`, or `seaweed_data` when `.env` already exists. It never runs `down -v`. It waits for `/health/ready`. A migration failure stops the script, leaves the volume in place, and prints `deploy: FAIL migrate`. Repeat runs do not rotate secrets. Restore is a separate documented procedure, and a backup is not accepted unless `pg_restore -l` succeeds.

**Alternatives.** A short script that only runs `docker compose up -d`. Auto-restore when migrations fail. Recreate volumes whenever the script is unsure.

**Why.** A one-command install on a clean machine is the requirement. The same command will be run again on a machine that already has users and videos. Those runs must be boring. A failed migration that deletes the volume would be worse than a failed migration.

**Limitations.** The script cannot repair a migration that committed and then proved wrong. That recovery is restore-from-backup. The script also cannot install Ubuntu packages on a host without apt and systemd.

## ADR-034 — Replace MinIO with SeaweedFS

**Decision.** Object storage is SeaweedFS 4.53, image `chrislusf/seaweedfs:4.53` pinned by digest, running as one `weed server` process with the filer and the S3 gateway. The application continues to use boto3 with path-style SigV4. Internal calls use `S3_ENDPOINT_URL`. Presigned URLs use `S3_PUBLIC_ENDPOINT_URL`. The bucket stays private. `media-app` is limited to that bucket. `S3_ADMIN_ACCESS_KEY` and `S3_ADMIN_SECRET_KEY` are loaded only by the SeaweedFS process and `s3-init`.

**Why MinIO was removed.** The community images `minio/minio` and `minio/mc` are no longer anonymously pullable. Docker Hub returns 404 for the pinned tags. Quay.io returns 401 and issues an anonymous token with an empty pull permission. The Compose build cannot fetch those images.

**Why SeaweedFS.** It publishes a maintained image, speaks the S3 API the upload and playback design already uses, and can run master, volume, filer, and the S3 gateway in one process on a single host. PostgreSQL remains the system of record. Redis remains the rate-limit and heartbeat store.

**Compatibility.** Create, head, get, put, and delete objects, multipart upload, `ListParts`, abort, presigned PUT and GET, CORS, lifecycle abort of incomplete multipart uploads, and a bucket byte quota are part of the storage contract. The quota uses the SeaweedFS `?seaweedfs-quota` subresource rather than a MinIO admin command. Authorization is an identity file, not a MinIO IAM user. Anonymous callers have no actions. Path-style addressing is required. Master, volume, and filer bind to loopback inside the container, filer HTTP is disabled, and the embedded IAM API on the S3 port is disabled. The S3 port is not published.

**Trade-offs.** The gateway must hold both identities so it can verify signatures, including the admin identity. That is narrower than giving the admin identity to the API, and it is broader than a server that can boot without storing the admin secret. Bucket quota enforcement is a SeaweedFS extension, so a generic S3 service would ignore that call. A future move to another S3 service keeps the boto3 client and the endpoint variables and drops the quota subresource.

**Operations.** Data lives in the `seaweed_data` volume. `s3-init` is safe to run again and does not rotate `media-app`. Restarting the SeaweedFS container must keep the volume. Rollback is to restore `seaweed_data` from the mirror backup and point `S3_ENDPOINT_URL` at the previous gateway. Existing `minio_data` volumes are not mounted. A deployment that already stored objects in MinIO has to copy those keys into the `media` bucket before it is cut over. This repository had not completed a MinIO stack start, so there is no in-place data migration.

**Limitations.** Master, volume, and filer still exist inside the container even though their HTTP ports are not published. Host access to the container environment is administrative access. The public S3 hostname must be the host that was signed, which is why Caddy preserves `Host`.

## 10. Cross-cutting rules that are easy to break

These are decisions even though they are short.

- Business rules live in services. Routers translate HTTP. Repositories run SQL.
- The frontend is never the authority for visibility, role, or quota.
- S3 credentials never appear in a response, a frontend bundle, or a log line.
- Video bytes never go in PostgreSQL and never go through the API, except thumbnails and avatars under 2 MB.
- Processing jobs are idempotent at the object-key level and unique while active.
- A private or hidden video is not delivered by making a prefix public.
- An upload part URL signs one `Content-Length`. Complete trusts `ListParts` and `HEAD`, not the client's size claim.
- Storage admin credentials are not placed in the API, worker, edge, or frontend environment.

## 11. Confirmations still open

The architecture chooses a default for each of these so implementation does not block. They are product or privacy choices, and they should be confirmed before the stage noted.

| # | Default chosen | Confirm before | If rejected, what changes |
| --- | --- | --- | --- |
| 1 | Upload cap 2 GB, duration 60 minutes, quota 10 GB, two active uploads | Stage 4 | Environment defaults only |
| 2 | Dislike counts are visible to viewers | Stage 7 | Omit `dislike_count` for non-owners |
| 3 | Home feed is global and newest-first, not subscriptions | Stage 3 | Feed query and home UI |
| 4 | Moderators can watch private videos that were reported or hidden, with an audit row | Stage 10 | Reports on private videos become metadata-only, or access widens |
| 5 | Anonymous view dedupe hashes IP and user agent with a daily salt, kept 48 hours | Stage 7 | Dedupe by playback session id instead |
| 6 | No email verification, no password reset, no account deletion | Stage 2 | New flows, mail dependency, erasure design |
| 7 | English only, `simple` text search | Stage 9 | Dictionary and UI strings |
| 8 | Comment threads are one level deep and are not reportable | Stage 7 | Schema for deeper trees or comment reports |
| 9 | Original files are kept so processing can be retried | Stage 5 | Delete original after a successful transcode and disable retry-from-original |
| 10 | Backup retention 14 daily dumps, manual restore | Stage 12 | Script parameters |

None of these require a new service. Items 3, 4, 5, and 6 change behavior enough that they should not be "fixed" silently during implementation.

iOS playback is not an open product decision. The redirect design is the default. The embedded-presign fallback is enabled only after a failed device test, and the result is written into the stage 6 notes.

## 12. Consistency review

The documents were checked against each other after the playback and logout designs were revised. These are the conflicts that existed during drafting and how they were resolved. They are not open work.

| Topic | Resolution |
| --- | --- |
| Segment authorization | Early draft embedded presigned segment URLs in the playlist with a 15 minute or 2 hour TTL. That cannot both cover a 60 minute VOD and revoke quickly. All documents now use API segment routes and a two-minute redirect. |
| Logout effectiveness | Early draft left the access token valid for up to 15 minutes and considered a Redis denylist. The chosen design increments `users.session_epoch`, and the JWT carries `epoch`. [DATABASE_DESIGN.md](DATABASE_DESIGN.md) includes the column. |
| Moderator scope | The overview said moderators only review reports. The playback table briefly allowed any private video through the moderation API. The narrower rule is canonical: hidden or previously reported, always audited when private or hidden (ADR-028). |
| Job transport | No document uses Celery, RQ, or a Redis list as the queue. Redis is limited to ADR-024. |
| Bucket visibility | No document grants anonymous read. Public viewing still goes through the API and a presign. |
| Thumbnail `<img>` tags | Image requests cannot send a bearer token. Public cards use a public redirect route. Private and unlisted thumbnails in JSON are absolute short-lived presigns. |
| Role on the token | Role is loaded from PostgreSQL. The JWT does not carry it. |
| Grants vs visibility | Grants are retained and apply only while visibility is `private`. |
| Counters | Updated in SQL in the same transaction as the source write. |
| Identifiers | UUID v7 for entities, UUID v4 for playback session ids. |
| Upload size check | The first draft allowed `HEAD` to differ from the declaration by 1 percent. That tolerated a quota bypass. Every document now requires an exact `Content-Length` signature and an exact `ListParts` / `HEAD` match (ADR-031). |
| Storage credentials | The first draft gave the API the same keys the server uses. Admin credentials stay on the SeaweedFS process and `s3-init`. The API uses `media-app` and exits if it sees admin credentials (ADR-032, ADR-034). |
| Deploy repeat | The install script must not delete volumes or rotate secrets. Failure text is `deploy: FAIL <code>` (ADR-033). |

Remaining gaps are requirements the brief does not give, not contradictions:

- No legal retention schedule, no GDPR erasure workflow, no copyright counter-notice.
- No watch history, no notifications, no comment moderation.
- No multi-host topology.
- No measured capacity. The numbers in the overview are targets.
- iOS redirect behavior is unverified because there is no implementation yet.
- The audit trigger can be disabled by the table owner. ADR-012 accepts that for the first release.

The stage 00 review below records the changes made after the first design pass. Those changes are already incorporated in the rows above.

## 13. Known limitations of the whole design

- One host is a single point of failure. Restarts recover containers. They do not recover a lost disk. Backups are daily.
- Presigned transfer URLs and a weak database role are the sharpest security edges. Both are documented above.
- Search and recommendations are intentionally plain.
- Transcode concurrency of 1 means a queue forms under batch uploads. Status `uploaded` is the visible "waiting" state.
- Account recovery does not exist until a mail design is added.
- A presigned part URL is only as strict as the signature the code actually produces. ADR-031 is enforced by the stage 4 oversized-`PUT` test.
- Storage admin credentials remain in the SeaweedFS process environment.

## Stage 00 Architecture Review

This section records the review that produced ADR-031, ADR-032, and ADR-033. No application code was written. The eight documents were updated in place.

### Changes made

- Upload part URLs now sign an exact `Content-Length`. Non-final parts are 16 MiB. The final part is the remainder of the declared size. `part_count` is capped at 512, which caps a configured upload at 8 GiB.
- Complete verifies `ListParts` (part numbers, per-part sizes, sum, ETags) and then `HEAD`. The 1 percent size tolerance was removed.
- Duplicate complete returns the current video and does not insert a second job. Concurrent complete takes a row lock and returns `409 UPLOAD_IN_PROGRESS` to the loser.
- Quota is reserved before any part URL is issued and is released only after the S3 abort succeeds, or moved to `bytes_committed` only after the size check passes.
- Expired sessions are rejected by the API and aborted by the sweeper. Multipart uploads with no session row are aborted after 15 minutes. Object-storage lifecycle still aborts incomplete uploads after one day. A hard bucket quota is a disk backstop.
- `upload_sessions` gained `part_count`, `error_code`, `completion_token`, `completion_started_at`, and a nullable `s3_upload_id`, plus checks that keep part geometry consistent.
- Upload verification failure moves a video from `uploading` to `failed` and does not allow processing retry.
- Storage admin credentials are isolated from the API, worker, edge, and frontend. `media-app` is scoped to the bucket. CORS, ETag exposure, hostname, and outage behavior are specified.
- `ubuntu-up.sh` gained preflight checks, volume protection, migration failure handling, startup order, ready validation, and idempotent repeat runs. Backup verification uses `pg_restore -l`.
- Private-video tests now cover anonymous access, unauthorized users, grantees, revoked grants, playlists, segments, expired and revoked playback sessions, moderator limits, hide and restore, and object-URL exposure. Each test names the stage that automates it.

### Reasons

A presigned `PUT` without a signed length can fill the disk while the database still believes the reservation is small. Complete-time checks alone are too late. Root credentials in the API environment turn any API leak into a storage admin. A one-command script that is safe on a clean machine and destructive on the second run is not an acceptable install path. Private HLS is only as strong as the segment route, so metadata tests are not enough.

### Remaining risks

- A segment download that has already started can finish after a revoke or hide. The next request fails. The transfer URL lives at most two minutes.
- The SeaweedFS process still holds the storage-admin identity. Container-environment access is administrative access.
- HLS output can push a user over `bytes_limit` after a successful transcode. New uploads are then blocked. The stored objects are not deleted automatically.
- The audit trigger can be disabled by the table owner.
- The bucket quota limits total damage from a signing bug. It does not attribute bytes to a user.
- iOS Safari redirect behavior is still unverified because there is no player yet.
- There is still no email verification, password reset, or account deletion.
- One host and daily backups remain the availability design. A lost disk is a restore, and the recovery point is the last successful backup.

### Decisions that require implementation-time verification

| Decision | What to prove | When |
| --- | --- | --- |
| SigV4 includes `content-length` in `SignedHeaders` for `UploadPart` | An oversized `PUT` returns 403 from the S3 gateway and stores no part | Stage 4 |
| Caddy forwards `Host` and `Content-Length` without breaking the signature | The same `PUT` succeeds when sent through `S3_PUBLIC_ENDPOINT_URL`, not only against `seaweedfs:8333` | Stage 4 and the stage 12 rehearsal |
| Browser `fetch` or XHR sends the signed length for a `Blob` body | The upload screen completes a real file | Stage 4 |
| Bucket privacy written by `s3-init` matches section 5.2 of the security document, including on a second run | Unsigned `GET` and `PUT` return 403, and the second run does not rotate `media-app` | Stage 4 and stage 12 |
| API environment cannot see root credentials | Process environment assertion | Stage 1 |
| Alembic revisions roll back on failure | Throwaway bad revision, documented in stage 12, not shipped | Stage 12 |
| Native HLS on iOS follows the segment 302 | Device test. The longer-lived playlist fallback stays off until this fails | Stage 6 |

The stage 00 review did not change the stack. ADR-034 later replaced the object store with SeaweedFS. PostgreSQL remains the job queue. Playback remains an API playlist plus a short redirect. The install command remains `bash deploy/ubuntu-up.sh`.
