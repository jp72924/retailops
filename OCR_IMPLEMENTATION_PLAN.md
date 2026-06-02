# Plan: Pago Móvil & Transferencia Bancaria with VEPay OCR

## 1 · Context

Currently `kiosk/app/components/PaymentScreen.js` lets the customer pick a method, then jumps straight to `ProcessingScreen` which calls the existing `/api/v1/payments/` endpoint. There's no opportunity for the customer to attest details of a non-card payment.

`Payment` ([core/models.py:343](core/models.py:343)) only has `amount`, `payment_method`, `reference_number` (CharField), `notes`. There is no place to store the receipt image, originating phone/bank, or the deterministic `transaction_key` from VEPay needed for duplicate detection.

`SystemSettings` ([core/models.py:438](core/models.py:438)) is the right home for VEPay configuration — same singleton pattern that already drives currency settings. The kiosk already loads it at boot via `services/settings.js`, so adding fields there flows down to the kiosk for free.

The VEPay API is documented at <https://vepay-api.fly.dev/docs>. From the README, the relevant endpoints are:
- `POST /v1/receipts/parse` — multipart upload, returns the JSON receipt schema
- `POST /v1/receipts/parse-json` — base64 JSON variant
- `POST /v1/jobs` + `GET /v1/jobs/{id}` — async batch
- `GET /v1/capabilities`, `GET /healthz` — diagnostics
- Optional `X-API-Key` header

The receipt JSON shape (per [redacted_receipt.json](redacted_receipt.json)) gives us: `payment.bank_app`, `payment.reference`, `payment.amount.value` (normalized decimal), `payment.date_time.iso`, `origin.phone/account/bank`, `recipient.phone/document_id/bank`, `transaction_key`, `validation.is_complete`, `validation.missing_fields`. This is the contract the backend will store.

---

## 2 · Open decisions before coding

These should be confirmed first; they alter the data model and API contract. Recommended defaults below.

| # | Question | Recommendation |
|---|---|---|
| **D1** | Add a new `mobile_payment` enum value to `Payment.METHOD_CHOICES`, or reuse the existing `bank_transfer` for both? | **Add new value.** Splitting them gives clearer reporting and lets us label kiosk receipts correctly. Migration is trivial — new enum value, no data backfill. |
| **D2** | Store OCR receipt JSON as a `JSONField` on `Payment`, or as a separate `ReceiptVerification` model? | **JSONField on `Payment`** plus a few denormalized columns (`receipt_image`, `origin_phone`, `origin_bank`, `transaction_key`). Keeps queries simple; `Payment` is already immutable so the JSON is captured once and never mutated. |
| **D3** | Where does the VEPay API key live? `SystemSettings` row, or `.env`? | **`SystemSettings` field**, encrypted-at-rest if possible. Reason: lets non-engineers rotate the key from the back-office, and per-tenant deployments need different keys. Treat the field as write-only (mask in GET responses). |
| **D4** | Strict matching: must the OCR'd amount equal `order.amount_outstanding`? | **Yes for auto-confirm; soft-warn otherwise.** Exact match → auto-Pay. Mismatch → kiosk surfaces the warning, customer can call staff who logs in and confirms manually. |
| **D5** | Manual entry fallback when VEPay is down or the customer skips upload? | **Yes.** Same form fields, but `transaction_key` and `validation.is_complete` will be blank. Payment ends up in a `pending_review` state (new `Payment.status` field) and an Admin sweeps them later. |
| **D6** | Where does the kiosk send the screenshot? Direct to VEPay or through the Django backend? | **Through the backend.** API key stays server-side, we audit every call, and we can apply rate limits / file-size caps. Reusing `api/throttling.py`'s scoped-throttle pattern. |

---

## 3 · Architecture overview

```
┌──────────────┐  multipart upload  ┌─────────────────────────────┐
│ Kiosk PWA    │ ─────────────────▶ │ Django: /api/v1/payments/   │
│ PagoMovilForm│                    │   receipts/verify/  (POST)  │
│ Screen.js    │ ◀───── parsed ──── │                             │
└──────────────┘     receipt JSON   │ ┌─ services/vepay.py ─────┐ │
                                    │ │ VEPay client (async)    │ │
                                    │ │ POST /v1/receipts/parse │ │ ──▶ vepay-api.fly.dev
                                    │ └─────────────────────────┘ │
                                    │                             │
                                    │ Match against SalesOrder    │
                                    │ Reject duplicate transaction_key
                                    │                             │
                                    └─────────────────────────────┘

Then on confirm:
┌──────────────┐                   ┌─────────────────────────────┐
│ Kiosk        │ ─── POST ───────▶ │ /api/v1/payments/           │
│ Confirm      │   {payment, OCR}  │ Creates Payment + image     │
└──────────────┘                   └─────────────────────────────┘
```

