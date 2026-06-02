#!/usr/bin/env bash
set -Eeuo pipefail

db_mode="local"
storage_mode="local"
port="8000"
proxy_port="5433"
apply_migrations=false
no_runserver=false
stop_proxy_on_exit=false

project_id="retailops-db-20260516"
instance_connection_name="retailops-db-20260516:northamerica-south1:retailops-postgres-01"
secret_name="retailops-db-password"
db_name="retailops"
db_user="retailops_app"
local_db_name=""

postgres_host="127.0.0.1"
postgres_port="5432"
postgres_db_name="retailops"
postgres_user="retailops_app"
postgres_password=""
postgres_sslmode="disable"

proxy_address="127.0.0.1"
django_address="127.0.0.1"
proxy_path="${CLOUD_SQL_PROXY_PATH:-cloud-sql-proxy}"
credentials_file="${HOME}/.config/retailops/cloudsql/retailops-cloudsql-client.json"
python_command="${PYTHON:-python3}"
startup_timeout_seconds="30"

media_project_id="retailops-media"
media_public_bucket_name="retailops-public-assets"
media_private_bucket_name="retailops-private-documents"
media_credentials_file="${HOME}/.config/retailops/media/retailops-media-client.json"
media_use_iam_sign_blob=false
media_signer_service_account=""
media_public_custom_endpoint=""
media_root=""
media_url=""

s3_provider="rustfs"
s3_endpoint_url=""
s3_access_key_id=""
s3_secret_access_key=""
s3_public_bucket_name="retailops-public-assets"
s3_private_bucket_name="retailops-private-documents"
s3_region_name="us-east-1"
s3_product_public=true
s3_receipt_signed_urls=true

proxy_started=false
proxy_pid=""

usage() {
  cat <<'EOF'
Usage: scripts/start-retailops.sh [options]

Database:
  --db-mode local|postgres|cloud
  --local-db-name <path>
  --postgres-host <host>
  --postgres-port <port>
  --postgres-db-name <name>
  --postgres-user <user>
  --postgres-password <password>
  --postgres-sslmode <mode>
  --project-id <id>
  --instance-connection-name <name>
  --secret-name <name>
  --db-name <name>
  --db-user <user>
  --proxy-address <address>
  --proxy-port <port>
  --proxy-path <path-or-command>
  --credentials-file <path>
  --startup-timeout-seconds <seconds>

Storage:
  --storage-mode local|s3|cloud
  --media-root <path>
  --media-url <url>
  --media-project-id <id>
  --media-public-bucket-name <name>
  --media-private-bucket-name <name>
  --media-credentials-file <path>
  --media-use-iam-sign-blob
  --media-signer-service-account <email>
  --media-public-custom-endpoint <url>
  --s3-provider rustfs|garage|custom
  --s3-endpoint-url <url>
  --s3-access-key-id <key>
  --s3-secret-access-key <secret>
  --s3-public-bucket-name <name>
  --s3-private-bucket-name <name>
  --s3-region-name <region>
  --s3-product-public | --no-s3-product-public
  --s3-receipt-signed-urls | --no-s3-receipt-signed-urls

Runtime:
  --port <port>
  --django-address <address>
  --python-command <command>
  --apply-migrations
  --no-runserver
  --stop-proxy-on-exit
  -h, --help
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1 && [[ ! -x "$command_name" ]]; then
    die "Required command not found: $command_name"
  fi
}

validate_choice() {
  local name="$1"
  local value="$2"
  shift 2
  local allowed
  for allowed in "$@"; do
    [[ "$value" == "$allowed" ]] && return 0
  done
  die "$name must be one of: $*"
}

clear_db_env() {
  unset DATABASE_URL DB_ENGINE DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT DB_SSLMODE DB_CONN_MAX_AGE
}

clear_prefixed_env() {
  local prefix="$1"
  local name
  while IFS='=' read -r name _; do
    if [[ "$name" == "$prefix"* ]]; then
      unset "$name"
    fi
  done < <(env)
}

