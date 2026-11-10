# API Design

**Status: design for product routes. Stage 1 implements `/health/live` and `/health/ready` only.**

The HTTP API is the only interface the frontend and the test suite use. Base path: `/api/v1`. Interactive documentation is FastAPI's Swagger UI at `/docs`, backed by `/openapi.json`. ReDoc is at `/redoc`.

Health routes sit outside the version prefix so deploy scripts have a stable URL.

Internal conventions below are normative for the implementation.

## 1. Conventions

### 1.1 Methods and bodies

- JSON request and response bodies, UTF-8.
- `POST` creates or triggers an action. `PATCH` sends partial updates with only the fields to change. `PUT` is used for the reaction endpoint because the body is the whole desired state. `DELETE` removes or soft-deletes as specified per route.
- Timestamps are ISO 8601 in UTC.
- Identifiers are UUID strings.
- Enumerations use the exact values in [DATABASE_DESIGN.md](DATABASE_DESIGN.md).

### 1.2 Authentication marks

| Mark | Meaning |
| --- | --- |
| public | No credential |
| optional | Public behavior, plus owner or member fields when a valid access token is present |
| user | Valid access token whose `epoch` matches an active user |
| owner | `user`, and the resource belongs to that user |
| moderator | `user` with role `MODERATOR` or `ADMIN` |
| admin | `user` with role `ADMIN` |
| playback cookie | Valid `hls_session` for this video id |

Mutations require `user` or a stronger mark. They do not accept the refresh cookie as authentication.

### 1.3 Pagination

Collection routes use cursor pagination (ADR-008).

```json
{
  "items": [],
  "next_cursor": "opaque-or-null"
}
```

The client passes `?cursor=` and `?limit=`. Default `limit` is 24. Maximum is 50. A missing `next_cursor` means the end. A malformed cursor is `400 INVALID_CURSOR`.

Cursors are unguessable only by opacity, not by signature. Tampering changes the page position and cannot skip the ACL, because every row is filtered again.

Sort orders:

| Collection | Default order |
| --- | --- |
| Home feed | `published_at desc`, `id desc` |
| Channel videos | same, after visibility filtering |
| Search videos | requested sort |
| Comments | `created_at asc`, `id asc` |
| Moderation reports | `created_at desc` |

### 1.4 Errors

```json
{
  "error": {
    "code": "VIDEO_NOT_FOUND",
    "message": "Video not found.",
    "details": [],
    "request_id": "req_..."
  }
}
```

`details` is a list of `{ "field": "title", "issue": "too_long" }` for validation errors, and empty otherwise.

| HTTP | When | Typical codes |
| --- | --- | --- |
| 400 | Malformed JSON, bad cursor, bad query | `INVALID_REQUEST`, `INVALID_CURSOR` |
| 401 | Missing or bad access token, epoch mismatch, bad login | `UNAUTHENTICATED`, `INVALID_CREDENTIALS` |
| 403 | Authenticated but wrong role | `FORBIDDEN` |
| 404 | Missing, or hidden by ACL | `VIDEO_NOT_FOUND`, `CHANNEL_NOT_FOUND`, `PLAYLIST_NOT_FOUND`, `COMMENT_NOT_FOUND` |
| 409 | State conflict | `EMAIL_TAKEN`, `HANDLE_TAKEN`, `INVALID_STATE`, `ALREADY_REPORTED`, `VIDEO_NOT_READY`, `UPLOAD_IN_PROGRESS`, `UPLOAD_EXPIRED` |
| 413 | Body too large | `PAYLOAD_TOO_LARGE` |
| 415 | Thumbnail type rejected | `UNSUPPORTED_MEDIA_TYPE` |
| 422 | Pydantic or upload verification | `VALIDATION_ERROR`, `UPLOAD_PART_INVALID`, `UPLOAD_REJECTED` |
| 429 | Rate limit | `RATE_LIMITED` |
| 503 | Dependency down | `SERVICE_UNAVAILABLE`, `STORAGE_UNAVAILABLE` |

