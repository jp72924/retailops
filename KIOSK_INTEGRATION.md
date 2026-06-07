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
python manage.py init
python manage.py runserver
```

Or use the Linux/macOS setup helper:

```bash
bash scripts/setup-retailops-local.sh --admin-email owner@example.com --store MAIN --station-count 1
python manage.py runserver
```

## Provision A Station

For a new establishment, create one or more stations during site
initialization:

```bash
python manage.py init \
  --admin-email owner@example.com \
  --store MAIN \
  --station-count 2
```

For an already initialized backend, create an individual station and copy the
API key immediately:

```bash
python manage.py provision_kiosk --store MAIN --station 1 --by owner@example.com
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

## Receipt OCR (VEPay)

Receipt OCR is **disabled by default and points at no service**. RetailOps does
not ship a hosted VEPay endpoint. To verify mobile-payment and bank-transfer
receipts, run your own VEPay instance and connect it.

VEPay is a separate open-source project:
https://github.com/jp72924/vepay-api

1. Deploy a VEPay instance you control (locally, on a VM, or on a container
   platform). Follow that project's README for build and run instructions. A
   local run typically exposes it on something like `http://127.0.0.1:8080`.
2. In RetailOps, open **System Settings → Receipt OCR** and set:
   - **Enable OCR**: on
   - **Provider**: VEPay
   - **VEPay Base URL**: the URL of your instance, e.g.
     `https://vepay.your-domain.example.com` (no trailing `/v1`; RetailOps
     appends the API paths).
   - **API Key**: the key your VEPay instance expects (sent as the `X-API-Key`
     header). Leave blank if your instance needs no key.
3. Save. RetailOps calls `<base_url>/v1/receipts/parse` for parsing and
   `<base_url>/health` (or `/healthz`) for health checks.

Until a Base URL is configured, saving with OCR enabled is rejected — the field
is required when OCR is on.

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
