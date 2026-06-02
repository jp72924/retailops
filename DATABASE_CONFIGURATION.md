# RetailOps Database Configuration

RetailOps uses SQLite by default for local development. The default database is
`db.sqlite3` in the project root.

For a centralized database shared by multiple devices, run one Django backend
that points to one PostgreSQL database. Kiosks, MCP clients, and the CLI should
connect to the Django API, not directly to the database.

Django reads these settings from the process environment. It does not auto-load
the project `.env` file for `DJANGO_*`, `DATABASE_URL`, or `DB_*` variables.
Install dependencies before using PostgreSQL:

```bash
pip install -r requirements.txt
```

## Profiles

### Local SQLite

No database environment variables are required.

```bash
python manage.py migrate
python manage.py runserver
```

Optional custom SQLite file:

```bash
export DB_ENGINE=sqlite
export DB_NAME=local-retailops.sqlite3
python manage.py migrate
```

Relative SQLite paths are resolved from the project root. Absolute paths are
used as-is.

### PostgreSQL With DATABASE_URL

`DATABASE_URL` is the preferred production and remote-database setting.

```bash
export DATABASE_URL="postgresql://retailops_user:secret@db.example.com:5432/retailops?sslmode=require"
export DJANGO_SECRET_KEY="<strong-secret>"
export DJANGO_DEBUG=False
export DJANGO_ALLOWED_HOSTS=retailops.example.com
python manage.py migrate
python manage.py runserver
```

`DB_CONN_MAX_AGE` can be set to tune persistent connection lifetime. It defaults
to `60` seconds.

### PostgreSQL With Separate Variables

Use this profile when a hosting provider injects credentials separately.

```bash
export DB_ENGINE=postgres
export DB_NAME=retailops
export DB_USER=retailops_user
export DB_PASSWORD=secret
export DB_HOST=db.example.com
export DB_PORT=5432
export DB_SSLMODE=require
export DJANGO_SECRET_KEY="<strong-secret>"
export DJANGO_DEBUG=False
export DJANGO_ALLOWED_HOSTS=retailops.example.com
python manage.py migrate
```

`DB_ENGINE=postgres` requires `DB_NAME`, `DB_USER`, `DB_PASSWORD`, and
`DB_HOST`. `DB_PORT` defaults to `5432`.

### Local PostgreSQL Service

Use this profile for a bare-metal or LAN-hosted PostgreSQL service that replaces
SQLite without using Cloud SQL. PostgreSQL 16 is the recommended local version.

Create the database and app user outside the repo, then start RetailOps with the
general helper:

```bash
export RETAILOPS_POSTGRES_PASSWORD="<local-postgres-password>"
./scripts/start-retailops.sh \
  --db-mode postgres \
  --postgres-host 127.0.0.1 \
  --postgres-port 5432 \
  --postgres-db-name retailops \
  --postgres-user retailops_app
```

You can pass `--postgres-password` instead of `RETAILOPS_POSTGRES_PASSWORD`.
The helper injects `DB_ENGINE=postgres`, `DB_SSLMODE=disable`, and
`DB_CONN_MAX_AGE=60` only into the Django process it starts.

### Local Startup With Selectable Infrastructure

Use the general Bash helper to choose local or cloud infrastructure per layer.
The default is fully local: SQLite plus local `media/`.

```bash
./scripts/start-retailops.sh
```

Available combinations:

| Command | Database | Storage |
| --- | --- | --- |
| `./scripts/start-retailops.sh` | local SQLite | local `media/` |
| `./scripts/start-retailops.sh --db-mode postgres` | local PostgreSQL | local `media/` |
| `./scripts/start-retailops.sh --storage-mode s3` | local SQLite | S3-compatible storage |
| `./scripts/start-retailops.sh --db-mode postgres --storage-mode s3` | local PostgreSQL | S3-compatible storage |
| `./scripts/start-retailops.sh --db-mode cloud` | Cloud SQL | local `media/` |
| `./scripts/start-retailops.sh --storage-mode cloud` | local SQLite | Google Cloud Storage |
| `./scripts/start-retailops.sh --db-mode cloud --storage-mode cloud` | Cloud SQL | Google Cloud Storage |

