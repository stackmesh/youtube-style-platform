#!/bin/sh
set -eu
python -m app.db.migrate
python -m app.identity.bootstrap
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
