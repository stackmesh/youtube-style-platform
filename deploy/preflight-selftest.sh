#!/usr/bin/env bash
# Exercise every preflight failure message without starting the stack.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cat > "$tmp/os-debian" <<'EOF'
ID=debian
VERSION_ID=12
EOF
os_bad="$(check_os "$tmp/os-debian" x86_64)"
[[ "$os_bad" == os\|* ]] || { echo "os check failed: $os_bad"; exit 1; }
echo "deploy: FAIL $os_bad"

arch_bad="$(check_os /etc/os-release ppc64le)"
[[ "$arch_bad" == os\|* ]]
echo "deploy: FAIL $arch_bad"

priv="$(check_privilege /tmp/not-systemd 0 0)"
[[ "$priv" == privilege\|* ]]
echo "deploy: FAIL $priv"

priv_sudo="$(check_privilege /tmp 0 0)"
[[ "$priv_sudo" == privilege\|* ]]
echo "deploy: FAIL $priv_sudo"

mem="$(check_memory 1048576)"
[[ "$mem" == memory\|* ]]
echo "deploy: FAIL $mem"

warn="$(check_memory 3145728)"
[[ "$warn" == warn\|* ]]
echo "deploy: WARN ${warn#warn|}"

disk="$(check_disk 1024 0)"
[[ "$disk" == disk\|* ]]
echo "deploy: FAIL $disk"

disk_repeat="$(check_disk 1024 1)"
[[ "$disk_repeat" == disk\|* ]]
echo "deploy: FAIL $disk_repeat"

cat > "$tmp/env" <<'EOF'
DATABASE_URL=postgresql+psycopg://u:p@postgres:5432/youtube
REDIS_URL=redis://redis:6379/0
S3_ENDPOINT_URL=http://seaweedfs:8333
S3_PUBLIC_ENDPOINT_URL=https://s3.example.com
S3_ACCESS_KEY=media
S3_SECRET_KEY=secret-secret-secret-secret-secret
S3_BUCKET=media
JWT_SECRET=short
PUBLIC_APP_URL=https://example.com
PUBLIC_DOMAIN=example.com
COOKIE_SECURE=true
CORS_ORIGINS=https://example.com
BOOTSTRAP_ADMIN_PASSWORD=correct-password
MAX_UPLOAD_BYTES=10
PART_SIZE_BYTES=10
MAX_PARTS=2
POSTGRES_USER=youtube
POSTGRES_PASSWORD=secret-secret-secret
POSTGRES_DB=youtube
TRUSTED_HOSTS=example.com
EOF
chmod 644 "$tmp/env"
mode="$(check_env_file "$tmp/env" 0)"
[[ "$mode" == env\|*mode* ]]
echo "deploy: FAIL $mode"
chmod 600 "$tmp/env"
jwt="$(check_env_file "$tmp/env" 0)"
[[ "$jwt" == env\|JWT_SECRET* ]]
echo "deploy: FAIL $jwt"

python3 - "$tmp/env" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text().replace("JWT_SECRET=short", "JWT_SECRET=" + "a" * 32)
text = text.replace("CORS_ORIGINS=https://example.com", "CORS_ORIGINS=https://other.example")
path.write_text(text)
PY
cors="$(check_env_file "$tmp/env" 0)"
[[ "$cors" == env\|CORS_ORIGINS* ]]
echo "deploy: FAIL $cors"

printf '\nS3_ADMIN_SECRET_KEY=nope\n' >> "$tmp/env"
python3 - "$tmp/env" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text().replace("CORS_ORIGINS=https://other.example", "CORS_ORIGINS=https://example.com")
path.write_text(text)
PY
admin="$(check_env_file "$tmp/env" 0)"
[[ "$admin" == env\|*.env\ contains\ storage\ admin* ]]
echo "deploy: FAIL $admin"

volume="$(check_missing_volume youtube-prod_pg_data)"
[[ "$volume" == volume\|*missing* ]]
echo "deploy: FAIL $volume"

bad_root="$(BACKUP_ROOT=/tmp/../secrets require_backup_root)"
[[ "$bad_root" == backup\|* ]]
echo "deploy: FAIL $bad_root"
keep="$(mktemp -d)"
index=0
while [[ "$index" -lt 16 ]]; do
  printf -v name '202001%02dT000000Z' "$index"
  mkdir "$keep/$name"
  index=$((index + 1))
done
prune_backups "$keep" 14
remaining="$(find "$keep" -mindepth 1 -maxdepth 1 -type d | wc -l)"
[[ "$remaining" -eq 14 ]]
rm -rf "$keep"
echo "retention kept ${remaining}"