clear_media_env() {
  unset MEDIA_STORAGE_BACKEND MEDIA_ROOT MEDIA_URL
  clear_prefixed_env "MEDIA_GCS_"
  clear_prefixed_env "MEDIA_S3_"
}

tcp_open() {
  local address="$1"
  local port="$2"
  "$python_command" - "$address" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

address, port = sys.argv[1], int(sys.argv[2])
try:
    with socket.create_connection((address, port), timeout=1):
        pass
except OSError:
    sys.exit(1)
PY
}

gcloud_secret_access() {
  require_command gcloud
  local value
  if ! value="$(gcloud secrets versions access latest --secret="$secret_name" --project="$project_id" 2>/dev/null)"; then
    die "Could not read Secret Manager secret '$secret_name' from project '$project_id'."
  fi
  if [[ -z "${value//[[:space:]]/}" ]]; then
    die "Secret Manager returned an empty value for '$secret_name'."
  fi
  printf '%s' "$value" | head -n 1
}

start_cloud_sql_proxy_if_needed() {
  if tcp_open "$proxy_address" "$proxy_port"; then
    echo "Cloud SQL Auth Proxy already listening on ${proxy_address}:${proxy_port}."
    return 0
  fi

  require_command "$proxy_path"

  mkdir -p "$repo_root/db_backups"
  local stdout_log="$repo_root/db_backups/cloud-sql-proxy.stdout.log"
  local stderr_log="$repo_root/db_backups/cloud-sql-proxy.stderr.log"
  local proxy_args=("--address" "$proxy_address" "--port" "$proxy_port")

  if [[ -f "$credentials_file" ]]; then
    proxy_args+=("--credentials-file" "$credentials_file")
  else
    echo "Cloud SQL credentials file not found; proxy will use application default credentials."
  fi
  proxy_args+=("$instance_connection_name")

  echo "Starting Cloud SQL Auth Proxy on ${proxy_address}:${proxy_port}..."
  "$proxy_path" "${proxy_args[@]}" >"$stdout_log" 2>"$stderr_log" &
  proxy_pid="$!"
  proxy_started=true

  local attempt
  for ((attempt = 1; attempt <= startup_timeout_seconds; attempt++)); do
    if tcp_open "$proxy_address" "$proxy_port"; then
      echo "Cloud SQL Auth Proxy is ready. PID: $proxy_pid"
      return 0
    fi
    sleep 1
  done

  local tail_text=""
  if [[ -f "$stderr_log" ]]; then
    tail_text="$(tail -n 20 "$stderr_log" || true)"
  fi
  die "Cloud SQL Auth Proxy did not become ready within ${startup_timeout_seconds} seconds. ${tail_text}"
}

cleanup() {
  if [[ "$stop_proxy_on_exit" == true && "$proxy_started" == true && -n "$proxy_pid" ]]; then
    echo "Stopping Cloud SQL Auth Proxy started by this script..."
    kill "$proxy_pid" >/dev/null 2>&1 || true
  fi
}

set_local_database_env() {
  clear_db_env
  if [[ -n "$local_db_name" ]]; then
    export DB_ENGINE="sqlite"
    export DB_NAME="$local_db_name"
    echo "RetailOps database environment configured for local SQLite at '$local_db_name'."
  else
    echo "RetailOps database environment configured for default local SQLite."
  fi
}

set_cloud_database_env() {
  unset DATABASE_URL
  local db_password
  db_password="$(gcloud_secret_access)"
  export DB_ENGINE="postgres"
  export DB_NAME="$db_name"
  export DB_USER="$db_user"
  export DB_PASSWORD="$db_password"
  export DB_HOST="$proxy_address"
  export DB_PORT="$proxy_port"
  export DB_SSLMODE="disable"
  export DB_CONN_MAX_AGE="60"
  echo "RetailOps database environment configured for Cloud SQL."
}

