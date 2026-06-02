# RetailOps Kiosk Integration

RetailOps Kiosk is an independent frontend project. This backend provides the
station provisioning, authentication, settings, product lookup, customer lookup,
checkout, receipt, and polling APIs that an external Kiosk uses.

## Backend Requirements

1. Run migrations and create a staff/admin user.
2. Set `KIOSK_CORS_ORIGINS` to the URL where the external Kiosk is served.
3. Start the backend and make it reachable from the Kiosk device.

For local development:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_local --seed
python manage.py runserver
```

Or use the Linux/macOS setup helper:

```bash
bash scripts/setup-retailops-local.sh --seed --provision-kiosk
python manage.py runserver
```

## Provision A Station

Create a station and copy the API key immediately:

```bash
python manage.py provision_kiosk --store DEV-LOCAL --station 1 --by admin@retailops.local
```

Or during local bootstrap:

```bash
python manage.py bootstrap_local --provision-kiosk
```

The raw key is shown only once. The database stores only a hash and a short
lookup prefix.

## Configure The External Kiosk

Set the external Kiosk deployment config to:

```text
BASE_URL=http://127.0.0.1:8000
API_PATH=/api/v1
KIOSK_API_KEY=<printed station key>
```

For production, use the backend HTTPS URL:

```text
BASE_URL=https://retailops.example.com
API_PATH=/api/v1
KIOSK_API_KEY=<production station key>
```

## Important Boundaries

- The Kiosk never connects directly to PostgreSQL, SQLite, GCS, S3, or local
  media storage.
- Every Kiosk request goes through Django under `/api/v1/kiosk/`.
- Kiosk station keys are independent from normal user tokens.
- Receipt image upload and OCR validation are enforced server-side when enabled.
- Product images and signed receipt URLs are returned by the backend according
  to the configured media storage profile.

## Troubleshooting

- `401` or `403`: the station key is wrong, missing, inactive, or belongs to a
  different station.
- Browser CORS error: add the Kiosk origin to `KIOSK_CORS_ORIGINS` and restart
  the backend process.
- Station already exists: create another station number or rotate the existing
  station key from backend tooling.
- Receipt validation fails: check System Settings for OCR enablement, supported
  payment methods, receipt image requirement, and VEPay configuration.
