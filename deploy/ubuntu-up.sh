#!/usr/bin/env bash
# Idempotent production installer for Ubuntu 22.04 or 24.04.
# Usage, from a checkout at /opt/youtube-clone:
#   sudo bash deploy/ubuntu-up.sh
# Local HTTP demo, which does not request a certificate:
#   sudo DEMO_HTTP=1 HTTP_PORT=80 bash deploy/ubuntu-up.sh
# TLS:
#   sudo PUBLIC_DOMAIN=videos.example.com ACME_EMAIL=you@example.com bash deploy/ubuntu-up.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

fail() {
  fail_line "$1" "$2"
  exit 1
}

apply_check() {
  local result="$1"
  if [[ -z "$result" ]]; then
    return 0
  fi
  local code="${result%%|*}"
  local message="${result#*|}"
  if [[ "$code" == "warn" ]]; then
    printf 'deploy: WARN %s\n' "$message"
    return 0
  fi
  fail "$code" "$message"
}

random_hex() {
  openssl rand -hex "$1"
}

write_env_if_missing() {
  if [[ -f "$ROOT/.env" || -f "$ROOT/.env.seaweed-admin" ]]; then
    return 0
  fi
  local http_port="${HTTP_PORT:-80}"
  local pg_user="youtube"
  local pg_password pg_db jwt s3_key s3_secret admin_key admin_secret bootstrap
  pg_password="$(random_hex 24)"
  pg_db="youtube"
  jwt="$(random_hex 32)"
  s3_key="media-$(random_hex 8)"
  s3_secret="$(random_hex 32)"
  admin_key="admin-$(random_hex 8)"
  admin_secret="$(random_hex 32)"
  bootstrap="$(random_hex 16)"
  local public_url cors cookie domain s3_public trusted docs
  if [[ "${DEMO_HTTP:-}" == "1" ]]; then
    domain=""
    if [[ "$http_port" == "80" ]]; then
      public_url="http://127.0.0.1"
      s3_public="http://s3.localhost"
    else
      public_url="http://127.0.0.1:${http_port}"
      s3_public="http://s3.localhost:${http_port}"
    fi
    cors="$public_url"
    cookie="false"
    trusted="127.0.0.1,localhost,s3.localhost"
    docs="${DOCS_ENABLED:-false}"
  else
    domain="${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN or DEMO_HTTP=1}"
    if [[ -z "${ACME_EMAIL:-}" ]]; then
      fail env "ACME_EMAIL is required for TLS. Set it or use DEMO_HTTP=1."
    fi
    public_url="https://${domain}"
    s3_public="https://s3.${domain}"
    cors="$public_url"
    cookie="true"
    trusted="${domain},s3.${domain}"
    docs="${DOCS_ENABLED:-false}"
  fi
  umask 077
  cat > "$ROOT/.env" <<EOF
POSTGRES_USER=${pg_user}
POSTGRES_PASSWORD=${pg_password}
POSTGRES_DB=${pg_db}
DATABASE_URL=postgresql+psycopg://${pg_user}:${pg_password}@postgres:5432/${pg_db}
REDIS_URL=redis://redis:6379/0
S3_ENDPOINT_URL=http://seaweedfs:8333
S3_PUBLIC_ENDPOINT_URL=${s3_public}
S3_ACCESS_KEY=${s3_key}
S3_SECRET_KEY=${s3_secret}
S3_BUCKET=media
S3_REGION=us-east-1
S3_BUCKET_QUOTA_BYTES=10737418240
PART_SIZE_BYTES=16777216
MAX_PARTS=512
UPLOAD_PART_URL_TTL_SECONDS=1200
JWT_SECRET=${jwt}
ACCESS_TOKEN_TTL_SECONDS=900
REFRESH_TOKEN_TTL_SECONDS=1209600
PUBLIC_APP_URL=${public_url}
PUBLIC_DOMAIN=${domain}
COOKIE_SECURE=${cookie}
CORS_ORIGINS=${cors}
TRUSTED_HOSTS=${trusted}
DOCS_ENABLED=${docs}
BOOTSTRAP_ADMIN_EMAIL=${BOOTSTRAP_ADMIN_EMAIL:-admin@example.com}
BOOTSTRAP_ADMIN_PASSWORD=${bootstrap}
MAX_UPLOAD_BYTES=2147483648
MAX_DURATION_SECONDS=3600
DEFAULT_QUOTA_BYTES=10737418240
MAX_ACTIVE_UPLOADS=2
FFMPEG_CONCURRENCY=1
WORK_DIR=/var/lib/media-work
PLAYBACK_SEGMENT_TTL_SECONDS=120
STORAGE_STARTUP_CHECK=true
EOF
  cat > "$ROOT/.env.seaweed-admin" <<EOF
S3_ADMIN_ACCESS_KEY=${admin_key}
S3_ADMIN_SECRET_KEY=${admin_secret}
EOF
  chmod 600 "$ROOT/.env" "$ROOT/.env.seaweed-admin"
  printf 'deploy: generated .env and .env.seaweed-admin with mode 600. Existing files were not replaced.\n'
  printf 'deploy: bootstrap admin email is %s. The password is in .env as BOOTSTRAP_ADMIN_PASSWORD.\n' "${BOOTSTRAP_ADMIN_EMAIL:-admin@example.com}"
}