The kiosk **never** holds the API key. Verification and persistence are two separate calls so the customer can re-take the photo without burning a Payment record.

---

## 4 · Step-by-step implementation phases

### Phase A — Backend data model (≈1 day)

**A1. Migrate `Payment` to carry receipt evidence.** Edit [core/models.py:343](core/models.py:343):
```python
PENDING_REVIEW = 'pending_review'
CONFIRMED      = 'confirmed'
STATUS_CHOICES = [(PENDING_REVIEW, '…'), (CONFIRMED, '…')]

status              = models.CharField(max_length=20, choices=STATUS_CHOICES, default=CONFIRMED)
receipt_image       = models.ImageField(upload_to='receipts/%Y/%m/', blank=True, null=True)
ocr_receipt_data    = models.JSONField(blank=True, null=True)   # full VEPay JSON
transaction_key     = models.CharField(max_length=128, blank=True, db_index=True)
origin_phone        = models.CharField(max_length=30, blank=True)
origin_bank         = models.CharField(max_length=120, blank=True)
recipient_bank      = models.CharField(max_length=120, blank=True)
recipient_account   = models.CharField(max_length=64, blank=True)
verified_at         = models.DateTimeField(null=True, blank=True)

class Meta:
    constraints = [
        models.UniqueConstraint(
            fields=['transaction_key'],
            condition=models.Q(transaction_key__gt=''),
            name='payment_transaction_key_unique_when_set',
        ),
    ]
```
Add `'mobile_payment'` to `METHOD_CHOICES`.

Run `python manage.py makemigrations && python manage.py migrate`.

**A2. Extend `SystemSettings`** with a VEPay block. Edit [core/models.py:438](core/models.py:438):
```python
ocr_enabled            = models.BooleanField(default=False)
ocr_provider           = models.CharField(max_length=20, default='vepay', choices=[('vepay', 'VEPay')])
ocr_base_url           = models.URLField(default='https://vepay-api.fly.dev')
ocr_api_key            = models.CharField(max_length=256, blank=True)        # write-only at API layer
ocr_timeout_seconds    = models.PositiveIntegerField(default=30)
ocr_max_file_mb        = models.PositiveIntegerField(default=8)
ocr_strict_amount      = models.BooleanField(default=True)
ocr_require_complete   = models.BooleanField(default=False)
ocr_enabled_methods    = models.JSONField(default=list)   # e.g. ["mobile_payment", "bank_transfer"]
```
Update `clean()` to require `ocr_base_url` when `ocr_enabled`.

**A3. Add VEPay client service.** New file `core/services/vepay.py`:
- `class VEPayClient` with constructor `(base_url, api_key, timeout)` reading from `SystemSettings.get()`
- `async def parse_receipt(image_bytes, filename, content_type) -> dict` — wraps `requests.post(f'{base_url}/v1/receipts/parse', files=…, headers={'X-API-Key': key} if key else {})`
- Translate VEPay errors into a `VEPayError` with `code`, `message`, `is_retryable`. Validation errors (4xx) are non-retryable; 5xx and timeouts are retryable
- Single source of truth for the JSON-shape constants we care about (paths into the parsed dict)

**A4. Pre-existing settings serializer.** Add the new VEPay fields to `api/serializers/settings.py`:
- Read serializer: include all but mask `ocr_api_key` (return `'***'` if set, empty otherwise)
- Write serializer: accept a literal `ocr_api_key` write but never echo it back; `'__no_change__'` sentinel keeps the existing value
- Update `api/views/settings.py` PATCH permission stays Manager+ but mask handling lives in the serializer

### Phase B — Backend API endpoints (≈1 day)

