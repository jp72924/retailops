#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/start-retailops-cloudsql.sh [options]

Shortcut wrapper around scripts/start-retailops.sh that always uses
--db-mode cloud. All supported storage, Cloud SQL, media, S3, and runtime
options are forwarded to start-retailops.sh.

Common options:
  --storage-mode local|s3|cloud
  --port <port>
  --proxy-port <port>
  --apply-migrations
  --no-runserver
  --stop-proxy-on-exit
  --project-id <id>
  --instance-connection-name <name>
  --secret-name <name>
  --db-name <name>
  --db-user <user>
  --proxy-address <address>
  --django-address <address>
  --proxy-path <path-or-command>
  --credentials-file <path>
  --python-command <command>
  --startup-timeout-seconds <seconds>

Run scripts/start-retailops.sh --help for the complete option list.
EOF
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
start_script="$script_dir/start-retailops.sh"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

exec "$start_script" --db-mode cloud "$@"
