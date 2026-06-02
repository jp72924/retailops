# Kiosk Integration — Architecture v2

Replaces the original shared-service-account model documented in
`KIOSK_INTEGRATION_ANALYSIS.md`. This document describes the new server-side
architecture as implemented in this branch and the client-side migration
required in `kiosk/`.

## Server-side (implemented)

### Namespace

All kiosk traffic goes through `/api/v1/kiosk/*`. That namespace is served by
`api/kiosk/` and is fully isolated from the main RetailOps API — a kiosk API
key authenticates only against those endpoints.

### Authentication

| Concern       | Value                                                          |
|---------------|----------------------------------------------------------------|
| Scheme        | `Authorization: KioskKey <raw-api-key>`                        |
| Lookup        | First 8 chars → `KioskStation.api_key_prefix` index            |
| Verification  | SHA-256 hash comparison against `KioskStation.api_key_hash`    |
| Principal     | `request.user = station.service_user`, `request.auth = station`|
| Side effect   | `last_heartbeat` updated via single-row `.update()`            |

The service user has `set_unusable_password()` and the `Kiosk` role. It
cannot log in through the normal auth flow but satisfies all `created_by` /
`recorded_by` FKs on mutating records.

### Permissions

`IsKioskStation` is the only permission class on kiosk endpoints. It is not
used anywhere else. Consequently, the `Kiosk` role is denied access to
every existing endpoint by default — it never appears in `IsStaffOrAbove`,
`IsManagerOrAdmin`, or `IsAdminRole`.

### Endpoints

| Method | URL                                                  | Purpose                                  |
|--------|------------------------------------------------------|------------------------------------------|
| POST   | `/api/v1/kiosk/identify/`                            | Look up customer by `national_id`        |
| POST   | `/api/v1/kiosk/register/`                            | Register a new kiosk customer            |
| GET    | `/api/v1/kiosk/product/<sku>/`                       | Barcode scan → product details           |
| POST   | `/api/v1/kiosk/checkout/`                            | Atomic checkout (single call)            |
| GET    | `/api/v1/kiosk/receipt/<order_id>/`                  | Receipt for an order created by station  |
| GET    | `/api/v1/kiosk/verification/<order_id>/`             | Poll verification status                 |
| POST   | `/api/v1/kiosk/heartbeat/`                           | Station health check                     |

Employee-facing (standard Token auth, Staff+):

| Method | URL                                                  | Purpose                                  |
|--------|------------------------------------------------------|------------------------------------------|
| GET    | `/api/v1/kiosk/verifications/pending/`               | List pending verifications               |
| POST   | `/api/v1/kiosk/verifications/<id>/approve/`          | Approve → order DELIVERED                |
| POST   | `/api/v1/kiosk/verifications/<id>/reject/`           | Reject → REFUNDED + stock restored       |

### Atomic checkout

`POST /api/v1/kiosk/checkout/` runs inside one `transaction.atomic()`:

1. Lock products via `Product.objects.select_for_update().get(...)`
2. Validate stock inside the lock (no TOCTOU gap)
3. Create `SalesOrder` (status=CONFIRMED)
4. Create line items, compute totals
5. Deduct stock (bulk negative `InventoryMovement`)
6. Record `Payment`, transition order to PAID
7. Roll `random.random() < station.verification_rate`
8. Either set DELIVERED, or create a pending `VerificationRequest`

Error responses use the standard envelope (`{error, code, details?}`) — notably
`409` with `insufficient` array on stock shortfall.

### Throttling

Per-station (not per-user), keyed by `station.pk`:

| Scope              | Rate       | Endpoints                          |
|--------------------|------------|------------------------------------|
| `kiosk_identify`   | 60/min     | identify, register                 |
| `kiosk_scan`       | 120/min    | product lookup                     |
| `kiosk_checkout`   | 30/min     | checkout                           |
| `kiosk_poll`       | 60/min     | receipt, verification status       |

### Provisioning

```
python manage.py provision_kiosk --store LAS-MERCEDES-01 --station 3 \
    --by admin@retailops.local
```

The raw key is printed **once**; only its SHA-256 hash and 8-char prefix are
stored. The Django admin exposes equivalent actions on `KioskStation`:
rotate-key, deactivate, activate.

## Client-side migration (required in `kiosk/`)

The PWA still runs against the old `POST /auth/token/` flow. To adopt v2:

### `index.html` config block

```js
window.__KIOSK_CONFIG__ = {
    BASE_URL: "https://retailops.example.com",
    API_PATH: "/api/v1",

    // NEW — provisioned per station; replaces KIOSK_USER_EMAIL + KIOSK_PASSWORD
    KIOSK_API_KEY: "<raw-key-from-provisioning>",

    STORE_NAME: "...",
    STORE_ADDRESS: "...",
    KIOSK_STATION_NUMBER: "3",
    ENABLED_PAYMENT_METHODS: ["card"],
    // ...
};
```

### `app/config.js`

- Remove `KIOSK_USER_EMAIL` and `KIOSK_PASSWORD` from `_REQUIRED`.
- Add `KIOSK_API_KEY` to `_REQUIRED`.
- Drop `KIOSK_USER_EMAIL` / `KIOSK_PASSWORD` fields from the frozen config.

### `app/api.js` / `app/services/auth.js`

- Replace the `Authorization: Token …` header with `Authorization: KioskKey ${CONFIG.KIOSK_API_KEY}`.
- Delete `_reauth()` and all login/retry logic — the API key is static.
- On 401, surface a single terminal message: "Estación desactivada — contacte a un administrador." No retry.

### Checkout flow

- Replace the 10-step flow (customer lookup by derived email, order create,
  line-item creates, payment create, status transitions) with a single call:
  `POST /api/v1/kiosk/checkout/` with `{customer_id, items, payment_reference}`.
- Add a poll loop on `GET /api/v1/kiosk/verification/<order_id>/` when the
  response contains `verification_required: true`.

### Customer identification

- Replace the Cédula-to-email derivation with `POST /api/v1/kiosk/identify/`
  — send `{ national_id }`, expect `{ customer_id, first_name, last_name }`
  or 404.
- Registration: `POST /api/v1/kiosk/register/` with
  `{ national_id, first_name, last_name, phone? }`. The server assigns a
  non-guessable `kiosk-<uuid>@kiosk.internal` email automatically.

## Security posture

| # | Old flaw                               | v2 status    |
|---|----------------------------------------|--------------|
| 1 | Password in static HTML                | Mitigated — API key, not a credential |
| 2 | Single shared account                  | Eliminated — per-station service user |
| 3 | Manager-role over-privilege            | Eliminated — dedicated Kiosk role     |
| 4 | Non-expiring DRF tokens                | Eliminated — keys rotate per station  |
| 5 | Deterministic derived email + PII notes| Eliminated — `national_id` field + UUID email |
| 6 | TOCTOU stock race                      | Eliminated — `select_for_update()` lock |
| 7 | 10+ sequential checkout calls          | Eliminated — single atomic endpoint   |
| 8 | Page reload invalidates all tokens     | Eliminated — static per-station key   |

Residual risk: the API key still lives in `index.html` on the kiosk device.
Compromise is scoped to that station's `/api/v1/kiosk/*` access — no reach
into order management, inventory adjustments, user data, or any other main
API functionality.