When `--db-mode cloud` is selected, the script starts or reuses Cloud SQL Auth
Proxy on `127.0.0.1:5433`, reads the database password from Secret Manager, and
injects PostgreSQL `DB_*` variables only into the current process.

When `--db-mode postgres` is selected, the script connects directly to a local or
LAN PostgreSQL service. It never starts, stops, or installs PostgreSQL.

When `--storage-mode cloud` is selected, the script injects `MEDIA_*` variables
for Google Cloud Storage only into the current process. See
`MEDIA_STORAGE_CONFIGURATION.md` for bucket and credential details.

When `--storage-mode s3` is selected, the script injects `MEDIA_S3_*` variables
for RustFS, Garage, or any S3-compatible endpoint. See
`MEDIA_STORAGE_CONFIGURATION.md` for endpoint and bucket provisioning details.

Useful options:

```bash
# Validate configuration without starting server.
./scripts/start-retailops.sh --no-runserver

# Apply pending migrations before starting server.
./scripts/start-retailops.sh --apply-migrations

# Use Cloud SQL and another Django or proxy port.
./scripts/start-retailops.sh \
  --db-mode cloud \
  --project-id "<gcp-project-id>" \
  --instance-connection-name "<gcp-project-id>:<region>:<instance-name>" \
  --secret-name "<db-password-secret-name>" \
  --port 8010 \
  --proxy-port 5434

# Use local PostgreSQL and S3-compatible media storage.
export RETAILOPS_POSTGRES_PASSWORD="<local-postgres-password>"
export RETAILOPS_S3_ACCESS_KEY_ID="<s3-access-key>"
export RETAILOPS_S3_SECRET_ACCESS_KEY="<s3-secret-key>"
./scripts/start-retailops.sh --db-mode postgres --storage-mode s3 --s3-provider rustfs

# Stop the proxy on exit if this script started it.
./scripts/start-retailops.sh --db-mode cloud --stop-proxy-on-exit
```

Cloud SQL values to provide:

- GCP project: `<gcp-project-id>`
- Cloud SQL instance connection name: `<gcp-project-id>:<region>:<instance-name>`
- Secret Manager secret containing the database password: `<db-password-secret-name>`
- PostgreSQL database/user: `retailops` / `retailops_app` by convention, or your own values via `--db-name` and `--db-user`
- Proxy executable: `cloud-sql-proxy` from `PATH`, or `CLOUD_SQL_PROXY_PATH`
- Optional proxy service account key: `~/.config/retailops/cloudsql/<service-account-key>.json`

The helper may include local project defaults for a specific workstation, but
public deployments should pass the project, instance, and secret values
explicitly instead of relying on those defaults.

The Cloud SQL helper delegates to the general script:

```bash
./scripts/start-retailops-cloudsql.sh

# Same wrapper, but with Google Cloud Storage enabled.
./scripts/start-retailops-cloudsql.sh --storage-mode cloud
```

Windows / PowerShell equivalents:

```powershell
# Validate configuration without starting server.
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 -NoRunserver

# Apply pending migrations before starting server.
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 -ApplyMigrations

# Use Cloud SQL and another Django or proxy port.
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 `
  -DbMode cloud `
  -ProjectId "<gcp-project-id>" `
  -InstanceConnectionName "<gcp-project-id>:<region>:<instance-name>" `
  -SecretName "<db-password-secret-name>" `
  -Port 8010 `
  -ProxyPort 5434

# Use local PostgreSQL and S3-compatible media storage.
$env:RETAILOPS_POSTGRES_PASSWORD = "<local-postgres-password>"
$env:RETAILOPS_S3_ACCESS_KEY_ID = "<s3-access-key>"
$env:RETAILOPS_S3_SECRET_ACCESS_KEY = "<s3-secret-key>"
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 -DbMode postgres -StorageMode s3 -S3Provider rustfs

# Stop the proxy on exit if this script started it.
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 -DbMode cloud -StopProxyOnExit
```

