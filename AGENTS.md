# Agent guide

This repository is a YouTube-like video platform. Stage 1 is the development foundation only: health checks, configuration, database extensions, a heartbeat worker, and the application shell. Do not add accounts, uploads, processing, comments, reactions, subscriptions, or moderation until that stage is explicitly started.

## Source of truth

Read the relevant document in `docs/` before changing behavior:

- `docs/PROJECT_OVERVIEW.md` for scope
- `docs/SYSTEM_ARCHITECTURE.md` for process layout, configuration, and health
- `docs/DATABASE_DESIGN.md` for schema rules
- `docs/VIDEO_PIPELINE.md` for media flow
- `docs/SECURITY_DESIGN.md` for auth, storage, and tests
- `docs/API_DESIGN.md` for HTTP contracts
- `docs/DEVELOPMENT_PLAN.md` for the current stage and its definition of done
- `docs/ARCHITECTURAL_DECISIONS.md` for decisions that must not be reversed casually

If code and those documents disagree, fix the code or update the document in the same change and say why.

## Stack

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2
- PostgreSQL 16, Redis 7, SeaweedFS
- React, TypeScript, Vite, React Router, Tailwind CSS
- Docker Compose and Caddy for local development

Redis stores rate limits and the worker heartbeat. It is not a job queue. Do not add Celery, RQ, Dramatiq, or ARQ.

## Layout

- `backend/app/api` translates HTTP. It does not own business rules.
- `backend/app/core` holds configuration, logging, and error types.
- `backend/app/db` holds the engine, session, and Alembic wiring.
- `backend/app/infrastructure` holds Redis and object storage clients.
- `backend/app/worker` is the separate worker process.
- `frontend/src/api` talks to the backend. Components do not call `fetch` directly.
- `deploy/` holds Caddy and SeaweedFS init files used by Compose.

## Workflow

1. Confirm the task is inside the current stage in `docs/DEVELOPMENT_PLAN.md`.
2. Read the architecture sections the change touches.
3. Implement the smallest structure that matches those sections.
4. Run the tests and commands in the stage definition. Report the commands and their real results.
5. Do not claim a check passed unless it was executed.

## Security

- Validate input with Pydantic or an equivalent server-side check.
- The browser is not an authorization authority.
- `S3_ADMIN_ACCESS_KEY` and `S3_ADMIN_SECRET_KEY` belong only to the SeaweedFS server and `s3-init`.
- Do not log passwords, tokens, or presigned query strings.
- Do not put access tokens in `localStorage` or `sessionStorage`.
- Keep the media bucket private.

## Tests

- Important behavior needs an automated test.
- Database integration tests use PostgreSQL, not SQLite.
- Do not skip a failing test to make the suite green.
- Unit tests live in `backend/tests/unit` and `frontend` Vitest files.
- Stack tests live in `backend/tests/integration` and expect the Compose network.

## Git

- Use imperative commit subjects, for example `feat: establish full-stack development foundation`.
- Do not commit `.env`, `.env.seaweed-admin`, or any generated secret.
- Do not rewrite published history.

## Local stack

Copy `.env.example` to `.env` and `.env.seaweed-admin.example` to `.env.seaweed-admin`, then run `docker compose up --build`. The site is `http://localhost`. Swagger is `http://localhost/docs`.
