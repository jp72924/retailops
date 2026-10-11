# RetailOps Storefront Integration

RetailOps can feed an online store or a web catalogue and take its orders. The
store is a **sales channel**: a server-to-server integration that authenticates
with a secret key, reads the catalogue RetailOps publishes for it, creates
orders and sends the receipts that pay them. This document covers provisioning a
channel, the catalogue, and the order and payment flow. The endpoints are listed
in `API_GUIDE.md` and described in the OpenAPI schema (`/api/v1/schema/`, tag
`storefront`).

## How it fits together

- **The product stays the unit of price, stock and sale.** Orders, the kiosk and
  inventory are unchanged.
- **A listing is how a product is presented.** It adds a slug, marketing copy, a
  badge, free-form attributes and an image, and groups the products a customer
  chooses between (the same licence for 12 or 24 months) as **variants**. Each
  variant is a separate product, because price lives on the product.
- **A channel is a service account, not a person.** Provisioning creates a
  service user (role `Channel`, no usable password) and one or more keys. The
  channel's keys never reach a browser, so there is no public key and no CORS to
  configure: a storefront calls RetailOps from its server.
- **The store authenticates its own customers.** RetailOps stores no password
  for them and trusts only the channel's key. Each customer travels as an
  `external_ref`, the store's own user id, and is tied to a RetailOps customer
  the first time they order (see [Customers](#customers)). Nothing the store
  reads ever describes a customer.
- **An order is two steps, because a receipt payment is.** The customer is given
  an order number and an amount in bolívares *before* they pay; the store then
  sends the receipt. The exchange rate is frozen when the order is created, so
  the amount the customer was quoted is the amount their receipt is checked
  against.

## Provision a channel

```bash
python manage.py provision_channel --name "Trend Store" --slug trend-store --by owner@example.com
```

The key is printed once. The database stores only its SHA-256 hash and an
8-character lookup prefix, so it cannot be recovered. Put it where the store's
server reads secrets (for example a Cloudflare Worker secret), never in client
code.

### Rotate a key without downtime

A channel can hold several active keys.

1. `python manage.py provision_channel --slug trend-store --new-key --label "2026-11"`
   prints a second key.
2. Deploy the store with the new key.
3. In the Django admin, open **Channel keys** and run **Revoke selected keys** on
   the old one.

The admin (**Sales channels**) can also issue a key, revoke every key of a
channel, or deactivate a channel, which refuses all its keys at once.

## Authenticate

```http
GET /api/v1/storefront/catalog/listings/
Authorization: ChannelKey <secret>
```

| Response | Meaning |
|----------|---------|
| `401` | No key, a malformed or unknown key, a revoked key, or a channel (or its service user) that is inactive |
| `429` | The channel exceeded its allowance, counted per channel: 300 catalogue and query requests a minute, 60 order creations, 30 payments (each can cost an OCR call) |

A channel key works **only** under `/api/v1/storefront/`. The staff API and the
kiosk API answer `401` to it, and the storefront answers `401` to a staff token
or a kiosk key. Authenticating reads one row and writes nothing.

## Publish products to the catalogue

In the back-office, open a product and choose **Publish to store** (Manager or
Admin). That creates a published listing with the product as its only variant.
Publishing a product that is already listed changes nothing.

Everything else is edited in the Django admin under **Listings**:

| Field | Meaning |
|-------|---------|
| `slug` | The public identifier; unique |
| `title`, `summary` | Marketing text; `summary` is optional |
| `description` | Long text; **empty means the first variant's product description** |
| `features` | A list of up to 20 short strings |
| `badge` | A short label such as "Popular" |
| `display_type` | How the store groups it, such as `license` or `subscription`; free text |
| `attributes` | A JSON object of free-form details: platform, activation, delivery |
| `category` | A product category |
| `image`, `external_image_url` | Optional; **empty means the first variant's product image** |
| `sort` | Ascending; the default order of the list |
| `is_published` | Unpublished listings are invisible to every channel |

Each variant has a product (a product can belong to one variant only), a
`label` such as "12 months", a `position`, and an optional `compare_at_price`.

A listing is visible when it is published and at least one variant is an
**active** product. A variant whose product is inactive is left out; it is never
shown as sold out. Prices always come from the product.

## Catalogue endpoints

Every response carries a live `currency` block and is meant to be cached by the
store: the catalogue is sent with `Cache-Control: max-age=60`. Every response also
carries `Vary: Authorization`, so a cache keyed by URL never hands one caller's
response to another.

### `GET /api/v1/storefront/catalog/listings/`

| Parameter | Meaning |
|-----------|---------|
| `category` | A category id; matches the category and everything beneath it |
| `display_type` | Exact match, ignoring case |
| `search` | Matches title, summary, slug and variant product names |
| `ordering` | `sort` (default), `title`, `created_at` or `from_price`; prefix `-` to reverse |
| `page`, `page_size` | Standard paging; `page_size` is capped at 100 |

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "slug": "antivirus-pro",
      "title": "Antivirus Pro",
      "summary": "Protection for every device.",
      "badge": "Popular",
      "display_type": "license",
      "category": { "id": 3, "name": "Antivirus" },
      "image_url": "https://cdn.example.com/antivirus.png",
      "from_price": "29.00",
      "availability": "unlimited",
      "variants": [
        { "sku": "AV-PRO-12M", "label": "12 months", "kind": "digital",
          "price": "29.00", "compare_at_price": "49.00", "availability": "unlimited" },
        { "sku": "AV-PRO-24M", "label": "24 months", "kind": "digital",
          "price": "49.00", "compare_at_price": null, "availability": "unlimited" }
      ]
    }
  ],
  "currency": {
    "code": "USD", "symbol": "$", "decimal_places": 2,
    "secondary": {
      "code": "VES", "symbol": "Bs.", "decimal_places": 2,
      "rate": "36.50000000", "rate_as_of": "2026-10-09T12:05:00Z", "rate_source": "fetched"
    }
  }
}
```

- `price` is the product's unit price, in the primary currency. `from_price` is
  the lowest variant price.
- `compare_at_price` is the struck-through price. It is `null` unless it is higher
  than `price`, so a price that rises past it never shows a nonsensical
  "discount".
- `sku` identifies the variant's product.
- `availability` is `in_stock`, `low_stock`, `out_of_stock` or `unlimited`
  (the product does not track inventory). It is **a label, never a count**:
  exact stock is not published. A listing is as available as its most available
  variant.
- `image_url` is absolute.

### `GET /api/v1/storefront/catalog/listings/{slug}/`

One listing in the same shape, plus `description`, `features`, `attributes` and
`currency`. `404` if it is unpublished or has no variant on sale.

### `GET /api/v1/storefront/catalog/categories/`

The categories that lead to at least one visible listing, nested, with empty
branches left out. `listing_count` includes descendants.

```json
{
  "results": [
    { "id": 1, "name": "Software", "description": "", "listing_count": 3,
      "children": [
        { "id": 3, "name": "Antivirus", "description": "", "listing_count": 2, "children": [] }
      ] }
  ],
  "currency": { "...": "..." }
}
```

### `GET /api/v1/storefront/payment-instructions/`

Where a customer is told to pay: the primary active recipient profile for each
payment method, at most one per method, in the same customer-safe shape the kiosk
shows. This response is **not cached** (`Cache-Control: no-store`), so a change
of account takes effect at once.

```json
{
  "results": [
    { "payment_method": "mobile_payment", "payment_method_display": "Mobile Payment",
      "bank": "Banco de Venezuela", "phone": "0414-1234567",
      "account_number": "", "document_id": "J-12345678-9" }
  ],
  "currency": { "...": "..." }
}
```

## Currency

`currency` is the configuration in force when the response was produced:
the primary currency and, when one is configured, the secondary currency with its
exchange rate and where that rate came from. Use it to show an indicative amount
in the secondary currency. It is **indicative**: it follows the live rate and the
store may hold it for up to a minute.

## Customers

The store names each customer with an `external_ref` and sends their profile with
every order:

```json
"customer": {
  "external_ref": "user-42",
  "email": "ana@example.com",
  "email_verified": true,
  "first_name": "Ana",
  "last_name": "Pérez",
  "phone": "0414-5550000"
}
```

`external_ref` is at most 128 characters of letters, digits and `. : @ + - _`.
RetailOps decides which customer an order belongs to like this:

1. **A customer the channel has ordered for before** is the same customer, always,
   whatever email the store sends later. What the store sends is kept for support
   and never written over the RetailOps customer.
2. **A new customer** whose email is unknown to RetailOps becomes a new customer.
3. **A new customer whose email already belongs to a RetailOps customer** (someone
   who registered at a physical store or a kiosk) is linked to that customer only
   if the channel is **trusted to vouch for emails** *and* the order says
   `email_verified: true`. Otherwise the order is refused with
   `409 customer_email_conflict`.

A national id is never used to link anyone, a RetailOps customer is never
overwritten, and the conflict response says nothing about the customer it
collided with. Trusting a channel is a setting on the channel in the admin
(**Trusts email verification**, off by default): turn it on only for a store that
really verifies addresses, because it lets that store place orders on the
customer who owns the address.

## Orders and payments

### Create an order

```http
POST /api/v1/storefront/orders/
Authorization: ChannelKey <secret>
```

```json
{
  "client_reference": "WEB-1001",
  "customer": { "external_ref": "user-42", "email": "ana@example.com",
                "email_verified": true, "first_name": "Ana", "last_name": "Pérez" },
  "items": [ { "sku": "AV-PRO-12M", "quantity": 1 } ],
  "fulfillment": "digital",
  "contact": { "whatsapp": "0414-5550000" }
}
```

- `client_reference` is the store's own order id and the **idempotency key**. The
  same reference from the same channel returns the same order with `200` instead
  of creating a second one, so a retry after a timeout is safe. Reusing a
  reference for a different cart or customer is `409 client_reference_conflict`.
  A cancelled order keeps its reference: a new attempt needs a new one.
- `items` name products by `sku`, as the catalogue shows them. Repeated SKUs are
  summed. Only products in a **published listing on an active product** can be
  ordered; any other SKU is `400 invalid_product`. At most 50 lines and 1,000 of
  one product.
- `fulfillment` is `digital` or `pickup`. `contact` is up to ten short text
  entries saying where to reach the buyer about this order.

The order is created, confirmed and its stock reserved in one short transaction,
and nothing is left behind if it fails. It answers `201`:

```json
{
  "order_number": "SO-20261011-0007",
  "client_reference": "WEB-1001",
  "customer_status": "awaiting_payment",
  "created_at": "2026-10-11T14:02:11Z",
  "expires_at": "2026-10-11T15:02:11Z",
  "fulfillment": "digital",
  "contact": { "whatsapp": "0414-5550000" },
  "items": [ { "sku": "AV-PRO-12M", "name": "Antivirus Pro 12 months", "quantity": 1,
               "unit_price": "29.00", "line_total": "29.00" } ],
  "total": "29.00",
  "amount_paid": "0.00",
  "amount_outstanding": "29.00",
  "quote": {
    "currency": { "code": "USD", "symbol": "$", "decimal_places": 2,
                  "secondary": { "code": "VES", "symbol": "Bs.", "decimal_places": 2,
                                 "rate": "36.50000000", "rate_as_of": "2026-10-09T12:05:00Z",
                                 "rate_source": "fetched" } },
    "total": "29.00",
    "secondary_total": "1058.50"
  },
  "payments": [],
  "payment_instructions": [
    { "payment_method": "mobile_payment", "payment_method_display": "Mobile Payment",
      "bank": "Banco de Venezuela", "phone": "0414-1234567",
      "account_number": "", "document_id": "J-12345678-9" }
  ]
}
```

Show the customer `quote.secondary_total` and the `payment_instructions`. The
quote **does not move**: it is the rate at the moment the order was created.

If a product has too little stock the order is `409 insufficient_stock`, listing
the SKUs that fell short and how many were asked for. It never says how many are
left.

### Pay it

```http
POST /api/v1/storefront/orders/SO-20261011-0007/payments/
```

```json
{
  "payment_method": "mobile_payment",
  "receipt": {
    "reference": "000123",
    "paid_on": "2026-10-11",
    "origin_bank": "BDV",
    "origin_phone": "04121234567",
    "receipt_image_base64": "data:image/png;base64,iVBORw0KGgo..."
  }
}
```

`payment_method` is `mobile_payment` or `bank_transfer`. `reference`, `paid_on`,
`origin_bank` and the image are required; the image is JPEG, PNG, HEIC or HEIF,
base64 or a `data:` URL.

RetailOps reads the image through OCR and compares it with what the store sent
and with the amount owed **at the quoted rate**, so a change of the exchange rate
since the order was created does not matter. The response is the order, `201`:

| What happened | `payments[].review_status` | Order |
|---------------|----------------------------|-------|
| OCR read the receipt and it matched | `verified` | paid (`customer_status: processing`) |
| OCR could not decide: it is off, or VEPay was unavailable, or it found no transaction key | `needs_review` | waits for a person (`payment_under_review`) |
| The receipt does not match, was already used, or cannot be read | no payment is recorded | unchanged; the error says why |

The store can never vouch for its own receipt. A transaction key, an OCR reading
or an amount in the request is ignored: only RetailOps's own reading of the image
counts. A `needs_review` payment is approved or rejected by a Manager in the
back-office; the customer sees `verified` or `rejected` and, after a rejection,
may send another receipt. While one payment is under review a second is refused
(`409 payment_under_review`). A paid, cancelled or otherwise closed order takes no
payment (`409 order_not_payable`, with its `customer_status`).

For an internet channel, switch on **recipient validation** in the settings, so a
receipt is only accepted when the money went to one of your own accounts.

### Follow an order

| Request | Returns |
|---------|---------|
| `GET /api/v1/storefront/orders/{order_number}/` | The order, as above without `payment_instructions` |
| `GET /api/v1/storefront/customers/{external_ref}/orders/` | That customer's orders for this channel, newest first, paged; empty for a customer with none |

A channel only ever reaches its own orders: another channel's order number is a
`404`, the same as one that does not exist. These responses are never cached.

`customer_status` is one word for what to show the customer:

| Value | Meaning |
|-------|---------|
| `awaiting_payment` | Created and unpaid, or its only receipt was rejected |
| `payment_under_review` | A receipt is waiting for a person |
| `processing` | Paid; being prepared |
| `completed` | Delivered |
| `cancelled` | Cancelled, including by expiring unpaid |
| `refunded` | Refunded |

Each payment reads as `verified`, `needs_review` or `rejected`. Receipt images,
OCR readings, bank transaction keys and the reviewer's notes are never returned.

### Unpaid orders expire

An unpaid order holds its stock until `expires_at`: the channel's **Order expiry
minutes** after creation (60 by default, at least 5, set in the admin). Schedule
this every few minutes to release it:

```bash
python manage.py expire_channel_orders
```

It cancels an order that lapsed unpaid and returns its stock. **An order with any
payment is never expired**, not even one whose receipt was rejected: a person has
touched it, so a person closes it. Running it twice is harmless, and `--dry-run`
only counts. `expires_at` is when an order *becomes eligible* for cancelling: a
payment that arrives before the command cancels the order is accepted.

### Errors

| Status | `code` | When |
|--------|--------|------|
| `400` | `validation_error` | A field is missing or malformed |
| `400` | `invalid_product` | A SKU is not in a published listing, or its product is inactive |
| `400` | `invalid_quantity` | More than 1,000 of one product |
| `400` / `413` / `415` | `invalid_receipt_image`, `receipt_too_large`, `unsupported_receipt_type` | The image cannot be used |
| `404` | `not_found` | No such order for this channel |
| `409` | `insufficient_stock` | Stock fell short; `insufficient` lists `sku` and `requested` |
| `409` | `customer_email_conflict` | The email belongs to a customer this channel cannot claim |
| `409` | `client_reference_conflict` | The reference was used for a different order |
| `409` | `order_not_payable` | The order cannot take a payment; `customer_status` says why |
| `409` | `payment_under_review` | A receipt for this order is already waiting |
| `409` | `duplicate_transaction` | This bank transaction was already recorded |
| `422` | `receipt_field_mismatch`, `recipient_mismatch`, `amount_mismatch`, `receipt_parse_error`, … | The receipt does not match, or cannot be read |

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `401` on every call | Wrong scheme (it is `ChannelKey`, not `Bearer` or `Token`), a revoked key, or an inactive channel |
| A product is missing from the list | Its listing is unpublished, the product is inactive, or it was never published to the store |
| `availability` is `unlimited` for a physical product | Its product has **Track inventory** turned off |
| A price looks stale | The store's own cache; the catalogue is cacheable for 60 seconds |
| `429` | The store polls too often; cache the catalogue instead |
| `409 customer_email_conflict` for a returning shopper | Their email is already a RetailOps customer; enable **Trusts email verification** for the channel if the store verifies emails, and send `email_verified: true` |
| A receipt is `needs_review` every time | OCR is off for the payment method, VEPay is unreachable, or the receipt shows no transaction key |
| Orders never release their stock | `expire_channel_orders` is not scheduled, or the orders have a payment |
