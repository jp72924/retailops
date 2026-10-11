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

- **Payments pending review can be approved or rejected.** A payment whose
  receipt could not be verified automatically was recorded as `pending_review`
  and then had nowhere to go but the Django admin, where changing its status
  did not touch the order. Managers and Admins now settle it from the payment's
  page in the back-office, or with `POST /api/v1/payments/{id}/approve/` and
  `/reject/`. Approving applies the payment to its order exactly as if it had
  been recorded confirmed, and can record the bank transaction key the reviewer
  checked, refusing one already held by another payment. Rejecting requires a
  reason and leaves the order unchanged. Payments gain `reviewed_by`,
  `reviewed_at` and `review_notes`.

  `Payment.status` gains a third value, `rejected`, also accepted by
  `?status=` on `GET /api/v1/payments/`. Clients that branch on every status
  value (RetailOps CLI, the MCP server) should expect it.

- **Products can be sold without a stock limit.** A licence, a subscription or a
  service has no stock to run out of, but every product used to: with no
  inventory movements it read as 0 in stock, every sale path refused it with
  `insufficient_stock`, and the back-office listed it as out of stock.
  Products gain `kind` (`physical`, `digital` or `service`) and
  `track_inventory`. They are independent: `kind` only classifies, and
  `track_inventory` alone decides whether stock exists, so a limited-run digital
  product can track stock and a made-to-order physical one need not. Existing
  products stay physical and tracked.

  A product that does not track inventory is never locked, checked or deducted
  when an order is confirmed, so an order made only of such products takes no
  stock locks at all. Its `current_stock` is `null`, it is never low or out of
  stock, `?stock=ok` includes it, and `?kind=` and `?track_inventory=` filter on
  the new fields. Manual adjustments for it are refused with
  `400 inventory_not_tracked`. The product form gains the type and a "Track
  inventory" switch, and the inventory page shows "Unlimited".

  The kiosk does not list or sell these products: Konteo Express reads a null
  `current_stock` as zero and would refuse the sale, so kiosk search, detail and
  scan skip them and checkout refuses them as `invalid_product` until the app
  supports them.

  **API:** `current_stock` on products becomes nullable (`int` to `int | null`),
  which changes the generated OpenAPI type. The CLI and the MCP server show it as
  empty.

- **Online stores can read a catalogue from RetailOps.** RetailOps was built
  for physical stores; an online store or a web catalogue had nothing to read:
  the kiosk namespace shows six products at a time and writes to the database on
  every request, and the staff API exposes customers and orders.

  A storefront is now a **sales channel**: a server-to-server integration with
  its own service user (role `Channel`, no usable password) and one or more
  keys, sent as `Authorization: ChannelKey <secret>`. Only the SHA-256 of a key
  and an 8-character prefix are stored. A channel can hold several active keys,
  so a store can switch to a new one before the old one is revoked. A channel
  key works only under `/api/v1/storefront/`: the staff and kiosk APIs answer
  `401` to it, and the storefront answers `401` to a staff token or a kiosk key.
  Authenticating is one read and writes nothing. Each channel is limited to 300
  requests per minute. Provision one with `manage.py provision_channel` (add
  `--new-key` for another key); the admin can issue and revoke keys and
  deactivate a channel.

  A **listing** is how a product, or a family of related products, is
  presented: a slug, text, features, a badge, free-form attributes, an image,
  and **variants** such as the same licence for 12 or 24 months. The product
  stays the unit of price, stock and sale, and each variant is a separate
  product. Back-office managers publish a product with **Publish to store** on
  its page; everything else is edited in the admin. Deleting a product takes its
  variant with it; orders and stock movements still protect a product from being
  deleted, as before.

  Four read-only endpoints under `/api/v1/storefront/` serve published
  listings (paginated, filterable, searchable), one listing by slug, the
  category tree, and the payment instructions a customer is shown. Prices come
  from the product. Availability is a label (`in_stock`, `low_stock`,
  `out_of_stock` or `unlimited`) and exact stock is never published. Every
  response carries the live currency and rate, so a store never reads the
  staff-only settings endpoint. The catalogue is sent with
  `Cache-Control: max-age=60`; payment instructions are never cached. See
  `STOREFRONT_INTEGRATION.md`.

  The `Role.name` choices gain `Channel`; the role appears in the user lists
  once a channel has been provisioned.

