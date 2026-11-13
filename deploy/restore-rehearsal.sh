#!/usr/bin/env bash
# Load a backup into a scratch database and a scratch bucket, then read both
# back. Requires the operator to type: restore scratch
# Does not drop the live database or delete the live media bucket.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

backup="${1:-}"
base_result="$(require_backup_root)"
if [[ "$base_result" == backup\|* ]]; then
  echo "deploy: FAIL ${base_result}" >&2
  exit 1
fi
base="${base_result%$'\n'}"
if [[ ! "$backup" =~ ^${base}/[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "deploy: FAIL restore: pass one timestamp directory under ${base}." >&2
  exit 1
fi
if [[ ! -s "$backup/db.dump" || ! -s "$backup/media.manifest.json" ]]; then
  echo "deploy: FAIL restore: db.dump or media.manifest.json is missing." >&2
  exit 1
fi
python3 - "$backup" <<'PY'
import hashlib, json, sys
from pathlib import Path
backup = Path(sys.argv[1])
manifest = json.loads((backup / "media.manifest.json").read_text())
for item in manifest:
    key = str(item["key"])
    if key.startswith("/") or ".." in key.split("/"):
        raise SystemExit("deploy: FAIL restore: manifest key escapes the media directory")
    target = (backup / "media" / key).resolve()
    root = (backup / "media").resolve()
    if root not in target.parents and target != root:
        raise SystemExit("deploy: FAIL restore: manifest key escapes the media directory")
    body = target.read_bytes()
    if hashlib.sha256(body).hexdigest() != item["sha256"] or len(body) != item["size"]:
        raise SystemExit(f"deploy: FAIL restore: {key} does not match the manifest")
print(f"manifest_ok {len(manifest)}")
PY
printf 'Type "restore scratch" to load this dump into a new database and bucket: '
read -r confirm
if [[ "$confirm" != "restore scratch" ]]; then
  echo "deploy: FAIL restore: confirmation was not received. Nothing was changed." >&2
  exit 1
fi

cid="$(compose_prod "$ROOT" ps -q postgres)"
api_cid="$(compose_prod "$ROOT" ps -q api)"
if [[ -z "$cid" || -z "$api_cid" ]]; then
  echo "deploy: FAIL restore: postgres or the API is not running." >&2
  exit 1
fi
stamp="$(basename "$backup" | tr '[:upper:]' '[:lower:]')"
name="restore_${stamp}"
bucket="restore${stamp}"
docker cp "$backup/db.dump" "$cid:/tmp/restore.dump"
docker exec "$cid" sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -c \"CREATE DATABASE ${name};\""
if ! docker exec "$cid" sh -c "pg_restore --no-owner -U \"\$POSTGRES_USER\" -d ${name} /tmp/restore.dump"; then
  docker exec "$cid" rm -f /tmp/restore.dump
  echo "deploy: FAIL restore: pg_restore failed. The live database was not changed." >&2
  exit 1
fi
docker exec "$cid" rm -f /tmp/restore.dump
count="$(docker exec "$cid" sh -c "psql -U \"\$POSTGRES_USER\" -d ${name} -Atc \"SELECT count(*) FROM users;\"")"
printf 'scratch database %s user count %s\n' "$name" "$count"

upload_py="$(mktemp)"
cleanup_py="$(mktemp)"
chmod 644 "$upload_py" "$cleanup_py"
trap 'rm -f "$upload_py" "$cleanup_py"' EXIT
cat > "$upload_py" <<'PY'
import hashlib
import json
import os
from pathlib import Path

import boto3
from botocore.client import Config

client = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"].rstrip("/"),
    aws_access_key_id=os.environ["S3_ADMIN_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_ADMIN_SECRET_KEY"],
    region_name=os.environ.get("S3_REGION", "us-east-1"),
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)
bucket = os.environ["RESTORE_BUCKET"]
from botocore.exceptions import ClientError

try:
    client.create_bucket(Bucket=bucket)
except ClientError as exc:
    code = exc.response.get("Error", {}).get("Code", "")
    if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
        raise
manifest = json.loads(Path("/restore-media.manifest.json").read_text())
for item in manifest:
    key = str(item["key"])
    body = (Path("/restore-media") / key).read_bytes()
    if hashlib.sha256(body).hexdigest() != item["sha256"]:
        raise SystemExit("restored object hash mismatch")
    client.put_object(Bucket=bucket, Key=key, Body=body)
if manifest:
    key = str(manifest[0]["key"])
    head = client.head_object(Bucket=bucket, Key=key)
    print(f"isolated_head {head['ContentLength']}")
else:
    print("isolated_head none")
print(f"uploaded {len(manifest)}")
PY
cat > "$cleanup_py" <<'PY'
import os

import boto3
from botocore.client import Config

client = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"].rstrip("/"),
    aws_access_key_id=os.environ["S3_ADMIN_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_ADMIN_SECRET_KEY"],
    region_name=os.environ.get("S3_REGION", "us-east-1"),
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)
bucket = os.environ["RESTORE_BUCKET"]
page = client.list_objects_v2(Bucket=bucket)
for item in page.get("Contents", []):
    client.delete_object(Bucket=bucket, Key=item["Key"])
client.delete_bucket(Bucket=bucket)
print("scratch bucket removed")
PY

compose_prod "$ROOT" run --no-deps --rm --user 0 \
  -e RESTORE_BUCKET="$bucket" \
  -v "$backup/media:/restore-media:ro" \
  -v "$backup/media.manifest.json:/restore-media.manifest.json:ro" \
  -v "$upload_py:/restore-upload.py:ro" \
  --entrypoint python \
  s3-init /restore-upload.py

docker cp "$backup/media" "$api_cid:/tmp/restore-media"
docker cp "$backup/media.manifest.json" "$api_cid:/tmp/restore-media.manifest.json"
docker exec -u 0 "$api_cid" chmod -R a+rX /tmp/restore-media /tmp/restore-media.manifest.json
docker exec -i -e RESTORE_DATABASE="$name" "$api_cid" python - <<'PY'
import json
import os
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.infrastructure.storage import storage_client

settings = get_settings()
database = os.environ["RESTORE_DATABASE"]
password = quote_plus(os.environ["POSTGRES_PASSWORD"])
user = quote_plus(os.environ["POSTGRES_USER"])
url = f"postgresql+psycopg://{user}:{password}@postgres:5432/{database}"
engine = create_engine(url)
with engine.connect() as connection:
    users = connection.execute(text("SELECT count(*) FROM users")).scalar()
print(f"app_users {users}")
manifest = json.loads(Path("/tmp/restore-media.manifest.json").read_text(encoding="utf-8"))
if not manifest:
    print("app_head none")
else:
    item = manifest[0]
    key = "acceptance/restore-probe"
    body = (Path("/tmp/restore-media") / str(item["key"])).read_bytes()
    client = storage_client()
    client.put_object(Bucket=settings.s3_bucket, Key=key, Body=body)
    try:
        head = client.head_object(Bucket=settings.s3_bucket, Key=key)
        print(f"app_head {head['ContentLength']}")
    finally:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
PY

compose_prod "$ROOT" run --no-deps --rm --user 0 \
  -e RESTORE_BUCKET="$bucket" \
  -v "$cleanup_py:/restore-cleanup.py:ro" \
  --entrypoint python \
  s3-init /restore-cleanup.py
docker exec "$cid" sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -c \"DROP DATABASE ${name};\""
docker exec -u 0 "$api_cid" rm -rf /tmp/restore-media /tmp/restore-media.manifest.json
printf 'Restore rehearsal finished. The scratch database and scratch bucket were removed. The probe object was deleted.\n'