docs_off="$(mktemp)"
printf 'DOCS_ENABLED=false\n' > "$docs_off"
off_result="$(check_docs_opt_in "$docs_off" "")"
[[ -z "$off_result" ]]
echo "docs false is allowed"
docs_on="$(mktemp)"
printf 'DOCS_ENABLED=true\nJWT_SECRET=unchanged\n' > "$docs_on"
before_docs="$(sha256sum "$docs_on" | awk '{print $1}')"
on_result="$(check_docs_opt_in "$docs_on" "")"
[[ "$on_result" == docs\|* ]]
after_docs="$(sha256sum "$docs_on" | awk '{print $1}')"
[[ "$before_docs" == "$after_docs" ]]
echo "deploy: FAIL $on_result"
opt_result="$(check_docs_opt_in "$docs_on" 1)"
[[ "$opt_result" == warn\|* ]]
echo "deploy: WARN ${opt_result#warn|}"
rm -f "$docs_off" "$docs_on"

install="$(mktemp -d)"
mkdir -p "$install/deploy/caddy"
(
  set -euo pipefail
  export DEPLOY_SOURCE_ONLY=1
  # shellcheck source=deploy/ubuntu-up.sh
  source "$ROOT/deploy/ubuntu-up.sh"
  ROOT="$install"
  DEMO_HTTP=1
  HTTP_PORT=8088
  write_env_if_missing
  first_sum="$(sha256sum "$ROOT/.env" | awk '{print $1}')"
  write_env_if_missing
  second_sum="$(sha256sum "$ROOT/.env" | awk '{print $1}')"
  [[ "$first_sum" == "$second_sum" ]]
  python3 - "$ROOT/.env" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
values = {}
for line in text.splitlines():
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key] = value
assert values["DOCS_ENABLED"] == "false"
assert values["CORS_ORIGINS"] == values["PUBLIC_APP_URL"] == "http://127.0.0.1:8088"
assert values["COOKIE_SECURE"] == "false"
assert values["TRUSTED_HOSTS"] == "127.0.0.1,localhost,s3.localhost"
assert values["S3_PUBLIC_ENDPOINT_URL"] == "http://s3.localhost:8088"
assert "S3_ADMIN_ACCESS_KEY" not in values
assert "S3_ADMIN_SECRET_KEY" not in values
PY
  mode="$(stat -c '%a' "$ROOT/.env")"
  [[ "$mode" == "600" ]]
  render_caddy
  if grep -q '/docs' "$ROOT/deploy/caddy/Caddyfile.runtime"; then
    exit 1
  fi
  python3 - "$ROOT/.env" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.write_text(path.read_text().replace("DOCS_ENABLED=false", "DOCS_ENABLED=true"))
PY
  opted_sum="$(sha256sum "$ROOT/.env" | awk '{print $1}')"
  DOCS_OPT_IN=""
  render_caddy
  if grep -q '/docs' "$ROOT/deploy/caddy/Caddyfile.runtime"; then
    exit 1
  fi
  [[ "$(sha256sum "$ROOT/.env" | awk '{print $1}')" == "$opted_sum" ]]
  DOCS_OPT_IN=1
  render_caddy
  grep -q '/docs' "$ROOT/deploy/caddy/Caddyfile.runtime"
  [[ "$(sha256sum "$ROOT/.env" | awk '{print $1}')" == "$opted_sum" ]]
)
rm -rf "$install"

tls="$(mktemp -d)"
mkdir -p "$tls/deploy/caddy"
(
  set -euo pipefail
  export DEPLOY_SOURCE_ONLY=1
  # shellcheck source=deploy/ubuntu-up.sh
  source "$ROOT/deploy/ubuntu-up.sh"
  ROOT="$tls"
  unset DEMO_HTTP
  PUBLIC_DOMAIN=videos.example.com
  ACME_EMAIL=ops@example.com
  write_env_if_missing
  python3 - "$ROOT/.env" <<'PY'
import sys
from pathlib import Path
values = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if not line or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key] = value
assert values["DOCS_ENABLED"] == "false"
assert values["COOKIE_SECURE"] == "true"
assert values["PUBLIC_APP_URL"] == "https://videos.example.com"
assert values["CORS_ORIGINS"] == values["PUBLIC_APP_URL"]
assert values["S3_PUBLIC_ENDPOINT_URL"] == "https://s3.videos.example.com"
assert values["TRUSTED_HOSTS"] == "videos.example.com,s3.videos.example.com"
PY
  render_caddy
  caddy_text="$(cat "$ROOT/deploy/caddy/Caddyfile.runtime")"
  grep -q 'Strict-Transport-Security' <<<"$caddy_text"
  if grep -q "unsafe-inline" <<<"$caddy_text"; then
    exit 1
  fi
  if grep -q "unsafe-eval" <<<"$caddy_text"; then
    exit 1
  fi
  if grep -q '/docs' <<<"$caddy_text"; then
    exit 1
  fi
)
rm -rf "$tls"
echo "first install keeps docs closed"
echo "preflight selftest passed"