render_caddy() {
  local dest="$ROOT/deploy/caddy/Caddyfile.runtime"
  local http_port="${HTTP_PORT:-80}"
  local s3_origin="http://s3.localhost"
  if [[ "$http_port" != "80" ]]; then
    s3_origin="http://s3.localhost:${http_port}"
  fi
  local docs_enabled="false"
  local api_paths="/api/* /health/*"
  if [[ -f "$ROOT/.env" ]]; then
    docs_enabled="$(awk -F= '$1=="DOCS_ENABLED" {print substr($0, index($0, "=")+1)}' "$ROOT/.env")"
  fi
  if [[ "$docs_enabled" == "true" ]]; then
    api_paths="/api/* /health/* /docs /docs/* /redoc /redoc/* /openapi.json"
  fi
  umask 077
  if [[ "${DEMO_HTTP:-}" == "1" ]]; then
    cat > "$dest" <<EOF
{
	auto_https off
	admin off
}
http://127.0.0.1, http://localhost {
	encode gzip
	header {
		X-Content-Type-Options nosniff
		X-Frame-Options DENY
		Referrer-Policy strict-origin-when-cross-origin
		Permissions-Policy "camera=(), microphone=(), geolocation=()"
		Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: ${s3_origin}; media-src 'self' ${s3_origin}; connect-src 'self' ${s3_origin}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
		Cache-Control "no-cache"
	}
	@hashed path /assets/*
	header @hashed Cache-Control "public, max-age=31536000, immutable"
	@api path ${api_paths}
	handle @api {
		reverse_proxy api:8000
	}
	handle {
		root * /srv
		try_files {path} /index.html
		file_server
	}
}
http://s3.localhost {
	reverse_proxy seaweedfs:8333 {
		header_up Host {host}
	}
}
EOF
  else
    local domain="$PUBLIC_DOMAIN"
    cat > "$dest" <<EOF
{
	email ${ACME_EMAIL}
	admin off
}
${domain} {
	encode gzip
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options nosniff
		X-Frame-Options DENY
		Referrer-Policy strict-origin-when-cross-origin
		Permissions-Policy "camera=(), microphone=(), geolocation=()"
		Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://s3.${domain}; media-src 'self' https://s3.${domain}; connect-src 'self' https://s3.${domain}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
		Cache-Control "no-cache"
	}
	@hashed path /assets/*
	header @hashed Cache-Control "public, max-age=31536000, immutable"
	@api path ${api_paths}
	handle @api {
		reverse_proxy api:8000
	}
	handle {
		root * /srv
		try_files {path} /index.html
		file_server
	}
}
s3.${domain} {
	reverse_proxy seaweedfs:8333 {
		header_up Host {host}
	}
}
EOF
  fi
  chmod 644 "$dest"
}

ensure_docker() {
  if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$(id -u)" -ne 0 ]]; then
    sudo -n true || fail privilege "Docker is missing and sudo needs a password. Install Docker Engine and the Compose v2 plugin, then run the installer again."
  fi
  local runner="sudo"
  if [[ "$(id -u)" -eq 0 ]]; then
    runner=""
  fi
  $runner apt-get update
  $runner apt-get install -y ca-certificates curl
  $runner install -m 0755 -d /etc/apt/keyrings
  $runner curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  $runner chmod a+r /etc/apt/keyrings/docker.asc
  local codename
  codename="$(. /etc/os-release && printf '%s' "$VERSION_CODENAME")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" |
    $runner tee /etc/apt/sources.list.d/docker.list >/dev/null
  $runner apt-get update
  $runner apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  if ! docker info >/dev/null 2>&1; then
    fail docker "Docker was installed but docker info failed. Start the docker service and run the installer again."
  fi
}

check_volumes() {
  local name
  for name in pg_data redis_data seaweed_data; do
    if ! docker volume inspect "youtube-prod_${name}" >/dev/null 2>&1; then
      apply_check "$(check_missing_volume "youtube-prod_${name}")"
    fi
  done
  if ! docker volume inspect youtube-prod_work_data >/dev/null 2>&1; then
    printf 'deploy: WARN work_data is missing. Scratch transcode files from the previous run are gone. Media in seaweed_data is unchanged.\n'
  fi
}

check_admin_keys_absent() {
  local config
  config="$(compose_prod "$ROOT" config --format json)"
  python3 -c '
import json, sys
doc = json.loads(sys.stdin.read())
forbidden = {"S3_ADMIN_ACCESS_KEY", "S3_ADMIN_SECRET_KEY"}
for name in ("api", "worker", "edge"):
    env = doc.get("services", {}).get(name, {}).get("environment", {})
    if isinstance(env, list):
        keys = {item.split("=", 1)[0] for item in env}
    else:
        keys = set(env)
    leaked = sorted(forbidden & keys)
    if leaked:
        sys.exit("api or worker received " + ",".join(leaked))
' <<<"$config" || fail env "The API, worker, or edge service received storage admin credentials. Remove them from .env. They belong only in .env.seaweed-admin."
}

wait_ready() {
  local url="$1"
  local attempt body
  for attempt in $(seq 1 90); do
    body="$(curl -fsS "$url" 2>/dev/null || true)"
    if [[ "$body" == *'"postgres":"ok"'* && "$body" == *'"redis":"ok"'* && "$body" == *'"s3":"ok"'* && "$body" == *'"worker":"ok"'* ]]; then
      printf '%s\n' "$body"
      return 0
    fi
    sleep 2
  done
  printf '%s\n' "$body"
  return 1
}

check_exposure() {
  if ! command -v ss >/dev/null 2>&1; then
    return 0
  fi
  local line port addr
  while read -r line; do
    addr="${line%% *}"
    port="${addr##*:}"
    case "$port" in
      5432|6379|8333|9333|8080|8888)
        case "$addr" in
          127.0.0.1:*|\[::1\]:*) ;;
          *)
            fail exposure "Port ${port} is listening on ${addr}. Remove the host publish for PostgreSQL, Redis, and SeaweedFS, then start again."
            ;;
        esac
        ;;
    esac
  done < <(ss -H -lnt | awk '{print $4}')
}

configure_firewall() {
  if [[ "${DEPLOY_CONFIGURE_UFW:-}" != "1" ]]; then
    printf 'deploy: firewall left unchanged. Set DEPLOY_CONFIGURE_UFW=1 to deny incoming traffic except OpenSSH, 80/tcp, and 443/tcp.\n'
    return 0
  fi
  if ! command -v ufw >/dev/null 2>&1; then
    fail privilege "ufw is not installed. Install ufw or leave DEPLOY_CONFIGURE_UFW unset."
  fi
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow OpenSSH
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
}

main() {
  cd "$ROOT"
  apply_check "$(check_os /etc/os-release "$(uname -m)")"
  local sudo_ok=0
  if [[ "$(id -u)" -eq 0 ]]; then
    sudo_ok=1
  elif sudo -n true >/dev/null 2>&1; then
    sudo_ok=1
  fi
  apply_check "$(check_privilege /run/systemd/system "$([[ "$(id -u)" -eq 0 ]] && echo 1 || echo 0)" "$sudo_ok")"
  local mem_kb avail_kb
  mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
  avail_kb="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
  apply_check "$(check_memory "$mem_kb")"
  local env_existed=0
  if [[ -f "$ROOT/.env" ]]; then
    env_existed=1
  fi
  apply_check "$(check_disk "$avail_kb" "$env_existed")"
  ensure_docker
  if [[ "$env_existed" -eq 1 ]]; then
    check_volumes
  fi
  write_env_if_missing
  apply_check "$(check_env_file "$ROOT/.env" "${DEMO_HTTP:-0}")"
  if [[ ! -f "$ROOT/.env.seaweed-admin" ]]; then
    fail env ".env.seaweed-admin is missing. Restore it. The installer will not invent a second admin key while .env exists."
  fi
  chmod 600 "$ROOT/.env.seaweed-admin"
  # shellcheck disable=SC1090
  PUBLIC_DOMAIN="$(awk -F= '$1=="PUBLIC_DOMAIN" {print substr($0, index($0, "=")+1)}' "$ROOT/.env")"
  export PUBLIC_DOMAIN
  render_caddy
  export CADDYFILE="$ROOT/deploy/caddy/Caddyfile.runtime"
  export HTTP_PORT="${HTTP_PORT:-80}"
  export HTTPS_PORT="${HTTPS_PORT:-443}"
  export EDGE_BIND="${EDGE_BIND:-0.0.0.0}"
  check_admin_keys_absent
  configure_firewall
  compose_prod "$ROOT" up -d --build --remove-orphans
  local ready_url="http://127.0.0.1:${HTTP_PORT}/health/ready"
  if [[ "${DEMO_HTTP:-}" != "1" ]]; then
    ready_url="https://${PUBLIC_DOMAIN}/health/ready"
  fi
  local ready_body=""
  if ! ready_body="$(wait_ready "$ready_url")"; then
    printf '%s\n' "$ready_body"
    local logs
    logs="$(compose_prod "$ROOT" logs api --tail 80 || true)"
    printf '%s\n' "$logs"
    if printf '%s' "$logs" | grep -E -q 'alembic|Error|FAILED'; then
      fail migrate "The API migration did not finish. The database was not deleted and was not downgraded. Restore from the latest backup if a committed migration is wrong. See docs/BACKUP_AND_RESTORE.md."
    fi
    fail health "Ready did not return postgres, redis, s3, and worker ok within 180 seconds. Read the API log above and start again. Volumes were kept."
  fi
  check_exposure
  local public_url
  public_url="$(awk -F= '$1=="PUBLIC_APP_URL" {print substr($0, index($0, "=")+1)}' "$ROOT/.env")"
  printf 'deploy: OK %s\n' "$public_url"
  printf 'Status: bash deploy/ops.sh status\n'
  printf 'Logs: bash deploy/ops.sh logs\n'
  printf 'Backup: bash deploy/backup.sh\n'
  printf 'Swagger: %s/docs\n' "$public_url"
}

if [[ "${DEPLOY_SOURCE_ONLY:-}" == "1" ]]; then
  return 0 2>/dev/null || exit 0
fi

main "$@"
