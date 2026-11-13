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
echo "preflight selftest passed"