**B1. New endpoint: receipt verification (idempotent, no DB write)**
- Route: `POST /api/v1/payments/receipts/verify/` (Manager+ for back-office; Kiosk service-user role for kiosk)
- Multipart input: `image` (required), `sales_order` (required PK), `payment_method` (required, one of `mobile_payment`/`bank_transfer`)
- Calls `VEPayClient.parse_receipt`
- Server-side checks:
  1. Image size ≤ `ocr_max_file_mb`, MIME in {jpeg,png,heic}
  2. If `ocr_require_complete=True` and `validation.is_complete=False` → return 422 with `validation.missing_fields`
  3. Look up `Payment.objects.filter(transaction_key=…).exists()` → 409 if duplicate
  4. If `ocr_strict_amount=True`: compare `Decimal(payment.amount.value)` against `sales_order.amount_outstanding` after applying `secondary_exchange_rate` if currencies differ → 422 on mismatch
  5. Confirm `payment.bank_app` is in `ocr_enabled_methods` mapping (per-method allowlist)
- Returns the full VEPay JSON plus a server-computed envelope:
  ```json
  {
    "valid": true,
    "vepay": { …raw vepay schema… },
    "checks": {
      "amount_matches": true,
      "duplicate": false,
      "complete": true,
      "amount_normalized_usd": "19.28"
    },
    "warnings": []
  }
  ```
- Throttling: new scope `'ocr_verify'` at e.g. 12/min per kiosk station (extend [api/throttling.py](api/throttling.py))

**B2. Update Payment write serializer.** [api/serializers/payments.py](api/serializers/payments.py) accepts the new fields when method is `mobile_payment` or `bank_transfer`:
- `receipt_image` (write only, optional)
- `ocr_receipt_data` (read-only-ish; only kiosk sets it after a successful `/verify/`; Admin can patch via shell)
- `transaction_key`, `origin_phone`, `origin_bank`, `recipient_bank`, `recipient_account` (writeable on create)
- Cross-field validation: if method is mobile_payment/bank_transfer and `ocr_enabled` is True for that method, require `transaction_key` OR `notes` containing reason for manual override; otherwise mark `status='pending_review'`

**B3. Enrich Payment list/retrieve filters.** [api/filters.py](api/filters.py): add `?status=`, `?method=`, `?has_receipt=`, `?bank=`. Surfaces in Manager review UI.

### Phase C — Back-office settings UI (≈0.5 day)