Private videos a caller cannot access use 404, not 403 (ADR-011). Role failures on moderation routes use 403 because the route itself is not secret.

Successful deletes that soft-delete return `204` with no body. Idempotent repeats of hide, restore, unsubscribe, and abort return `200` or `204` as specified, not an error, when the end state is already the requested state. A second hide does not write a second audit row.

### 1.5 Resource shapes

Video summary, used in feed, search, channel lists, and related:

```json
{
  "id": "uuid",
  "title": "string",
  "thumbnail_url": "https://app/api/v1/videos/{id}/thumbnail",
  "channel": { "handle": "string", "name": "string" },
  "view_count": 0,
  "published_at": "timestamp",
  "duration_seconds": 0
}
```

`thumbnail_url` is safe to put in an `<img>` because image requests do not send `Authorization`.

- Feed, search, related, and public channel cards include the same-origin URL `/api/v1/videos/{id}/thumbnail`. That route is public only for videos that are public, ready, visible, and not deleted. It redirects to a two-minute presigned `GET`.
- Watch, studio, playlist, and moderation responses for a video that is private, unlisted, hidden, or not yet public include an absolute presigned thumbnail URL instead of the API path. The page refetches the resource if the image URL has expired.

Unlisted videos use the presigned form in the watch response. They do not use the public thumbnail route, so a bare thumbnail URL is not a second unlisted index. The watch page already knows the video id.

Video detail adds `description`, `tags`, `like_count`, `dislike_count`, `comment_count`, and `visibility` for every caller who can see the video. `processing_status` and `moderation_status` are included for the owner and for moderators on moderation routes only. Authenticated callers also receive `viewer_reaction` (`like`, `dislike`, or null) and `subscribed` for the channel.

Channel summary: `handle`, `name`, `description`, `avatar_url`, `subscriber_count`, `viewer_subscribed`.

Avatar URLs are public API redirects. Avatars are not secret.

## 2. Modules and endpoints

### 2.1 Health

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| GET | `/health/live` | public | Process is up |
| GET | `/health/ready` | public | Database, Redis, bucket, worker heartbeat |

These are on the public internet. They return status names and nothing else: no versions of internal hosts, no credentials. Ready failure is a 503 with `{"status":"not_ready","checks":{"postgres":"ok","redis":"fail","s3":"ok","worker":"ok"}}`.

