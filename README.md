# Playback

Stage 1 foundation for a YouTube-like video platform. The stack starts, checks its dependencies, and serves an application shell. Accounts, uploads, playback, comments, and moderation are later stages.

## Technology stack

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, Pytest
- PostgreSQL 16, Redis 7, SeaweedFS
- React, TypeScript, Vite, React Router, Tailwind CSS, Vitest
- Docker Compose and Caddy

Redis holds the worker heartbeat. It is not a job queue.

## Prerequisites

- Docker Engine with Compose v2
- Python 3.12, for backend unit tests outside Docker
- Node.js 22, for frontend tests and the production build

## Repository structure

- `backend/` API, worker, Alembic, and Pytest
- `frontend/` Vite application
- `deploy/caddy/` local Caddyfile
- `deploy/seaweedfs/` S3 identity setup
- `docs/` architecture documents
- `compose.yaml` local development stack

## Environment setup

```bash
cp .env.example .env
cp .env.seaweed-admin.example .env.seaweed-admin
```

`.env` is the API and worker configuration. `.env.seaweed-admin` is read only by the SeaweedFS server and the one-shot init container. Do not put `S3_ADMIN_ACCESS_KEY` or `S3_ADMIN_SECRET_KEY` in `.env`. The API exits if those variables are present.

The checked-in values are local placeholders.

## Docker Compose

```bash
docker compose up --build
```

Stop the stack with `docker compose down`. That keeps the database and object-storage volumes. Do not add `-v` unless you intend to delete them.

`docker compose up` builds the API, worker, frontend, and init images, pulls the pinned SeaweedFS image, waits for PostgreSQL, Redis, and SeaweedFS, initializes the private `media` bucket, then starts the API and worker.

The API container healthcheck calls `/health/live`. `/health/ready` also checks the worker heartbeat, and the worker waits until the API is healthy, so a ready healthcheck would deadlock startup.

## Frontend

Open [http://localhost](http://localhost). The home page calls `/health/ready` and shows PostgreSQL, Redis, object storage, and the worker. There is no video catalogue in this stage.

Caddy publishes port 80 only. This local stack is HTTP, and `COOKIE_SECURE` is false. Production TLS is a later stage.

## Swagger

- Swagger UI: [http://localhost/docs](http://localhost/docs)
- OpenAPI: [http://localhost/openapi.json](http://localhost/openapi.json)
- ReDoc: [http://localhost/redoc](http://localhost/redoc)

## Backend tests

From `backend/`, with the development extra installed:

```bash
pytest tests/unit
```

Unit tests do not need PostgreSQL, Redis, or SeaweedFS.

Integration tests use the Compose network and a real PostgreSQL server:

```bash
docker compose exec api pytest tests/integration
```

That run applies Alembic to an empty database named `youtube_migration_test`, checks `/health/ready` against the running dependencies, and checks that an anonymous object read is rejected.

## Frontend tests

```bash
cd frontend
npm test
npm run build
```

## Database migrations

The API container runs `python -m app.db.migrate` before it listens. The same command applies `upgrade head` under advisory lock `84215045`.

To run it again inside the stack:

```bash
docker compose exec api python -m app.db.migrate
```

The first revision enables `citext` and `pg_trgm` only. It does not create product tables.

## Troubleshooting

- `/health/live` is 200 while `/health/ready` is 503 for a few seconds after startup. Wait until the worker log says `worker heartbeat updated`.
- If the API logs `refusing to start because storage admin credentials are in the environment`, remove `S3_ADMIN_ACCESS_KEY` and `S3_ADMIN_SECRET_KEY` from `.env` and recreate the API container.
- If the API logs `object storage is not ready for startup`, `s3-init` did not finish. Check `docker compose logs s3-init`.
- Port 80 must be free on the host. PostgreSQL, Redis, and SeaweedFS are not published to the host.
- The local Caddy policy allows inline scripts so the Vite dev server can run. The stricter production policy is not this file.
- Presigned media URLs use `http://s3.localhost`. Add `127.0.0.1 s3.localhost` to `/etc/hosts` if that name does not resolve on your machine. Inside Compose, the edge service answers that name.
- Backend unit tests need Python 3.12. The API image provides it when the host interpreter is older.
