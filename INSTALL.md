# RetailOps Backend Installation

This guide installs the RetailOps backend locally. The default path uses SQLite
and local media files, so no external database or object storage is required.

## Operational Minimum Setup

```bash
git clone https://github.com/jp72924/retailops.git
cd retailops
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py init
python manage.py runserver
```

`init` prompts for the first admin email and a strong password, then creates
only the operational minimum: roles, system settings, and the first admin user.
It does not create sample customers, catalog, orders, payments, or inventory.

For unattended setup, provide the password through an environment variable:

```bash
export RETAILOPS_INITIAL_ADMIN_PASSWORD="<strong-password>"
python manage.py init \
  --no-input \
  --yes \
  --admin-email owner@example.com
```

To create external Kiosk station credentials during installation:

```bash
python manage.py init \
  --admin-email owner@example.com \
  --store MAIN \
  --station-count 1
```

## Manual Developer Setup

Run each step yourself if you want more control or a demo database:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py init
python manage.py runserver
```

If you want a local demo with documented users and presentation data instead:

```bash
python manage.py init --demo --seed --provision-kiosk
```

If you want to reset the demo passwords on an existing local database:

```bash
python manage.py init --demo --reset-passwords
```

If you want to reseed sample business data:

```bash
python manage.py init --demo --force-seed
```

## Optional Setup Scripts

Linux and macOS users can run:

```bash
bash scripts/setup-retailops-local.sh
```

For unattended operational setup:

```bash
export RETAILOPS_INITIAL_ADMIN_PASSWORD="<strong-password>"
bash scripts/setup-retailops-local.sh --no-input --yes --admin-email owner@example.com
```

To let the Bash script create and use `.venv` automatically:

```bash
bash scripts/setup-retailops-local.sh --create-venv
```

To provision a custom external Kiosk station:

```bash
bash scripts/setup-retailops-local.sh \
  --admin-email owner@example.com \
  --store MAIN \
  --station-count 1 \
  --station 2 \
  --kiosk-label "Main entrance"
```

For a demo setup with sample data, explicitly opt in:

```bash
bash scripts/setup-retailops-local.sh --demo --seed --provision-kiosk
```

Windows / PowerShell equivalents:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -CreateVenv
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -AdminEmail owner@example.com -Store MAIN -StationCount 1 -Station 2 -KioskLabel "Main entrance"
```

These scripts install dependencies, apply migrations, and run
`init` in operational mode or `init --demo` in demo mode. They do not hide the
underlying Django commands; they only collect them into a repeatable setup flow.
After setup, start the backend with:

```bash
python manage.py runserver
```

## External Kiosk Connection

RetailOps Kiosk is not included in this repository. To connect an external
Kiosk project, configure that project with:

```text
BASE_URL=http://127.0.0.1:8000
API_PATH=/api/v1
KIOSK_API_KEY=<key printed by init or provision_kiosk>
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