set_postgres_database_env() {
  [[ -n "$postgres_db_name" ]] || die "PostgreSQL mode requires --postgres-db-name."
  [[ -n "$postgres_user" ]] || die "PostgreSQL mode requires --postgres-user."
  [[ -n "$postgres_host" ]] || die "PostgreSQL mode requires --postgres-host."

  if [[ -z "$postgres_password" ]]; then
    postgres_password="${RETAILOPS_POSTGRES_PASSWORD:-${DB_PASSWORD:-}}"
  fi
  [[ -n "$postgres_password" ]] || die "PostgreSQL mode requires --postgres-password, RETAILOPS_POSTGRES_PASSWORD, or DB_PASSWORD."

  unset DATABASE_URL
  export DB_ENGINE="postgres"
  export DB_NAME="$postgres_db_name"
  export DB_USER="$postgres_user"
  export DB_PASSWORD="$postgres_password"
  export DB_HOST="$postgres_host"
  export DB_PORT="$postgres_port"
  export DB_SSLMODE="$postgres_sslmode"
  export DB_CONN_MAX_AGE="60"
  echo "RetailOps database environment configured for local PostgreSQL at ${postgres_host}:${postgres_port}."
}

set_local_storage_env() {
  clear_media_env
  if [[ -n "$media_root" ]]; then
    export MEDIA_ROOT="$media_root"
  else
    unset MEDIA_ROOT
  fi
  if [[ -n "$media_url" ]]; then
    export MEDIA_URL="$media_url"
  else
    unset MEDIA_URL
  fi
  echo "RetailOps media storage configured for local filesystem."
}

set_cloud_storage_env() {
  [[ -n "$media_public_bucket_name" ]] || die "Cloud storage mode requires --media-public-bucket-name."
  [[ -n "$media_private_bucket_name" ]] || die "Cloud storage mode requires --media-private-bucket-name."

  clear_media_env
  export MEDIA_STORAGE_BACKEND="gcs"
  export MEDIA_GCS_PROJECT_ID="$media_project_id"
  export MEDIA_GCS_PUBLIC_BUCKET_NAME="$media_public_bucket_name"
  export MEDIA_GCS_PRIVATE_BUCKET_NAME="$media_private_bucket_name"
  export MEDIA_GCS_PRODUCT_PUBLIC="true"
  export MEDIA_GCS_RECEIPT_SIGNED_URLS="true"
  export MEDIA_GCS_SIGNED_URL_EXPIRATION="900"

  [[ -n "$media_public_custom_endpoint" ]] && export MEDIA_GCS_PUBLIC_CUSTOM_ENDPOINT="$media_public_custom_endpoint"
  [[ "$media_use_iam_sign_blob" == true ]] && export MEDIA_GCS_IAM_SIGN_BLOB="true"
  [[ -n "$media_signer_service_account" ]] && export MEDIA_GCS_SERVICE_ACCOUNT_EMAIL="$media_signer_service_account"

  if [[ -n "$media_credentials_file" && -f "$media_credentials_file" ]]; then
    export GOOGLE_APPLICATION_CREDENTIALS="$media_credentials_file"
    echo "Google Cloud Storage credentials configured from '$media_credentials_file'."
  else
    echo "Media credentials file not found; Google Cloud Storage will use application default credentials."
  fi
  echo "RetailOps media storage configured for Google Cloud Storage."
}

resolve_s3_endpoint_url() {
  if [[ -n "$s3_endpoint_url" ]]; then
    printf '%s' "$s3_endpoint_url"
    return 0
  fi
  case "$s3_provider" in
    rustfs) printf '%s' "http://127.0.0.1:9000" ;;
    garage) printf '%s' "http://127.0.0.1:3900" ;;
    custom) die "s3-provider=custom requires --s3-endpoint-url." ;;
    *) die "--s3-provider must be one of: rustfs garage custom" ;;
  esac
}