Windows Cloud SQL defaults use `%LOCALAPPDATA%\Programs\cloud-sql-proxy\cloud-sql-proxy.exe`
for the proxy and `%APPDATA%\RetailOps\cloudsql\retailops-cloudsql-client.json`
for the optional service account key. The Windows Cloud SQL wrapper is also
available:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops-cloudsql.ps1

# Same wrapper, but with Google Cloud Storage enabled.
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops-cloudsql.ps1 -StorageMode cloud
```

Troubleshooting:

- If the proxy executable is missing, reinstall Cloud SQL Auth Proxy at the
  default path or pass `--proxy-path` with `--db-mode cloud`.
- If Secret Manager access fails, confirm `gcloud auth list` and project access.
- If `127.0.0.1:5433` is occupied by another service, pass another `--proxy-port`.
- If local PostgreSQL authentication fails, confirm `--postgres-host`,
  `--postgres-port`, user/database names, and `RETAILOPS_POSTGRES_PASSWORD`.
- Running `python manage.py runserver` directly still uses SQLite unless `DB_*`
  variables are set in that shell.

## SQLite To PostgreSQL Migration

1. Stop all Django processes that can write to `db.sqlite3`.
2. Back up the current SQLite database.

```bash
mkdir -p db_backups
cp db.sqlite3 "db_backups/db.sqlite3.$(date +%Y%m%d-%H%M%S).bak"
```

3. Export portable data from SQLite.

```bash
python manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --output retailops-data.json
```

4. Configure PostgreSQL using `DATABASE_URL`, the separate `DB_*` variables, or
   `./scripts/start-retailops.sh --db-mode postgres`.
5. Build the PostgreSQL schema.

```bash
python manage.py migrate
```

6. Load the exported data.

```bash
python manage.py loaddata retailops-data.json
```

7. Verify core records.

```bash
python manage.py check
python manage.py shell
```

In the shell, verify users, roles, products, customers, orders, payments,
inventory movements, `SystemSettings`, `SequenceCounter`, and `KioskStation`.

8. Reprovision kiosks only if their migrated API keys do not authenticate.

```bash
python manage.py provision_kiosk --store DEV-LOCAL --station 1 --by admin@retailops.local
```

Windows backup equivalent:

```powershell
New-Item -ItemType Directory -Force .\db_backups | Out-Null
Copy-Item .\db.sqlite3 ".\db_backups\db.sqlite3.$(Get-Date -Format yyyyMMdd-HHmmss).bak"
```

## Centralized Multi-Device Setup

- Run a single Django backend that points to the centralized PostgreSQL
  database.
- Set every external RetailOps Kiosk deployment `BASE_URL` to that backend URL.
- Set MCP and CLI clients to the same API URL with `RETAILOPS_BASE_URL`.
- Do not put `db.sqlite3` on a shared network drive for multi-device use.
- If multiple Django servers serve the same app, configure shared media storage
  instead of local `MEDIA_ROOT`; see `MEDIA_STORAGE_CONFIGURATION.md`.
- For bare-metal deployments, put PostgreSQL and S3-compatible storage on
  reliable disks outside the repo and back up both layers.
- Configure PostgreSQL backups and monitor receipt-image retention.

## Verification Checklist

- `python manage.py check`
- `python manage.py migrate`
- `python manage.py seed --force` on a clean database
- `python manage.py provision_kiosk --store DEV-LOCAL --station 1 --by admin@retailops.local`
- Kiosk checkout with card, mobile payment, and bank transfer
- Payment receipt OCR JSON read/write
- Duplicate receipt `transaction_key` protection
- Concurrent order/payment number creation through `SequenceCounter`
- Inventory checkout with low stock
