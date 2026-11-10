# Project Overview

**Status: design only. Nothing in this document is implemented.**

This document defines what the YouTube-like platform is, what it must do, what it explicitly will not do in the first release, and which assumptions the rest of the design depends on.

Related documents:

- [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md)
- [DATABASE_DESIGN.md](DATABASE_DESIGN.md)
- [VIDEO_PIPELINE.md](VIDEO_PIPELINE.md)
- [SECURITY_DESIGN.md](SECURITY_DESIGN.md)
- [API_DESIGN.md](API_DESIGN.md)
- [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)
- [ARCHITECTURAL_DECISIONS.md](ARCHITECTURAL_DECISIONS.md)

## 1. Project objectives

Build a functional video-sharing platform that a single clean Ubuntu server can run with one command.

The platform must let people:

- create an account and manage a channel
- upload a video and watch it after background processing
- browse, search, comment, react, subscribe, and organize playlists
- keep videos public, unlisted, or private, with owner-granted access to private videos
- report videos, and let moderators hide or restore them with an audit trail

The system must include a React frontend, a FastAPI backend, PostgreSQL, Redis, object storage, an FFmpeg processing worker, and a reverse proxy. Swagger UI must be served at `/docs`.

The design target is a **production-oriented modular monolith on one machine**, not a multi-region streaming platform. Capacity planning, failure handling, and security controls are real, but they are sized for one server.

### Capacity target

These numbers bound later decisions. They are targets for the first release, not measured results.

| Concern | Target |
| --- | --- |
| Deployment | One Ubuntu 22.04 or 24.04 host |
| Concurrent transcodes | 1 by default (`FFMPEG_CONCURRENCY`) |
| Upload size | 2 GB default, configurable |
| Video duration | 60 minutes default, configurable |
| Storage quota | 10 GB committed media per user, configurable |
| Concurrent viewers | Tens to low hundreds on one host |
| API latency | p95 under 300 ms for metadata reads, excluding upload and transcode |
| Backup | Daily database dump and object-storage volume copy. Recovery point 24 hours. Recovery time about 4 hours, manual |

## 2. Functional requirements

### 2.1 Authentication and profiles

- Register with email, password, display name, and channel handle.
- Log in, log out, and rotate sessions.
- Store passwords only as Argon2id hashes.
- Authenticate API calls with short-lived JWTs.
- Log out by revoking the refresh token and active playback sessions.
- Each user owns exactly one channel profile: handle, name, description, and avatar.

Account recovery by email and email verification are **out of scope** for the first release. See assumptions.

### 2.2 Home feed

The home feed lists videos that are all of the following:

- processing status `ready`
- visibility `public`
- moderation status `visible`
- not soft-deleted

Each card shows thumbnail, title, channel name, view count, and upload date. Results are cursor-paginated, newest first. The feed is global and chronological. It is not a subscription or recommendation feed.

### 2.3 Video viewing

A watch page provides:

- an HLS player
- title, description, tags, visibility (to the owner), dates, and view count
- like and dislike
- comments and one level of replies
- related public videos
- channel name, handle, subscriber count, and subscribe control

A view is counted after the player has actually started, not when metadata is fetched.

### 2.4 Channels

A channel page shows the profile, subscriber count, public videos, and public playlists. Authenticated users can subscribe and unsubscribe. Owners can edit their profile. Handles are immutable in the first release so links do not break.

### 2.5 Search

- Search videos by title and description.
- Search channels by name and handle.
- Paginate with cursors.
- Sort videos by relevance, newest, or view count.

Search uses PostgreSQL full-text search. A separate search engine is not introduced.

### 2.6 Upload

An authenticated user can:

- choose a video file and see upload progress
- set title, description, tags, and visibility
- supply an optional custom thumbnail
- see upload and processing status

The browser uploads the file directly to object storage with presigned multipart URLs. The API never receives video bytes.

### 2.7 Video infrastructure

- Private S3-compatible object storage (SeaweedFS).
- Originals, HLS playlists, HLS segments, and thumbnails are stored as objects, never as PostgreSQL bytea.
- A background worker runs FFmpeg to validate the file, extract a thumbnail, and build HLS renditions.
- The worker records status, retries retryable failures, and stops on fatal media errors.
- Viewers receive HLS through the application. Segment bytes are delivered from SeaweedFS using short-lived presigned URLs.

### 2.8 Privacy

Visibility is one of:

| Value | Who can watch | Listed in feed, search, channel, related |
| --- | --- | --- |
| `public` | Anyone | Yes |
| `unlisted` | Anyone with the video id | No |
| `private` | Owner, and users with a live access grant | No |

The owner grants and revokes private access per user. Moderators and administrators do not receive standing access to private videos. They can open a private video only as part of reviewing a report or a moderation action, and that access is audited.

Changing visibility does not delete grants. Grants matter only while visibility is `private`. Each playlist request and each segment request checks access again. A segment route answers with a short-lived redirect to object storage, so a revoke or hide takes effect on the next segment. A segment already in flight is allowed to finish.

### 2.9 Moderation

- Authenticated users can report a video with a reason and optional details. A user cannot have two open reports on the same video. Users cannot report their own video.
- Moderators and administrators can list reports, dismiss a report, hide a video, and restore a video.
- Hide removes the video from public surfaces. The owner can still see it and see that it is hidden.
- Every privileged action writes an append-only moderation history row, including the actor, action, target, and reason.
- Administrators can change user roles and deactivate users. Moderators cannot.

### 2.10 Roles

