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

- **The primary `currency_code` can no longer change once any order or payment
  exists.** `PATCH /api/v1/settings/`, the Settings page and the admin reject it
  with a `currency_code` error. Every stored amount — product prices, order
  totals, payments — is a bare number in the primary currency, so the change
  never converted anything: it silently re-labelled all of it, turning a $10
  product into €10. The symbol, the decimals and the whole secondary currency
  remain editable.

### Added

- **Payments record the currency and exchange rate they were made at.** Until
  now the currency identity and the BCV rate lived only in `SystemSettings`, so
  editing the symbol or refreshing the rate restated every historical payment —
  a sale paid in bolívares last month was shown at today's rate. Each payment
  now references a `CurrencySnapshot` holding the primary and secondary currency
  and the rate in effect when it was recorded, and keeps it.

  Snapshots are immutable and shared: every payment recorded under the same
  configuration points at the same row. The cost is one 8-byte reference per
  payment and roughly one small row per rate update that a payment actually
  used, instead of copying the configuration onto every payment.

  Orders store nothing new. An order's value in the secondary currency is
  derived: its confirmed payments at their own recorded rates, plus the
  outstanding balance at the live rate — the rate it will be settled at. A fully
  paid order therefore no longer moves, while an unpaid one follows the rate.
  The back-office shows payments and paid totals at their recorded rates and
  outstanding balances at the live rate; a payment's page also shows the rate
  it was recorded at, where that rate came from, and, for an OCR'd receipt, the
  amount the receipt itself states.

  API: payments gain `currency` and `amount_secondary`; orders gain `currency`
  (with a `basis` of `live` or `recorded`) and `secondary`; kiosk checkout
  receipts and `GET /api/v1/kiosk/receipt/<id>/` gain `currency` and
  `amount_secondary`; settings gain a read-only `secondary_rate_source`. All
  additive.

  Payments recorded before this release get the configuration in effect when
  the migration runs, marked `rate_source: "backfilled"`; their secondary
  amounts are approximate, because the rate they were made at was never stored.
  **Run the migrations with the application stopped** — `0023` makes the
  reference mandatory and fails if a payment was created without one mid-deploy.

### Fixed

- **Kiosk checkout records the exchange rate its receipt was validated at.** The
  receipt was converted at the rate read before the OCR call, but nothing tied
  the stored payment to that rate. Checkout now reads the rate once and records
  that same rate on the payment, even if the BCV rate is refreshed while the
  receipt is being read.
- **A manually entered exchange rate is timestamped.** Only the automatic rate
  refresh set `secondary_rate_updated_at`; a rate typed into the Settings page,
  the admin, `PATCH /api/v1/settings/` or `manage.py init` kept the timestamp of
  the last fetch. Any rate change now stamps the time, and the new
  `secondary_rate_source` says whether it was `fetched` or `manual`.
- **The dashboard revenue card no longer converts a month of sales at today's
  rate.** It shows the primary currency only.

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
- **`ocr_timeout_seconds` now budgets a whole OCR call rather than each attempt.**
  `VEPayClient` applied the configured timeout per request, so the retry on a 5xx
  or network failure made the real worst case `2 × timeout + 1s` — 61s at the
  default 30 — and the health check's `/health` → `/healthz` fallback could take
  `2 × timeout` the same way. Both now run against one deadline: a retry happens
  only if enough budget remains for the backoff plus an attempt that could
  plausibly succeed, so a leftover fraction of a second no longer turns a
  diagnosable `502` into a misleading `timeout`. The connect phase is capped
  separately at 5s, so an unreachable host fails in seconds instead of consuming
  the whole budget on a connection that will never open.
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
