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

### Showing the customer where to pay

For Mobile Payment and Bank Transfer the customer sends money before checkout,
so the Kiosk has to display a destination. Fetch it rather than hardcoding it:

```
GET /api/v1/kiosk/recipient-profiles/
Authorization: KioskKey <station key>
```

```json
{
  "results": [
    {
      "payment_method": "mobile_payment",
      "payment_method_display": "Mobile Payment",
      "bank": "Banco de Venezuela",
      "phone": "04141234567",
      "account_number": "",
      "document_id": "V-12345678"
    }
  ]
}
```

One entry per payment method at most — whichever profile is marked **primary**
in *Settings → Manage Recipient Profiles* (see [is_primary](API_GUIDE.md#412-recipient-profiles)).
`phone` is populated for Mobile Payment and `account_number` for Bank Transfer;
the unused one is `""`. Inactive profiles are never returned, so a method whose
profile has been deactivated simply disappears from the list.

`results` can be empty — nothing is configured yet, or every profile for a
method is inactive. Treat that as "this payment method is unavailable" rather
than falling back to a built-in destination.

Fetch once when the payment screen opens and cache it for the transaction; this
endpoint is rate-limited per station (`kiosk_scan`, 120/min) and is not meant to
be polled.

Using this endpoint is what keeps the displayed destination and the allowlist in
the next section in step. A destination hardcoded in the Kiosk can drift from
the configured profiles, and the customer only finds out at checkout — after
the money has already been sent.

### Recipient profile validation (optional)

With OCR enabled, RetailOps can additionally verify that a receipt's OCR-detected
*recipient* details match one of your own configured recipient profiles
(**Settings → Manage Recipient Profiles**). This catches receipts paid into a
different account than yours. The identifying field checked depends on the
payment method: recipient **phone** for Mobile Payment, recipient **bank
account number** for Bank Transfer — plus bank and document/ID number in both
cases. When enabled and no profile matches, `POST /api/v1/kiosk/checkout/`
returns:

```json
{
  "error": "Receipt recipient details do not match any known-good account.",
  "code": "recipient_mismatch",
  "details": {
    "receipt_fields": {"phone": "...", "bank": "...", "document_id": "..."},
    "checked_profiles_count": 1
  }
}
```

(for a Bank Transfer checkout, `receipt_fields` has `account_number` in place
of `phone`.) HTTP status is `422`. This check is off by default and requires
at least one active recipient profile before it can be turned on.

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
- `recipient_mismatch`: the receipt's OCR-detected recipient doesn't match any
  active recipient profile for that payment method — check **Settings → Manage
  Recipient Profiles**, or disable recipient validation if not needed.