set_s3_storage_env() {
  if [[ -z "$s3_access_key_id" ]]; then
    s3_access_key_id="${RETAILOPS_S3_ACCESS_KEY_ID:-${MEDIA_S3_ACCESS_KEY_ID:-}}"
  fi
  if [[ -z "$s3_secret_access_key" ]]; then
    s3_secret_access_key="${RETAILOPS_S3_SECRET_ACCESS_KEY:-${MEDIA_S3_SECRET_ACCESS_KEY:-}}"
  fi

  [[ -n "$s3_access_key_id" && -n "$s3_secret_access_key" ]] || die "S3 storage mode requires --s3-access-key-id/--s3-secret-access-key or RETAILOPS_S3_ACCESS_KEY_ID/RETAILOPS_S3_SECRET_ACCESS_KEY."
  [[ -n "$s3_public_bucket_name" ]] || die "S3 storage mode requires --s3-public-bucket-name."
  [[ -n "$s3_private_bucket_name" ]] || die "S3 storage mode requires --s3-private-bucket-name."

  local resolved_endpoint
  resolved_endpoint="$(resolve_s3_endpoint_url)"

  clear_media_env
  export MEDIA_STORAGE_BACKEND="s3"
  export MEDIA_S3_ENDPOINT_URL="$resolved_endpoint"
  export MEDIA_S3_ACCESS_KEY_ID="$s3_access_key_id"
  export MEDIA_S3_SECRET_ACCESS_KEY="$s3_secret_access_key"
  export MEDIA_S3_REGION_NAME="$s3_region_name"
  export MEDIA_S3_PUBLIC_BUCKET_NAME="$s3_public_bucket_name"
  export MEDIA_S3_PRIVATE_BUCKET_NAME="$s3_private_bucket_name"
  export MEDIA_S3_PRODUCT_PUBLIC="$s3_product_public"
  export MEDIA_S3_RECEIPT_SIGNED_URLS="$s3_receipt_signed_urls"
  export MEDIA_S3_SIGNED_URL_EXPIRATION="900"
  export MEDIA_S3_ADDRESSING_STYLE="path"
  export MEDIA_S3_SIGNATURE_VERSION="s3v4"
  export MEDIA_S3_FILE_OVERWRITE="false"
  export MEDIA_S3_PRODUCT_CACHE_CONTROL="public, max-age=31536000, immutable"
  export MEDIA_S3_RECEIPT_CACHE_CONTROL="private, max-age=0, no-store"
  echo "RetailOps media storage configured for S3-compatible provider '$s3_provider' at '$resolved_endpoint'."
}

