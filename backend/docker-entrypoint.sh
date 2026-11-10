#!/bin/sh
set -eu
python -m app.db.migrate
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
