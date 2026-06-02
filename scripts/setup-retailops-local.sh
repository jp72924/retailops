#!/usr/bin/env bash
set -Eeuo pipefail

skip_install=false
seed=false
force_seed=false
provision_kiosk=false
reset_passwords=false
create_venv=false
python_command="${PYTHON:-python3}"
store="DEV-LOCAL"
station="1"
kiosk_label="Local development kiosk"

usage() {
  cat <<'EOF'
Usage: scripts/setup-retailops-local.sh [options]

Options:
  --skip-install          Skip pip upgrade and requirements install.
  --seed                  Load sample RetailOps data.
  --force-seed            Clear and reload sample RetailOps data.
  --provision-kiosk       Provision an external local Kiosk station.
  --reset-passwords       Reset demo user passwords to documented defaults.
  --create-venv           Create/use .venv automatically.
  --python <command>      Python command to use. Default: $PYTHON or python3.
  --store <id>            Kiosk store identifier. Default: DEV-LOCAL.
  --station <number>      Kiosk station number. Default: 1.
  --kiosk-label <label>   Kiosk station label.
  -h, --help              Show this help.
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-install) skip_install=true; shift ;;
    --seed) seed=true; shift ;;
    --force-seed) force_seed=true; shift ;;
    --provision-kiosk) provision_kiosk=true; shift ;;
    --reset-passwords) reset_passwords=true; shift ;;
    --create-venv) create_venv=true; shift ;;
    --python) python_command="${2:?Missing value for --python}"; shift 2 ;;
    --store) store="${2:?Missing value for --store}"; shift 2 ;;
    --station) station="${2:?Missing value for --station}"; shift 2 ;;
    --kiosk-label) kiosk_label="${2:?Missing value for --kiosk-label}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_root"

if ! command -v "$python_command" >/dev/null 2>&1 && [[ ! -x "$python_command" ]]; then
  die "Python command not found: $python_command"
fi

if [[ "$create_venv" == true ]]; then
  "$python_command" -m venv .venv
  python_command="$repo_root/.venv/bin/python"
fi

if [[ ! "$station" =~ ^[0-9]+$ ]]; then
  die "--station must be a positive integer."
fi

if [[ "$skip_install" == false ]]; then
  "$python_command" -m pip install --upgrade pip
  "$python_command" -m pip install -r requirements.txt
fi

"$python_command" manage.py migrate

bootstrap_args=(manage.py bootstrap_local)
[[ "$seed" == true ]] && bootstrap_args+=(--seed)
[[ "$force_seed" == true ]] && bootstrap_args+=(--force-seed)
[[ "$provision_kiosk" == true ]] && bootstrap_args+=(--provision-kiosk)
[[ "$reset_passwords" == true ]] && bootstrap_args+=(--reset-passwords)
bootstrap_args+=(--store "$store" --station "$station" --kiosk-label "$kiosk_label")

"$python_command" "${bootstrap_args[@]}"

echo
echo "RetailOps local backend is ready."
echo "Start it with:"
echo "  $python_command manage.py runserver"