**C1. Extend `core/templates/core/settings.html`.** Below the secondary-currency block (around line 140) add a new admin-only `<section>`:
- Toggle `ocr_enabled`
- Provider readonly select (currently only VEPay)
- `ocr_base_url`, `ocr_api_key` (password input + "Replace key" button — masks on display)
- `ocr_timeout_seconds`, `ocr_max_file_mb`
- Checkboxes for which payment methods OCR applies to (`mobile_payment`, `bank_transfer`)
- `ocr_strict_amount`, `ocr_require_complete`
- "Test connection" button (AJAX `GET /api/v1/payments/receipts/healthz/` → calls VEPay's `/healthz` server-side, shows green/red badge)

**C2. Update view in `core/views.py`** (the settings update view) to handle the new fields, mirroring the existing currency-validation pattern (use the model's `clean()` then `save()`).

**C3. Optional: a simple "Receipt review queue"** — a list view at `/payments/?status=pending_review` showing thumbnails (`receipt_image`), OCR JSON, and Approve / Reject actions. Small enough to ship in this phase.

### Phase D — Kiosk new screen + flow (≈1.5 days)

**D1. Add a new method choice to the kiosk display map.** Edit [kiosk/app/components/PaymentScreen.js:16](kiosk/app/components/PaymentScreen.js):
```js
const PAYMENT_DISPLAY = {
  cash:           { label: 'Efectivo',                desc: 'Billetes y monedas',                  icon: '💵' },
  mobile_payment: { label: 'Pago móvil',              desc: 'Banco de Venezuela, Mercantil, BBVA…', icon: '📱' },
  bank_transfer:  { label: 'Transferencia bancaria',  desc: 'Desde tu app bancaria',               icon: '🏦' },
  card:           { label: 'Tarjeta débito / crédito',desc: 'Visa, Mastercard, AmEx',              icon: '💳' },
  check:          { label: 'Cheque',                  desc: 'Cheque a nombre de la empresa',       icon: '📄' },
  other:          { label: 'Otro método',             desc: 'Consulta en caja',                    icon: '💱' },
};
```

**D2. Branch routing on method.** In [PaymentScreen.js:120](kiosk/app/components/PaymentScreen.js):
```js
container.querySelector('#btn-confirm').addEventListener('click', () => {
  const needsReceipt = ['mobile_payment','bank_transfer'].includes(self._selected);
  navigate(needsReceipt ? 'pago-movil-form' : 'processing', {
    paymentMethod: self._selected,
    totalUsd: total,
  });
});
```

**D3. New component `kiosk/app/components/PagoMovilFormScreen.js`.** Markup mirrors [Registro screen](kiosk-wireframe.html) (uses `.field-group`, `.ft-input`, `.ft-select`, `.field-row`, `.alert-box`, `.bottom-bar`):
- Top: progress bar (step 5 active, with a "5b · Comprobante" sub-label)
- Total to pay (pulls from store)
- **Upload card** at top:
  - "Sube la captura de tu comprobante" + dashed dropzone or `<input type="file" accept="image/*" capture="environment">`
  - On change: shows thumbnail + "Verificando…" spinner → POST to `/api/v1/payments/receipts/verify/`
  - On success: pre-fills form fields below, shows green check, "Comprobante verificado ✓"
  - On failure: shows the specific check that failed (amount mismatch / duplicate / incomplete) with allow-staff-override CTA
- **Form fields** (read-only when OCR-filled, editable when manual):
  - Banco emisor (`<select>`, options: BDV, Bancamiga, Banesco, Mercantil, BBVA Provincial)
  - Teléfono origen (`<input type="tel">`)
  - Cédula origen (`<input type="text">` with V/E prefix toggle reusing `.cedula-type-btns`)
  - Referencia (`<input>` numeric, monospace)
  - Fecha del pago (`<input type="datetime-local">`)
  - Monto pagado (read-only — locked to `totalUsd` converted to Bs)
  - Concepto (textarea optional)
- "Cambiar foto" link to re-upload
- Bottom bar: "Confirmar pago →" button. Disabled until either a verified upload OR all required fields are filled manually.
- On click confirm: `navigate('processing', {paymentMethod, totalUsd, receipt: {…}})`

**D4. Update `ProcessingScreen.js`.** When `params.receipt` is present, the POST to `/api/v1/payments/` includes the receipt fields (`receipt_image` as base64 or a re-upload, `transaction_key`, `origin_phone`, `origin_bank`, `recipient_bank`, `ocr_receipt_data`). Add error-state copy for `409 duplicate transaction_key` and `422 amount mismatch`.

**D5. Apply VEPay settings in `kiosk/app/services/settings.js`.** When `applySettings(json)` runs, store `json.ocr_enabled`, `json.ocr_enabled_methods`, `json.ocr_max_file_mb` on the kiosk store. The PaymentScreen consults `ocr_enabled_methods` to know whether to route to the form screen or skip directly to processing (e.g. if OCR is disabled but the form is still wanted, skip the upload card and go form-only).

**D6. Update wireframe demo.** Add a 7th tab "5b · Comprobante" to [kiosk-wireframe.html](kiosk-wireframe.html) showing the new form with both states (empty / uploaded-and-verified) so designers can review without running the kiosk.

### Phase E — Validation, security, observability (≈0.5 day)

**E1. Auditing.** Every call to `/receipts/verify/` writes a row to a new lightweight `OcrCallLog` model: `kiosk_station`, `sales_order`, `request_id` (from VEPay response), `status`, `latency_ms`, `bytes_sent`. Don't store the image bytes here — only metadata. Helps debugging and abuse detection.

**E2. Image handling.**
- Backend resizes to ≤ 1600px max side before forwarding to VEPay (Pillow). VEPay handles original size but we save bandwidth.
- Strip EXIF.
- Reject HEIC if Pillow/HEIF plugin not present (return 415).

**E3. Retries.** VEPay client retries once on 5xx/network error with 1s backoff. Beyond that, surface the error to the kiosk so the customer can choose manual entry.

**E4. Throttling.** New `OcrVerifyRateThrottle` (12/min per IP). Add to [api/throttling.py](api/throttling.py).

**E5. Privacy.**
- Receipts are stored under `MEDIA_ROOT/receipts/YYYY/MM/` with a path that includes the order number (not the customer name).
- Add `Payment.delete_receipt_image_after_days` setting on `SystemSettings` (default 90) and a Django management command `purge_receipts` that the user can wire to cron.

### Phase F — Tests (≈0.5 day)

**F1. Unit tests for `core.services.vepay`.** Mock `requests.post` with `responses` library; test happy path, timeout, 4xx, 5xx, malformed JSON, missing API key.

**F2. Integration test in `test_mcp_tools.py` style** but for REST: fixture image (the `redacted_receipt.jpeg` shape) + recorded VEPay response, POST to `/receipts/verify/`, assert envelope shape and the validation checks.

**F3. End-to-end manual test** (documented in the plan):
1. Set `ocr_enabled=true`, `ocr_base_url=https://vepay-api.fly.dev`, leave key blank
2. Boot kiosk, place a Bs. 963,89 order, pick "Pago móvil"
3. Upload a real BDV screenshot of that exact amount → form pre-fills
4. Try a screenshot of a different amount → 422 with mismatch banner
5. Re-upload the first screenshot → 409 duplicate
6. Toggle `ocr_enabled=false` → form still appears but no upload card; manual entry creates a `pending_review` payment.

---

## 5 · Files touched / created

| Area | File | Action |
|---|---|---|
| Model | [core/models.py](core/models.py) | Add Payment fields, new `mobile_payment` choice, SystemSettings OCR block, new `OcrCallLog` model |
| Migration | new under `core/migrations/` | Auto-generated |
| Service | `core/services/__init__.py`, `core/services/vepay.py` | New |
| Settings serializer | `api/serializers/settings.py` | Add VEPay fields, mask api_key |
| Payment serializer | `api/serializers/payments.py` | Add receipt fields |
| Filters | [api/filters.py](api/filters.py) | Add Payment filters |
| Throttling | [api/throttling.py](api/throttling.py) | Add OCR verify throttle |
| New endpoint view | `api/views/payments.py` | Add `verify_receipt` action |
| URLs | [api/urls.py](api/urls.py) | Wire `/payments/receipts/verify/` |
| Settings template | [core/templates/core/settings.html](core/templates/core/settings.html) | New OCR section |
| Settings view | core/views.py settings handler | Persist new fields |
| Receipt review template | `core/templates/core/payment_review.html` | New (optional Phase C3) |
| Kiosk PaymentScreen | [kiosk/app/components/PaymentScreen.js](kiosk/app/components/PaymentScreen.js) | Add `mobile_payment`, branch routing |
| New kiosk screen | `kiosk/app/components/PagoMovilFormScreen.js` | New |
| Kiosk router | `kiosk/app/router.js` | Register the new screen |
| Kiosk settings service | `kiosk/app/services/settings.js` | Apply VEPay flags |
| Kiosk store | `kiosk/app/store.js` | Add `ocr_enabled`, `ocr_enabled_methods` keys |
| Wireframe demo | [kiosk-wireframe.html](kiosk-wireframe.html) | Add "5b · Comprobante" tab |
| Tests | new `core/tests/test_vepay.py`, `api/tests/test_receipt_verify.py` | New |
| Docs | [API_GUIDE.md](API_GUIDE.md), [CLAUDE.md](CLAUDE.md) | Document new endpoint + flow |

---

## 6 · Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| VEPay API outage during business hours | Kiosk sales blocked | Manual-entry fallback (D5/E3); `pending_review` status + back-office sweep |
| Customer uploads a forged/edited screenshot | Fake payment recorded | `transaction_key` duplicate check + amount match + manual review queue + spot-checks against bank statement |
| OCR misreads amount (e.g. comma/dot confusion) | False mismatch | VEPay normalizes amounts to dot decimal; we still parse with `Decimal`; surface OCR'd amount in the form so customer can see the read |
| API key leaks to kiosk JS | Vendor abuse / bill | Always proxy through Django; never include key in any kiosk response |
| Old/expired receipts re-used | Fraud | Add 24h max-age check on `payment.date_time.iso` (configurable in `SystemSettings`) |
| HEIC images from iPhones | OCR fails silently | Detect MIME → 415 with explicit "convert to JPEG" message; recommend `<input accept="image/jpeg,image/png">` |

---

## 7 · Sequencing & rough estimate

- **Day 1**: Phase A (model + service + settings serializer)
- **Day 2**: Phase B (verify endpoint + Payment serializer) + Phase C (back-office settings UI)
- **Day 3**: Phase D (kiosk screen + flow wiring) + Phase F (tests)
- **Day 4**: Phase E (auditing/throttling/privacy) + manual E2E + docs

Total: ≈3.5–4 dev-days for one engineer; parallelizes well between backend (A/B/C) and kiosk (D) once the API contract in B1 is frozen.