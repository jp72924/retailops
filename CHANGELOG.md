# Changelog

## Unreleased

### Breaking

- **`reference_number` is now required for `bank_transfer`, `card` and `check`
  payments on `POST /api/v1/payments/`.** Requests without one are rejected with
  `400` and a `reference_number` entry in `details`. This applies to the REST API,
  the `retailops_record_payment` MCP tool, and RetailOps CLI's `payments record`.
  The back-office payment form has enforced the same rule since the MCP
  documentation-drift fix; the API, MCP and CLI did not, so the same business rule
  held on one surface out of four. Callers recording those three methods without a
  reference will start getting a rejection they did not get before.

  The rule stacks with the existing OCR requirement rather than replacing it: a
  `bank_transfer` under OCR needs both its own bank reference and a
  `transaction_key` (or manual override `notes`). A request missing both now
  reports both in a single response instead of surfacing them one at a time.

### Fixed

- **Kiosk checkout no longer holds a global order-number lock across the VEPay
  OCR call.** `POST /api/v1/kiosk/checkout/` ran receipt parsing inside its
  transaction, after `order.save()` had taken the `SELECT FOR UPDATE` row lock
  on the `SO-{today}` sequence counter — and that lock is released on commit of
  the outer transaction, not when `SequenceCounter.next_value()` returns. Every
  order created that day passes through that one row, so a single checkout
  waiting on VEPay (two attempts against `ocr_timeout_seconds`, up to 61s at the
  default) blocked all other order creation for the duration. Receipt decoding,
  OCR and recipient validation now run before the transaction opens; the
  transaction re-acquires the product locks, re-checks stock, and re-checks the
  order total and `transaction_key` before writing, so the checkout is still all
  or nothing and rejects an order whose price moved mid-OCR.
- Kiosk checkout derived `reference_number` by stripping the result of an `or`
  rather than each candidate. A whitespace-only `receipt.reference` won the `or`
  and then collapsed to `''`, storing a blank reference for a `card` payment — the
  exact value the new rule forbids, arriving through a path that bypasses the
  serializer. A non-string value such as `{"reference": 12345}` reached `.strip()`
  inside the atomic block and raised `AttributeError`, surfacing as an unhandled
  `500` rather than a `400`. Both are fixed in the same expression.
- `mcp_server/prompts/workflows.py` told the agent to collect a reference for
  `mobile_payment` or `bank_transfer` — the wrong method set on both ends.

## Initial public backend release

- Publish RetailOps Backend as the Django/API/MCP system of record.
- Keep RetailOps Kiosk and RetailOps CLI as independent projects.
- Include local SQLite setup, configurable PostgreSQL/Cloud SQL, and
  configurable local/GCS/S3-compatible media storage.
- Include Kiosk station provisioning APIs and documentation for connecting an
  external Kiosk frontend.
