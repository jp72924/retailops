#!/usr/bin/env bash
set -Eeuo pipefail

skip_install=false
seed=false
force_seed=false
provision_kiosk=false
reset_passwords=false
create_venv=false
demo=false
operational=false
yes=false
no_input=false
python_command="${PYTHON:-python3}"
store="DEV-LOCAL"
station="1"
kiosk_label="Local development kiosk"
station_count="0"
admin_email=""
admin_first_name="Store"
admin_last_name="Owner"
admin_password_env="RETAILOPS_INITIAL_ADMIN_PASSWORD"

usage() {
  cat <<'EOF'
Usage: scripts/setup-retailops-local.sh [options]

Options:
  --skip-install          Skip pip upgrade and requirements install.
  --demo                  Use documented demo users and optional sample data.
  --seed                  Load sample RetailOps data. Requires --demo.
  --force-seed            Clear and reload sample RetailOps data. Requires --demo.
  --provision-kiosk       Provision a demo Kiosk station. Requires --demo.
  --reset-passwords       Reset demo user passwords to documented defaults.
  --create-venv           Create/use .venv automatically.
  --operational           Accepted for compatibility; operational is default.
  --yes                   Confirm init without an interactive prompt.
  --no-input              Disable interactive prompts for init.
  --admin-email <email>   First operational admin email.
  --admin-first-name <n>  First operational admin first name.
  --admin-last-name <n>   First operational admin last name.
  --admin-password-env <n>
                          Environment variable with the initial admin password.
  --python <command>      Python command to use. Default: $PYTHON or python3.
  --store <id>            Kiosk store identifier. Default: DEV-LOCAL.
  --station <number>      Kiosk station number. Default: 1.
  --station-count <n>     Number of Kiosk stations for operational setup.
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
    --demo) demo=true; shift ;;
    --seed) seed=true; shift ;;
    --force-seed) force_seed=true; shift ;;
    --provision-kiosk) provision_kiosk=true; shift ;;
    --reset-passwords) reset_passwords=true; shift ;;
    --create-venv) create_venv=true; shift ;;
    --operational) operational=true; shift ;;
    --yes) yes=true; shift ;;
    --no-input) no_input=true; shift ;;
    --admin-email) admin_email="${2:?Missing value for --admin-email}"; shift 2 ;;
    --admin-first-name) admin_first_name="${2:?Missing value for --admin-first-name}"; shift 2 ;;
    --admin-last-name) admin_last_name="${2:?Missing value for --admin-last-name}"; shift 2 ;;
    --admin-password-env) admin_password_env="${2:?Missing value for --admin-password-env}"; shift 2 ;;
    --python) python_command="${2:?Missing value for --python}"; shift 2 ;;
    --store) store="${2:?Missing value for --store}"; shift 2 ;;
    --station) station="${2:?Missing value for --station}"; shift 2 ;;
    --station-count) station_count="${2:?Missing value for --station-count}"; shift 2 ;;
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
if [[ ! "$station_count" =~ ^[0-9]+$ ]]; then
  die "--station-count must be a non-negative integer."
fi

if [[ "$skip_install" == false ]]; then
  "$python_command" -m pip install --upgrade pip
  "$python_command" -m pip install -r requirements.txt
fi

"$python_command" manage.py migrate

init_args=(manage.py init)

if [[ "$demo" == true ]]; then
  init_args+=(--demo)
  [[ "$seed" == true ]] && init_args+=(--seed)
  [[ "$force_seed" == true ]] && init_args+=(--force-seed)
  [[ "$provision_kiosk" == true ]] && init_args+=(--provision-kiosk)
  [[ "$reset_passwords" == true ]] && init_args+=(--reset-passwords)
  init_args+=(--store "$store" --station "$station" --kiosk-label "$kiosk_label")
  "$python_command" "${init_args[@]}"
else
  if [[ "$seed" == true || "$force_seed" == true || "$reset_passwords" == true ]]; then
    die "--seed, --force-seed, and --reset-passwords require --demo."
  fi
  if [[ "$provision_kiosk" == true ]]; then
    die "--provision-kiosk requires --demo. For operational setup, use --station-count."
  fi
  [[ -n "$admin_email" ]] && init_args+=(--admin-email "$admin_email")
  [[ -n "$admin_first_name" ]] && init_args+=(--admin-first-name "$admin_first_name")
  [[ -n "$admin_last_name" ]] && init_args+=(--admin-last-name "$admin_last_name")
  [[ -n "$admin_password_env" ]] && init_args+=(--admin-password-env "$admin_password_env")
  [[ "$yes" == true ]] && init_args+=(--yes)
  [[ "$no_input" == true ]] && init_args+=(--no-input)
  if [[ "$station_count" != "0" ]]; then
    init_args+=(--store "$store" --station-start "$station" --station-count "$station_count" --kiosk-label-prefix "$kiosk_label")
  fi
  "$python_command" "${init_args[@]}"
fi

echo
echo "RetailOps local backend is ready."
echo "Start it with:"
echo "  $python_command manage.py runserver"