| Role | Capabilities beyond a normal user |
| --- | --- |
| `USER` | Own channel, own videos, grants, reports |
| `MODERATOR` | Review reports, hide and restore videos |
| `ADMIN` | Moderator capabilities, role changes, deactivate users |

The API enforces roles. The frontend hides controls only as a convenience.

## 3. Non-functional requirements

### Security

- Passwords hashed with Argon2id.
- Private buckets. SeaweedFS credentials never leave the server.
- Private and unlisted playback authorized on the server.
- Upload size, type, duration, quota, and rate limits enforced on the server. Each presigned part URL is bound to one exact length, so it cannot store more than the reserved quota.
- Privileged actions audited and append-only.
- OWASP-class controls for access control, injection, XSS, CSRF, and security headers are part of the design, not a later pass.

### Reliability

- Processing jobs are idempotent and claimed transactionally.
- A sweeper repairs uploads that completed in object storage but never queued a job, and jobs whose worker lock expired.
- Incomplete multipart uploads expire.
- Containers restart unless stopped. Health checks cover the API, database, Redis, SeaweedFS, and worker heartbeat.

### Maintainability

- One backend codebase, split into routers, services, repositories, and integrations.
- Business rules live in services, not in route functions.
- Schema changes go through Alembic.
- Request and response bodies are Pydantic models.
- Domain errors map to one HTTP error shape.

### Operability

- One command on a clean Ubuntu host: `bash deploy/ubuntu-up.sh`. The script checks the OS, Docker, free disk, and environment, refuses to delete volumes, and exits with a `deploy: FAIL` line when startup or migration does not become healthy.
- Configuration by environment variables.
- JSON logs with a request id.
- Persistent volumes for PostgreSQL, Redis, SeaweedFS, and transcode scratch space.

### Testability

- Pytest API integration tests, including visibility and role tests.
- Migration test that upgrades an empty database.
- Frontend unit tests and a small Playwright end-to-end path.
- Security tests for private-video access, role escalation, and upload authorization.

### Usability

- Responsive layout inspired by YouTube: top bar, feed grid, watch page with a player column and a related column.
- Every list and detail view has loading, empty, and error states.
- Upload progress reflects multipart part completion.

## 4. Scope

### In scope for the first release

- The features and architectural requirements listed in the project brief.
- Cursor pagination, PostgreSQL full text search, HLS with 360p and 720p (1080p only when the source is at least 1080p).
- Soft delete of videos by the owner.
- Manual backup and restore scripts.
- Swagger at `/docs` and ReDoc at `/redoc`.

### Out of scope for the first release

- Live streaming, premieres, stories, and shorts as a separate format.
- Recommendation ranking, watch history, and a subscription-only home feed.
- Email verification, password reset, and notification email.
- Comment reporting, user reporting, strikes, and appeals.
- DMCA counter-notice workflow. `copyright` is only a report reason.
- Subtitles, multiple audio tracks, chapters, and end screens.
- Monetization, ads, and analytics beyond view, like, dislike, comment, and subscriber counts.
- Native mobile apps. The web UI is responsive.
- Internationalization. The UI and API messages are English.
- Multi-server deployment, read replicas, CDN, and GPU transcoding.
- Real-time updates over WebSockets. Clients refetch or poll processing status.
- Hard delete of users. Deactivation only.
- Editing the uploaded video binary after the original is stored. Metadata remains editable. A failed video can be retried, or the owner deletes it and uploads again.

## 5. Assumptions

1. One person or a small team operates one Ubuntu server. High availability across hosts is not required.
2. A DNS name is available for a real deployment. The app is served at `https://<domain>` and object storage at `https://s3.<domain>`. Without DNS, the setup script can still start an HTTP demo, and secure cookies are disabled only in that demo mode.
3. The server has a public IPv4 address, ports 80 and 443, Docker, and enough disk for originals plus HLS copies (plan on roughly four times the original size during transcode).
4. There is no GPU. Transcoding is software x264.
5. No SMTP service is available, so verification and password reset are deferred.
6. The first administrator is created from bootstrap environment variables when no administrator exists.
7. English is the only catalogue language. PostgreSQL full-text search uses the `simple` configuration so non-English text is still tokenized without a wrong stemmer.
8. View deduplication stores a hash of the viewer key (user id, or IP plus user agent for anonymous viewers) for 48 hours. This is a product tradeoff, called out in the security design.
9. Dislike counts are visible, not hidden the way the current YouTube product hides them.
10. A channel is one-to-one with a user.
11. Playlist privacy is `public`, `unlisted`, or `private` (owner only). Playlists do not have per-user grants. A playlist entry still obeys the video's own visibility at read time.
12. Comment bodies are plain text, maximum 2,000 characters, with one reply level.
13. Defaults, all configurable: 2 GB per upload, 60 minutes duration, 10 GB quota, two concurrent active uploads per user, three automatic processing attempts, then up to three owner-triggered retries.
14. Browsers used for playback support HLS via `hls.js` (Chrome, Firefox, Edge) or native HLS (Safari). The playback cookie is same-site.
15. Clock source is UTC. All timestamps are `timestamptz`.

## 6. Success criteria for the design

The design is ready to implement when:

- the eight documents agree on status names, roles, storage layout, and request paths
- private video bytes are not available without a server-side access check
- a processing crash cannot lose the only copy of job state
- the first release can be deployed with one command and migrated with Alembic
- deferred work is explicit, so it is not accidentally built in the first pass

Implementation has not started.