manage_py() {
  "$python_command" manage.py "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db-mode) db_mode="${2:?Missing value for --db-mode}"; shift 2 ;;
    --storage-mode) storage_mode="${2:?Missing value for --storage-mode}"; shift 2 ;;
    --port) port="${2:?Missing value for --port}"; shift 2 ;;
    --proxy-port) proxy_port="${2:?Missing value for --proxy-port}"; shift 2 ;;
    --apply-migrations) apply_migrations=true; shift ;;
    --no-runserver) no_runserver=true; shift ;;
    --stop-proxy-on-exit) stop_proxy_on_exit=true; shift ;;
    --project-id) project_id="${2:?Missing value for --project-id}"; shift 2 ;;
    --instance-connection-name) instance_connection_name="${2:?Missing value for --instance-connection-name}"; shift 2 ;;
    --secret-name) secret_name="${2:?Missing value for --secret-name}"; shift 2 ;;
    --db-name) db_name="${2:?Missing value for --db-name}"; shift 2 ;;
    --db-user) db_user="${2:?Missing value for --db-user}"; shift 2 ;;
    --local-db-name) local_db_name="${2:?Missing value for --local-db-name}"; shift 2 ;;
    --postgres-host) postgres_host="${2:?Missing value for --postgres-host}"; shift 2 ;;
    --postgres-port) postgres_port="${2:?Missing value for --postgres-port}"; shift 2 ;;
    --postgres-db-name) postgres_db_name="${2:?Missing value for --postgres-db-name}"; shift 2 ;;
    --postgres-user) postgres_user="${2:?Missing value for --postgres-user}"; shift 2 ;;
    --postgres-password) postgres_password="${2:?Missing value for --postgres-password}"; shift 2 ;;
    --postgres-sslmode) postgres_sslmode="${2:?Missing value for --postgres-sslmode}"; shift 2 ;;
    --proxy-address) proxy_address="${2:?Missing value for --proxy-address}"; shift 2 ;;
    --django-address) django_address="${2:?Missing value for --django-address}"; shift 2 ;;
    --proxy-path) proxy_path="${2:?Missing value for --proxy-path}"; shift 2 ;;
    --credentials-file) credentials_file="${2:?Missing value for --credentials-file}"; shift 2 ;;
    --python-command) python_command="${2:?Missing value for --python-command}"; shift 2 ;;
    --startup-timeout-seconds) startup_timeout_seconds="${2:?Missing value for --startup-timeout-seconds}"; shift 2 ;;
    --media-project-id) media_project_id="${2:?Missing value for --media-project-id}"; shift 2 ;;
    --media-public-bucket-name) media_public_bucket_name="${2:?Missing value for --media-public-bucket-name}"; shift 2 ;;
    --media-private-bucket-name) media_private_bucket_name="${2:?Missing value for --media-private-bucket-name}"; shift 2 ;;
    --media-credentials-file) media_credentials_file="${2:?Missing value for --media-credentials-file}"; shift 2 ;;
    --media-use-iam-sign-blob) media_use_iam_sign_blob=true; shift ;;
    --media-signer-service-account) media_signer_service_account="${2:?Missing value for --media-signer-service-account}"; shift 2 ;;
    --media-public-custom-endpoint) media_public_custom_endpoint="${2:?Missing value for --media-public-custom-endpoint}"; shift 2 ;;
    --media-root) media_root="${2:?Missing value for --media-root}"; shift 2 ;;
    --media-url) media_url="${2:?Missing value for --media-url}"; shift 2 ;;
    --s3-provider) s3_provider="${2:?Missing value for --s3-provider}"; shift 2 ;;
    --s3-endpoint-url) s3_endpoint_url="${2:?Missing value for --s3-endpoint-url}"; shift 2 ;;
    --s3-access-key-id) s3_access_key_id="${2:?Missing value for --s3-access-key-id}"; shift 2 ;;
    --s3-secret-access-key) s3_secret_access_key="${2:?Missing value for --s3-secret-access-key}"; shift 2 ;;
    --s3-public-bucket-name) s3_public_bucket_name="${2:?Missing value for --s3-public-bucket-name}"; shift 2 ;;
    --s3-private-bucket-name) s3_private_bucket_name="${2:?Missing value for --s3-private-bucket-name}"; shift 2 ;;
    --s3-region-name) s3_region_name="${2:?Missing value for --s3-region-name}"; shift 2 ;;
    --s3-product-public) s3_product_public=true; shift ;;
    --no-s3-product-public) s3_product_public=false; shift ;;
    --s3-receipt-signed-urls) s3_receipt_signed_urls=true; shift ;;
    --no-s3-receipt-signed-urls) s3_receipt_signed_urls=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

validate_choice "--db-mode" "$db_mode" local postgres cloud
validate_choice "--storage-mode" "$storage_mode" local s3 cloud
validate_choice "--s3-provider" "$s3_provider" rustfs garage custom
require_command "$python_command"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_root"

trap cleanup EXIT

case "$db_mode" in
  cloud)
    start_cloud_sql_proxy_if_needed
    set_cloud_database_env
    ;;
  postgres)
    set_postgres_database_env
    ;;
  local)
    set_local_database_env
    ;;
esac

case "$storage_mode" in
  cloud) set_cloud_storage_env ;;
  s3) set_s3_storage_env ;;
  local) set_local_storage_env ;;
esac

echo "RetailOps startup profile: DB=$db_mode, storage=$storage_mode."

if [[ "$apply_migrations" == true ]]; then
  echo "Applying migrations..."
  manage_py migrate
else
  echo "Checking migrations..."
  if ! manage_py migrate --check; then
    die "Pending migrations detected or migration check failed. Re-run with --apply-migrations after confirming the selected DB profile is correct."
  fi
fi

echo "Running Django system check..."
manage_py check

if [[ "$no_runserver" == true ]]; then
  echo "Startup validation completed. Runserver was skipped because --no-runserver was set."
  exit 0
fi

echo "Starting RetailOps at http://${django_address}:${port}/ with DB=$db_mode and storage=$storage_mode..."
manage_py runserver "${django_address}:${port}"