### 2.2 Identity

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/register` | public | Create user, channel, quota. Body: `email`, `password`, `handle`, `name`. Sets refresh cookie. Returns access token and channel summary |
| POST | `/api/v1/auth/login` | public | Body: `email`, `password`. Same session result as register |
| POST | `/api/v1/auth/refresh` | refresh cookie | Rotates refresh token. Returns a new access token |
| POST | `/api/v1/auth/logout` | user plus refresh cookie | Revokes the refresh family, increments `session_epoch`, clears the refresh cookie. Playback-session revocation joins this transaction in stage 6 |
| GET | `/api/v1/auth/me` | user | User id, email, role, channel summary, quota summary |

Login and register responses:

```json
{
  "access_token": "jwt",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": { "id": "uuid", "email": "string", "role": "USER", "channel": { } }
}
```

The refresh token is not in the JSON body. The cookie name is `refresh_token`. It is `HttpOnly`, `SameSite=Lax`, `Path=/api/v1/auth`, and `Secure` when `COOKIE_SECURE=true`. Access tokens expire after `expires_in` seconds (900 by default). Logout does not keep an access-token denylist: the next request rejects the old token because its `epoch` no longer matches `users.session_epoch`. A second logout that still presents that access token returns `401`.

Password reset and email verification are not part of this API (ADR-018).

### 2.3 Videos and studio

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| GET | `/api/v1/videos` | public | Home feed. Query: `limit`, `cursor` |
| POST | `/api/v1/videos` | user | Create draft. Body: `title`, `description`, `tags`, `visibility`. Default visibility `private` |
| GET | `/api/v1/videos/{id}` | optional | Detail, ACL applied |
| PATCH | `/api/v1/videos/{id}` | owner | Update title, description, tags, visibility. Bumps `acl_version` when visibility changes |
| DELETE | `/api/v1/videos/{id}` | owner | Soft delete. `204` |
| GET | `/api/v1/videos/{id}/related` | optional | Up to 20 public ready visible videos. Same tags first, then same channel. Excludes the current id |
| POST | `/api/v1/videos/{id}/views` | optional | Count a view after playback start. `204`. No-op when the dedupe window has not elapsed |
| GET | `/api/v1/me/videos` | user | Owner's videos, all statuses, cursor by `created_at` |

Related-video selection is rule-based, not a ranked model. The response is a list of video summaries, not paginated, because the cap is 20.

### 2.4 Uploads and processing

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| POST | `/api/v1/videos/{id}/upload` | owner | Body: `byte_size`, `content_type`. Reserves quota and starts multipart upload. Returns `session_id`, `part_size`, `part_count`, `expires_at`. `503 STORAGE_UNAVAILABLE` if SeaweedFS does not create the upload. The reservation is released in that case |
| POST | `/api/v1/videos/{id}/upload/parts` | owner | Body: `part_numbers`. Returns `{ "parts": [{ "part_number", "url", "content_length", "expires_at" }] }`. `content_length` is the exact signed length. Part numbers outside `1 .. part_count`, duplicates, and batches over 20 are `422 UPLOAD_PART_INVALID`. Expired sessions are `409 UPLOAD_EXPIRED` |
| POST | `/api/v1/videos/{id}/upload/complete` | owner | Body: `parts: [{part_number, etag}]`. Verifies `ListParts` and `HEAD` against `expected_size`, then enqueues processing. A finished session returns the current video. A concurrent second caller receives `409 UPLOAD_IN_PROGRESS` and `Retry-After: 5`. A size or part mismatch is `422 UPLOAD_REJECTED` and creates no job. A SeaweedFS outage before the upload is finalized is `503 STORAGE_UNAVAILABLE` and leaves the session retryable |
| POST | `/api/v1/videos/{id}/upload/abort` | owner | Abort and release quota reservation. `204` |
| PUT | `/api/v1/videos/{id}/thumbnail` | owner | Raw image body or multipart file. Re-encoded and stored. Limit 2 MB |
| DELETE | `/api/v1/videos/{id}/thumbnail` | owner | Removes the custom thumbnail. Auto thumbnail remains. `204` |
| GET | `/api/v1/videos/{id}/processing` | owner | Processing status, attempt, `error_code` when failed, no raw FFmpeg log |
| POST | `/api/v1/videos/{id}/processing/retry` | owner | Manual retry when status is `failed` and attempts remain |

The browser uploads parts to the returned URLs. It does not send the video to FastAPI.

### 2.5 Playback and thumbnails

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| POST | `/api/v1/videos/{id}/playback` | optional | ACL plus `ready`. Creates playback session, sets `hls_session` cookie, returns `{ "playlist_url": "/api/v1/videos/{id}/hls/master.m3u8" }` |
| GET | `/api/v1/videos/{id}/hls/master.m3u8` | playback cookie | Rewritten master playlist, `content-type` `application/vnd.apple.mpegurl`, `no-store` |
| GET | `/api/v1/videos/{id}/hls/{rendition}/index.m3u8` | playback cookie | Variant playlist with relative segment names |
| GET | `/api/v1/videos/{id}/hls/{rendition}/{segment}` | playback cookie | ACL check, then `302` to a two-minute presigned segment URL |
| GET | `/api/v1/videos/{id}/thumbnail` | public only when the video is public, ready, visible, and not deleted | `302` to a two-minute thumbnail presign. Custom thumbnail preferred over auto. Unlisted, private, and hidden thumbnails are not served here; those responses embed a presigned URL in JSON |

`rendition` is an allow-list: `360`, `720`, `1080`. `segment` must match `segment_[0-9]{5}.ts`. Anything else is 404. These checks stop the route from becoming a general object proxy.

HLS routes are `include_in_schema` true so operators can see them, tagged `playback`. They are not meant to be called from Swagger without the cookie. The description in the schema says that.

### 2.6 Reactions and comments

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| PUT | `/api/v1/videos/{id}/reaction` | user | Body: `{ "value": "like" \| "dislike" \| "none" }`. Returns the new counts and the viewer reaction |
| GET | `/api/v1/videos/{id}/comments` | optional | Top-level comments. Each item includes up to the first 20 replies and `reply_count` |
| GET | `/api/v1/comments/{id}/replies` | optional | Remaining replies, cursor |
| POST | `/api/v1/videos/{id}/comments` | user | Body: `body`, optional `parent_id`. Parent must be a top-level comment on the same video |
| PATCH | `/api/v1/comments/{id}` | author | Edit body. Sets `edited` true in the response. The column is `updated_at`; the response includes `"edited": true` when `updated_at` is at least 1 second after `created_at` |
| DELETE | `/api/v1/comments/{id}` | author | Soft delete. `204` |

Comments are plain text. The server stores and returns them as text. It does not accept HTML.

### 2.7 Channels and subscriptions

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| GET | `/api/v1/channels/{handle}` | public | Channel profile. `viewer_subscribed` is false until subscriptions exist |
| GET | `/api/v1/channels/me` | user | The caller's channel. Handle `me` cannot exist because handles are at least 3 characters, so this route does not hide a public channel |
| GET | `/api/v1/channels/{handle}/videos` | optional | Public ready videos. Owner calling their own handle receives all non-deleted videos, with status fields |
| PATCH | `/api/v1/channels/me` | user | Update `name` and `description`. Handle and owner are not writable. There is no route that updates another user's channel |
| PUT | `/api/v1/channels/me/avatar` | user | Image upload, same rules as thumbnails, 2 MB |
| DELETE | `/api/v1/channels/me/avatar` | user | `204` |
| PUT | `/api/v1/channels/{handle}/subscription` | user | Subscribe. `204`. Idempotent |
| DELETE | `/api/v1/channels/{handle}/subscription` | user | Unsubscribe. `204`. Idempotent |
| GET | `/api/v1/channels/{handle}/playlists` | optional | Public playlists. Owner sees all of their playlists |

Subscribing to one's own channel is `409 INVALID_STATE`.

### 2.8 Playlists

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| POST | `/api/v1/playlists` | user | Body: `title`, `description`, `visibility` |
| GET | `/api/v1/playlists/{id}` | optional | Metadata plus items the caller may see. Private playlists are 404 for non-owners. Items the caller cannot watch are omitted on public and unlisted playlists, and shown as `{ "video_id", "unavailable": true }` on the owner's private playlist only if the video was deleted or hidden |
| PATCH | `/api/v1/playlists/{id}` | owner | Title, description, visibility |
| DELETE | `/api/v1/playlists/{id}` | owner | Hard delete playlist and items. Does not delete videos. `204` |
| POST | `/api/v1/playlists/{id}/items` | owner | Body: `video_id`. Video must be one the owner can watch. Position assigned at the end |
| DELETE | `/api/v1/playlists/{id}/items/{video_id}` | owner | `204` |

### 2.9 Search

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| GET | `/api/v1/search` | public | Query: `q` (1 to 100 characters), `type` = `video` \| `channel` \| `all`, `sort` = `relevance` \| `newest` \| `views`, `limit`, `cursor` |

Video results are public, ready, visible, not deleted. Channel results are handles and names. `sort=views` and `sort=newest` apply to videos. Channel results always use relevance. `type=all` returns two cursors:

```json
{
  "videos": { "items": [], "next_cursor": null },
  "channels": { "items": [], "next_cursor": null }
}
```

Empty `q` is `400`.

### 2.10 Reports and moderation

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| POST | `/api/v1/videos/{id}/reports` | user | Body: `reason`, optional `details`. Cannot report own video. One open report per user per video |
| GET | `/api/v1/moderation/reports` | moderator | Query: `status` default `open`, `cursor` |
| POST | `/api/v1/moderation/reports/{id}/dismiss` | moderator | Body: `reason`. Report becomes `dismissed` |
| POST | `/api/v1/moderation/videos/{id}/hide` | moderator | Body: `reason`. Hides the video. If called with `report_id`, that report becomes `action_taken` |
| POST | `/api/v1/moderation/videos/{id}/restore` | moderator | Body: `reason`. Sets moderation back to `visible` |
| GET | `/api/v1/moderation/videos/{id}/history` | moderator | Audit rows for that video, newest first, cursor |
| POST | `/api/v1/moderation/videos/{id}/playback` | moderator | Allowed when the video is hidden or has any report. Writes `view_private_video` when private or hidden. Then the same playback session behavior as the public playback route |
| POST | `/api/v1/admin/users/{id}/role` | admin | Body: `role`, `reason`. Cannot remove the last active admin |
| POST | `/api/v1/admin/users/{id}/deactivation` | admin | Body: `active` boolean, `reason` |

Moderator video lookup by id returns 404 when the video is private, was never reported, and is not hidden. The moderation player cannot be used as a private-video browser.

### 2.11 Quota

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| GET | `/api/v1/me/quota` | user | `bytes_committed`, `bytes_reserved`, `bytes_limit` |

Included so the upload screen can show capacity without overloading `/auth/me`. `/auth/me` still returns a short quota summary.

## 3. Endpoint authentication summary

Public read: feed, public video detail, public thumbnail, public channel, public playlists, search, health, login, register.

Optional auth: video detail, comments, related, view ping, playback create for public and unlisted, channel page.

Owner: metadata changes, upload, processing status, retry, delete, custom thumbnail.

User: comments, reactions, subscriptions, reports, playlist create, grant management.

Grants are owner routes under the video:

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| GET | `/api/v1/videos/{id}/grants` | owner | Active and revoked grants, handle and timestamps only |
| POST | `/api/v1/videos/{id}/grants` | owner | Body: `handle`. Creates an active grant. Bumps `acl_version` |
| DELETE | `/api/v1/videos/{id}/grants/{handle}` | owner | Revokes the active grant. `204`. Bumps `acl_version` |

Moderator and admin routes are listed in section 2.10.

Playback cookie routes are listed in section 2.5. They reject a bearer token that does not match the session user when both are present and disagree. Sending only the cookie is the normal case, because the video element will not set `Authorization`.

## 4. Idempotency and conflicts

| Call | Repeat behavior |
| --- | --- |
| Complete upload | 200 with the current video if already completed. In-flight duplicate is `409 UPLOAD_IN_PROGRESS`. A rejected upload is not retried through this route |
| Subscribe / unsubscribe | 204 if the end state is already true |
| Hide / restore | 200 with current moderation state, no second audit row |
| Reaction `like` when already `like` | 200, counts unchanged |
| Create grant when one is active | 409 |
| Register same email | 409 |
| Retry processing when a job is active | 409 `INVALID_STATE` |
| Comment delete already deleted | 204 |

Clients retry complete and view-count calls on network failure. Those two are safe to retry.

## 5. Versioning and compatibility

`/api/v1` is stable for the first release. Breaking changes require `/api/v2`. Adding a response field is not breaking. Removing or renaming one is breaking.

OpenAPI is generated from the route models. Hand-written YAML is not a second source of truth. The schema's security scheme is HTTP bearer. Cookie routes document the cookie in the description because OpenAPI cookie auth is easy to misconfigure; tests, not the schema file, prove the cookie behavior.

## 6. What the API will not do

- Receive original video bytes.
- Return S3 application credentials or storage admin credentials.
- Sign an upload part without a fixed `Content-Length`.
- Accept a complete body as proof of object size. `ListParts` and `HEAD` are the proof.
- Accept a role on registration.
- List private videos of other users.
- Serve a segment body through the API process.
- Expose FFmpeg stderr, password hashes, email addresses of other users, or refresh token hashes.
