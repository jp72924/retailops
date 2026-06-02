#!/usr/bin/env bash
set -Eeuo pipefail

s3_provider="rustfs"
s3_endpoint_url=""
s3_access_key_id=""
s3_secret_access_key=""
s3_public_bucket_name="retailops-public-assets"
s3_private_bucket_name="retailops-private-documents"
s3_region_name="us-east-1"
s3_product_public=true
policy_file=""

usage() {
  cat <<'EOF'
Usage: scripts/provision-retailops-local-s3.sh [options]

Provision the recommended RetailOps two-bucket layout on an existing
S3-compatible endpoint such as RustFS or Garage.

Options:
  --s3-provider rustfs|garage|custom
  --s3-endpoint-url <url>
  --s3-access-key-id <key>
  --s3-secret-access-key <secret>
  --s3-public-bucket-name <name>
  --s3-private-bucket-name <name>
  --s3-region-name <region>
  --s3-product-public
  --no-s3-product-public
  -h, --help

Credentials can also be supplied through:
  RETAILOPS_S3_ACCESS_KEY_ID / RETAILOPS_S3_SECRET_ACCESS_KEY
  MEDIA_S3_ACCESS_KEY_ID / MEDIA_S3_SECRET_ACCESS_KEY
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$policy_file" && -f "$policy_file" ]]; then
    rm -f "$policy_file"
  fi
}

trap cleanup EXIT

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    die "Required command not found: $command_name"
  fi
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

aws_call() {
  aws "$@" --no-cli-pager
}

ensure_bucket() {
  local endpoint_url="$1"
  local bucket_name="$2"

  if aws_call --endpoint-url "$endpoint_url" s3api head-bucket --bucket "$bucket_name" >/dev/null 2>&1; then
    echo "Bucket '$bucket_name' already exists."
    return 0
  fi

  echo "Creating bucket '$bucket_name'..."
  aws_call \
    --endpoint-url "$endpoint_url" \
    s3api create-bucket \
    --bucket "$bucket_name"
}

set_public_read_policy() {
  local endpoint_url="$1"
  local bucket_name="$2"

  policy_file="$(mktemp "${TMPDIR:-/tmp}/retailops-s3-public-policy.XXXXXX.json")"
  cat >"$policy_file" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "RetailOpsPublicProductRead",
      "Effect": "Allow",
      "Principal": "*",
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::$bucket_name/*"]
    }
  ]
}
EOF

  echo "Applying public read policy to '$bucket_name'..."
  aws_call \
    --endpoint-url "$endpoint_url" \
    s3api put-bucket-policy \
    --bucket "$bucket_name" \
    --policy "file://$policy_file"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --s3-provider) s3_provider="${2:?Missing value for --s3-provider}"; shift 2 ;;
    --s3-endpoint-url) s3_endpoint_url="${2:?Missing value for --s3-endpoint-url}"; shift 2 ;;
    --s3-access-key-id) s3_access_key_id="${2:?Missing value for --s3-access-key-id}"; shift 2 ;;
    --s3-secret-access-key) s3_secret_access_key="${2:?Missing value for --s3-secret-access-key}"; shift 2 ;;
    --s3-public-bucket-name) s3_public_bucket_name="${2:?Missing value for --s3-public-bucket-name}"; shift 2 ;;
    --s3-private-bucket-name) s3_private_bucket_name="${2:?Missing value for --s3-private-bucket-name}"; shift 2 ;;
    --s3-region-name) s3_region_name="${2:?Missing value for --s3-region-name}"; shift 2 ;;
    --s3-product-public) s3_product_public=true; shift ;;
    --no-s3-product-public) s3_product_public=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

require_command aws

if [[ -z "$s3_access_key_id" ]]; then
  s3_access_key_id="${RETAILOPS_S3_ACCESS_KEY_ID:-${MEDIA_S3_ACCESS_KEY_ID:-}}"
fi
if [[ -z "$s3_secret_access_key" ]]; then
  s3_secret_access_key="${RETAILOPS_S3_SECRET_ACCESS_KEY:-${MEDIA_S3_SECRET_ACCESS_KEY:-}}"
fi

[[ -n "$s3_access_key_id" && -n "$s3_secret_access_key" ]] || die "Provisioning requires --s3-access-key-id/--s3-secret-access-key or RETAILOPS_S3_ACCESS_KEY_ID/RETAILOPS_S3_SECRET_ACCESS_KEY."
[[ -n "$s3_public_bucket_name" ]] || die "Provisioning requires --s3-public-bucket-name."
[[ -n "$s3_private_bucket_name" ]] || die "Provisioning requires --s3-private-bucket-name."

endpoint_url="$(resolve_s3_endpoint_url)"

previous_access_key="${AWS_ACCESS_KEY_ID-}"
previous_secret_key="${AWS_SECRET_ACCESS_KEY-}"
previous_region="${AWS_DEFAULT_REGION-}"
previous_access_key_set=false
previous_secret_key_set=false
previous_region_set=false
[[ -v AWS_ACCESS_KEY_ID ]] && previous_access_key_set=true
[[ -v AWS_SECRET_ACCESS_KEY ]] && previous_secret_key_set=true
[[ -v AWS_DEFAULT_REGION ]] && previous_region_set=true

restore_aws_env() {
  if [[ "$previous_access_key_set" == true ]]; then
    export AWS_ACCESS_KEY_ID="$previous_access_key"
  else
    unset AWS_ACCESS_KEY_ID
  fi
  if [[ "$previous_secret_key_set" == true ]]; then
    export AWS_SECRET_ACCESS_KEY="$previous_secret_key"
  else
    unset AWS_SECRET_ACCESS_KEY
  fi
  if [[ "$previous_region_set" == true ]]; then
    export AWS_DEFAULT_REGION="$previous_region"
  else
    unset AWS_DEFAULT_REGION
  fi
  cleanup
}

trap restore_aws_env EXIT

export AWS_ACCESS_KEY_ID="$s3_access_key_id"
export AWS_SECRET_ACCESS_KEY="$s3_secret_access_key"
export AWS_DEFAULT_REGION="$s3_region_name"

ensure_bucket "$endpoint_url" "$s3_public_bucket_name"
ensure_bucket "$endpoint_url" "$s3_private_bucket_name"

if [[ "$s3_product_public" == true ]]; then
  set_public_read_policy "$endpoint_url" "$s3_public_bucket_name"
else
  echo "Skipping public product bucket policy because s3_product_public is false."
fi

echo "RetailOps local S3 buckets are ready at '$endpoint_url'."
