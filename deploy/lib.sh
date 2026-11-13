# Shared helpers for the production installer and operational scripts.
# Shellcheck: sourced, not executed.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "deploy: FAIL usage: source deploy/lib.sh from another script." >&2
  exit 1
fi

deploy_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

compose_prod() {
  local root="$1"
  shift
  docker compose \
    --env-file "$root/.env" \
    -f "$root/deploy/compose.prod.yaml" \
    -p youtube-prod \
    "$@"
}

fail_line() {
  printf 'deploy: FAIL %s: %s\n' "$1" "$2"
}

# Prints "code|message" or nothing when the check passes.
check_os() {
  local file="$1"
  local arch="$2"
  local id="" version=""
  if [[ ! -f "$file" ]]; then
    printf 'os|/etc/os-release is missing. Use Ubuntu 22.04 or 24.04 on x86_64 or aarch64.\n'
    return 0
  fi
  id="$(. "$file" && printf '%s' "${ID:-}")"
  version="$(. "$file" && printf '%s' "${VERSION_ID:-}")"
  if [[ "$id" != "ubuntu" || ( "$version" != "22.04" && "$version" != "24.04" ) ]]; then
    printf 'os|This host is %s %s. Use Ubuntu 22.04 or 24.04.\n' "$id" "$version"
    return 0
  fi
  if [[ "$arch" != "x86_64" && "$arch" != "aarch64" ]]; then
    printf 'os|Architecture %s is not supported. Use x86_64 or aarch64.\n' "$arch"
  fi
}

check_privilege() {
  local systemd_dir="$1"
  local is_root="$2"
  local sudo_ok="$3"
  if [[ ! -d "$systemd_dir" ]]; then
    printf 'privilege|systemd is not running. Boot with systemd and run the installer again.\n'
    return 0
  fi
  if [[ "$is_root" != "1" && "$sudo_ok" != "1" ]]; then
    printf 'privilege|Run as root or as a user that can sudo. Docker installation and the firewall need it.\n'
  fi
}

check_memory() {
  local mem_kb="$1"
  if [[ "$mem_kb" -lt 2097152 ]]; then
    printf 'memory|MemTotal is under 2 GiB. Add memory before installing.\n'
    return 0
  fi
  if [[ "$mem_kb" -lt 4194304 ]]; then
    printf 'warn|Memory is under 4 GiB. The stack can start, and transcoding will be slow.\n'
  fi
}

check_disk() {
  local avail_kb="$1"
  local repeat="$2"
  local need_kb=$((20 * 1024 * 1024))
  if [[ "$repeat" == "1" ]]; then
    need_kb=$((2 * 1024 * 1024))
  fi
  if [[ "$avail_kb" -lt "$need_kb" ]]; then
    if [[ "$repeat" == "1" ]]; then
      printf 'disk|Less than 2 GiB is free. Free space and run the installer again.\n'
    else
      printf 'disk|Less than 20 GiB is free on the install filesystem. Free space and run the installer again.\n'
    fi
  fi
}

require_backup_root() {
  local root="${BACKUP_ROOT:-/var/backups/youtube-clone}"
  if [[ "$root" != /* || "$root" == "/" || "$root" == *..* || "$root" == */ ]]; then
    printf 'backup|BACKUP_ROOT must be an absolute directory without a trailing slash or ..\n'
    return 0
  fi
  printf '%s\n' "$root"
}

