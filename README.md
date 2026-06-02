# RetailOps

RetailOps is a Django-based retail operations system for customers,
catalog, inventory, sales orders, payments, receipts, settings, and kiosk
station APIs. It includes the back-office web app, REST API, MCP server, and
deployment/configuration helpers.

RetailOps Kiosk and RetailOps CLI are independent projects. This backend exposes
the API and station credentials they use, but their application code is not part
of this repository.

## Quick Start

Requirements:

- Python 3.12 recommended
- SQLite for the default local setup

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

On Windows, use `.\.venv\Scripts\Activate.ps1` after creating the virtual
environment, or follow the PowerShell examples in `INSTALL.md`.

Open:

- Back office: http://127.0.0.1:8000/
- Admin: http://127.0.0.1:8000/admin/
- API schema: http://127.0.0.1:8000/api/v1/schema/swagger/

Local demo accounts:

| Email | Password | Role |
| --- | --- | --- |
| `admin@retailops.local` | `AdminPassword123!` | Admin |
| `manager@retailops.local` | `ManagerPass123!` | Manager |
| `staff@retailops.local` | `StaffPass123!` | Staff |

## What Is Included

- Django back office and admin
- REST API under `/api/v1/`
- Token authentication and role-based permissions
- Kiosk station authentication endpoints under `/api/v1/kiosk/`
- OCR receipt verification and receipt image metadata
- Product image support
- Configurable SQLite/PostgreSQL database profiles
- Configurable local, Google Cloud Storage, and S3-compatible media storage
- MCP server for AI/agent integrations

Use `python manage.py provision_kiosk` or `bootstrap_local --provision-kiosk`
to create station credentials for an external Kiosk project.

## Documentation

- `INSTALL.md` - local setup and developer workflow
- `KIOSK_INTEGRATION.md` - connecting an external RetailOps Kiosk station
- `DATABASE_CONFIGURATION.md` - SQLite, PostgreSQL, and Cloud SQL profiles
- `MEDIA_STORAGE_CONFIGURATION.md` - local, GCS, and S3-compatible media storage
- `API_GUIDE.md` - REST API reference
- `MCP_GUIDE.md` - MCP server integration

RetailOps can be deployed on Linux servers, PaaS platforms, or managed cloud
environments as long as Django, PostgreSQL-compatible database settings, and
media storage are configured through environment variables.

## Tests

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```