- **Online stores can place orders and pay them.** The storefront could read a
  catalogue but not sell from it. A store now creates an order, is told what to
  charge, and sends the receipt that pays it, in the two steps a receipt payment
  needs: the customer gets an order number and an amount in bolívares *before*
  they pay.

  `POST /api/v1/storefront/orders/` creates and confirms an order in one short
  transaction, reserving stock through the same rule every other order uses and
  freezing the exchange rate, so the quoted amount in the secondary currency
  does not move. `client_reference`, the store's own order id, is the
  idempotency key: a retry returns the same order instead of a second order and
  a second stock reservation. Only products in a published listing can be
  ordered, and a failed order leaves nothing behind: no order, no reserved
  stock, no customer. It takes product locks in the same order the kiosk does,
  so the two cannot deadlock.

  `POST /api/v1/storefront/orders/{order_number}/payments/` takes the receipt.
  It is validated by the same code as the kiosk's, under a stricter policy: only
  RetailOps's own OCR reading counts, never a transaction key or reading the
  request claims, and when OCR is unavailable the payment waits for a person
  instead of failing the customer. The receipt is checked against the rate
  frozen at order creation and the payment is recorded with it, so a rate change
  between the purchase and the payment does not refuse a customer who paid what
  they were quoted. OCR runs before any lock is taken. Each call is logged in
  `OcrCallLog` against the channel that spent it (`OcrCallLog.channel`), and
  channels are limited to 60 orders and 30 payments a minute.

  The store authenticates its own customers. Each travels as an `external_ref`
  and is tied to a RetailOps customer the first time they order: an existing
  link always decides; a new customer whose email is unknown is created; one
  whose email already belongs to a customer is linked only when the channel is
  set to **trust email verification** (off by default) and the store reports the
  address verified, and is otherwise refused with `409 customer_email_conflict`.
  A national id never links anyone, a customer is never overwritten, and
  nothing stored about a customer is returned to the store.

  `GET /api/v1/storefront/orders/{order_number}/` and
  `GET /api/v1/storefront/customers/{external_ref}/orders/` return the channel's
  own orders only. `customer_status` (`awaiting_payment`, `payment_under_review`,
  `processing`, `completed`, `cancelled`, `refunded`) and each payment's
  `review_status` (`verified`, `needs_review`, `rejected`) are pure functions of
  the order and its payments.

  Unpaid orders hold their stock until `expires_at`, the channel's **order
  expiry minutes** after creation (60 by default). Schedule
  `manage.py expire_channel_orders` to cancel the ones that lapsed and return
  their stock; an order with any payment is never expired. Back-office order
  pages show where an online order came from, and the admin lists channel orders
  and customer links read-only.

  New tables: `ChannelOrder` and `ChannelCustomerLink`, side tables that leave
  `SalesOrder` and `Customer` untouched. `SalesChannel` gains
  `trusts_email_verification` and `order_expiry_minutes`. See
  `STOREFRONT_INTEGRATION.md`.

### Fixed

- **The back-office payment form re-checks the order before writing.** The form
  is only offered on Confirmed and Paid orders, but the view never checked: a
  crafted POST could pay a cancelled order back to Paid after its stock had
  already been restored. The order is now locked and its status re-checked, and
  anything other than Confirmed or Paid is refused.
- **An extra payment on a Paid order no longer moves its `paid_at`.** The
  back-office re-stamped `paid_at` whenever the paid total covered the order, so
  a late top-up moved the sale into the month it was topped up in.
- **Concurrent partial payments can no longer leave a covered order
  Confirmed.** `POST /api/v1/payments/` summed the order's payments without
  locking it, so two partial payments recorded at the same moment on
  PostgreSQL could each miss the other and leave a fully paid order unpaid. It
  also checked the order's status before the write, unlocked. Every path that
  pays an existing order now locks the order row first and re-checks its status;
  an order cancelled in the meantime returns `409 order_not_payable`.
- **Kiosk checkout locks its products in primary-key order.** It locked one
  product at a time in cart order, while every other stock writer locks in
  primary-key order. On PostgreSQL, two checkouts holding the same products in
  opposite orders could deadlock, and one was aborted with a `500` after its
  receipt had already been read. Error codes are unchanged.

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

- **Cancelling or refunding an order gives back only the stock it took.** Both
  added each line's quantity to stock whatever the order had deducted. An order
  that never deducted any (one set to Confirmed in the Django admin, say) got
  back stock it never took, and an order reversed twice got it back twice.
  Stock is now restored from the order's own inventory movements: it reverses
  what they still net to, so there is nothing left to give back the second time,
  and no line for a product that does not track inventory is ever credited. The
  API and the back-office share one implementation, which locks the order row
  and re-checks its status first.

## Initial public backend release

- Publish RetailOps Backend as the Django/API/MCP system of record.
- Keep RetailOps Kiosk and RetailOps CLI as independent projects.
- Include local SQLite setup, configurable PostgreSQL/Cloud SQL, and
  configurable local/GCS/S3-compatible media storage.
- Include Kiosk station provisioning APIs and documentation for connecting an
  external Kiosk frontend.