prune_backups() {
  local base="$1"
  local keep="${2:-14}"
  local name
  local -a names=()
  mapfile -t names < <(find "$base" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
  if [[ "${#names[@]}" -le "$keep" ]]; then
    return 0
  fi
  local remove=$((${#names[@]} - keep))
  local index=0
  for name in "${names[@]}"; do
    if [[ "$index" -ge "$remove" ]]; then
      break
    fi
    index=$((index + 1))
    if [[ "$name" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
      rm -rf "${base}/${name}"
    fi
  done
}

check_docs_opt_in() {
  local file="$1"
  local opt_in="${2:-}"
  local enabled="false"
  if [[ -f "$file" ]]; then
    enabled="$(awk -F= '$1=="DOCS_ENABLED" {print substr($0, index($0, "=")+1)}' "$file")"
  fi
  if [[ -z "$enabled" ]]; then
    enabled="false"
  fi
  if [[ "$enabled" == "true" && "$opt_in" != "1" ]]; then
    printf 'docs|DOCS_ENABLED=true would expose Swagger. Set DOCS_ENABLED=false in .env, or rerun with DOCS_OPT_IN=1 for a maintenance window and set it back to false afterward. Secrets were not changed.\n'
    return 0
  fi
  if [[ "$enabled" == "true" && "$opt_in" == "1" ]]; then
    printf 'warn|DOCS_OPT_IN=1 is exposing /docs for this run. Set DOCS_ENABLED=false and redeploy when maintenance is finished.\n'
  fi
}

check_missing_volume() {
  local name="$1"
  case "$name" in
    youtube-prod_pg_data|youtube-prod_redis_data|youtube-prod_seaweed_data) ;;
    *)
      printf 'volume|unexpected volume name\n'
      return 0
      ;;
  esac
  printf 'volume|%s is missing while .env already exists. Restore that volume. Moving .env aside is the only way to create a new empty database, and that is deliberate.\n' "$name"
}

check_env_file() {
  local file="$1"
  local demo="$2"
  if [[ ! -f "$file" ]]; then
    printf 'env|.env is missing. Let the installer create it, or copy the production template.\n'
    return 0
  fi
  local mode
  mode="$(stat -c '%a' "$file")"
  if [[ "$mode" != "600" ]]; then
    printf 'env|.env mode is %s. Run chmod 600 .env and start again.\n' "$mode"
    return 0
  fi
  (
  set -a
  # shellcheck disable=SC1090
  source "$file"
  set +a
  required=(
    DATABASE_URL REDIS_URL S3_ENDPOINT_URL S3_PUBLIC_ENDPOINT_URL S3_ACCESS_KEY
    S3_SECRET_KEY S3_BUCKET JWT_SECRET PUBLIC_APP_URL COOKIE_SECURE CORS_ORIGINS
    BOOTSTRAP_ADMIN_PASSWORD MAX_UPLOAD_BYTES PART_SIZE_BYTES MAX_PARTS
    POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB TRUSTED_HOSTS
  )
  for key in "${required[@]}"; do
    if [[ -z "${!key:-}" ]]; then
      printf 'env|%s is empty. Set it in .env and run the installer again.\n' "$key"
      exit 0
    fi
  done
  if [[ "${#JWT_SECRET}" -lt 32 ]]; then
    printf 'env|JWT_SECRET must be at least 32 characters. Generate a new secret in a new .env.\n'
    exit 0
  fi
  if [[ "$MAX_UPLOAD_BYTES" -lt 1 ]]; then
    printf 'env|MAX_UPLOAD_BYTES must be at least 1.\n'
    exit 0
  fi
  ceiling=$((PART_SIZE_BYTES * MAX_PARTS))
  if [[ "$MAX_UPLOAD_BYTES" -gt "$ceiling" ]]; then
    printf 'env|MAX_UPLOAD_BYTES exceeds PART_SIZE_BYTES * MAX_PARTS.\n'
    exit 0
  fi
  if [[ "$CORS_ORIGINS" != "$PUBLIC_APP_URL" ]]; then
    printf 'env|CORS_ORIGINS must equal PUBLIC_APP_URL.\n'
    exit 0
  fi
  if [[ "$demo" == "1" ]]; then
    if [[ "$COOKIE_SECURE" != "false" ]]; then
      printf 'env|DEMO_HTTP=1 requires COOKIE_SECURE=false. Do not use demo mode on a public network.\n'
      exit 0
    fi
  else
    if [[ "$COOKIE_SECURE" != "true" ]]; then
      printf 'env|COOKIE_SECURE must be true unless DEMO_HTTP=1.\n'
      exit 0
    fi
    if [[ -z "${PUBLIC_DOMAIN:-}" ]]; then
      printf 'env|PUBLIC_DOMAIN is required for TLS. Set it, or set DEMO_HTTP=1 for a local HTTP demo.\n'
      exit 0
    fi
    if [[ "$PUBLIC_APP_URL" != "https://${PUBLIC_DOMAIN}" ]]; then
      printf 'env|PUBLIC_APP_URL must be https://%s.\n' "$PUBLIC_DOMAIN"
      exit 0
    fi
    if [[ "$S3_PUBLIC_ENDPOINT_URL" != "https://s3.${PUBLIC_DOMAIN}" ]]; then
      printf 'env|S3_PUBLIC_ENDPOINT_URL must be https://s3.%s.\n' "$PUBLIC_DOMAIN"
      exit 0
    fi
  fi
  if grep -E -q '^(S3_ADMIN_ACCESS_KEY|S3_ADMIN_SECRET_KEY)=' "$file"; then
    printf 'env|.env contains storage admin credentials. Move them to .env.seaweed-admin and remove them from .env.\n'
  fi
  )
}
