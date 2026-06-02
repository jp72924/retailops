# RetailOps Backend Installation

This guide installs the RetailOps backend locally. The default path uses SQLite
and local media files, so no external database or object storage is required.

## Simple Local Setup

```bash
git clone https://github.com/jp72924/retailops.git
cd retailops
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_local --seed --provision-kiosk
python manage.py runserver
```

The bootstrap command creates local demo roles, users, system settings, optional
sample business data, and an optional Kiosk station API key.

## Manual Developer Setup

Run each step yourself if you want more control:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_local
python manage.py seed
python manage.py provision_kiosk --store DEV-LOCAL --station 1 --by admin@retailops.local
python manage.py runserver
```

If you want to reset the demo passwords on an existing local database:

```bash
python manage.py bootstrap_local --reset-passwords
```

If you want to reseed sample business data:

```bash
python manage.py bootstrap_local --force-seed
```

## Optional Setup Scripts

Linux and macOS users can run:

```bash
bash scripts/setup-retailops-local.sh --seed --provision-kiosk
```

To let the Bash script create and use `.venv` automatically:

```bash
bash scripts/setup-retailops-local.sh --create-venv --seed
```

To provision a custom external Kiosk station:

```bash
bash scripts/setup-retailops-local.sh --provision-kiosk --store MAIN --station 2 --kiosk-label "Main entrance"
```

Windows / PowerShell equivalents:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -Seed -ProvisionKiosk
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -CreateVenv -Seed
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -ProvisionKiosk -Store MAIN -Station 2 -KioskLabel "Main entrance"
```

These scripts install dependencies, apply migrations, and run
`bootstrap_local`. They do not hide the underlying Django commands; they only
collect them into a repeatable local setup flow. After setup, start the backend
with:

```bash
python manage.py runserver
```

## External Kiosk Connection

RetailOps Kiosk is not included in this repository. To connect an external
Kiosk project, configure that project with:

```text
BASE_URL=http://127.0.0.1:8000
API_PATH=/api/v1
KIOSK_API_KEY=<key printed by bootstrap_local or provision_kiosk>
```

See `KIOSK_INTEGRATION.md` for the full station setup.

## Infrastructure Profiles

The default local profile needs no environment variables. For Linux/macOS
profiles, use `scripts/start-retailops.sh`:

```bash
./scripts/start-retailops.sh
./scripts/start-retailops.sh --db-mode postgres
./scripts/start-retailops.sh --storage-mode s3
./scripts/start-retailops.sh --db-mode cloud --storage-mode cloud
```

Related Linux/macOS helpers:

```bash
# Shortcut for Cloud SQL.
./scripts/start-retailops-cloudsql.sh --storage-mode local

# Provision buckets on an existing RustFS or Garage endpoint.
./scripts/provision-retailops-local-s3.sh --s3-provider rustfs
```

Windows / PowerShell equivalents:

```powershell
.\scripts\start-retailops.ps1
.\scripts\start-retailops.ps1 -DbMode postgres
.\scripts\start-retailops.ps1 -StorageMode s3
.\scripts\start-retailops.ps1 -DbMode cloud -StorageMode cloud
```

See `DATABASE_CONFIGURATION.md` and `MEDIA_STORAGE_CONFIGURATION.md`.
