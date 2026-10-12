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
- **A paid order delivers what it sold.** Licence keys, links and instructions are
  recorded per order line, released when the order is paid, and revoked by a refund.
  A key is stored encrypted and read back only by one explicit call (see
  [Digital delivery](#digital-delivery)).
- **The store can be told when an order changes.** Every change a store cares about
  (a payment approved or rejected, an order cancelled or refunded, a key issued) is
  recorded as an event on a feed the store can read, and RetailOps can also post each
  event to an address the store registered, signed so the store knows it came from
  RetailOps (see [Events and webhooks](#events-and-webhooks)). An event never carries a
  customer, an amount or a key: it says which order to read again.

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
| `429` | The channel exceeded its allowance, counted per channel: 300 catalogue and query requests a minute, 60 order creations and cancellations, 30 payments (each can cost an OCR call) |

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
| `channels` | The channels allowed to see it. **Empty means every channel**; naming some restricts the listing to them. A channel can neither list, retrieve nor order a listing that is not its own (it is a `404` / `400 invalid_product`, the same as one that does not exist), so stores with different assortments can share one RetailOps |

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
      ],
      "features": ["Real-time protection", "Secure browsing"],
      "attributes": { "platform": "Windows & macOS", "activation": "License key" }
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
- `features` (a list of short selling points) and `attributes` (free-form details such as
  platform or activation) come with every listing, so a card needs no second call.
  `description` is only on the detail.

### `GET /api/v1/storefront/catalog/listings/{slug}/`

One listing in the same shape, plus `description` and `currency`. `404` if it is
unpublished, has no variant on sale, or is published only to other channels.

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
               "unit_price": "29.00", "line_total": "29.00",
               "listing_slug": "antivirus-pro", "listing_title": "Antivirus Pro",
               "variant_label": "12 months" } ],
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
  "entitlements": [],
  "payment_instructions": [
    { "payment_method": "mobile_payment", "payment_method_display": "Mobile Payment",
      "bank": "Banco de Venezuela", "phone": "0414-1234567",
      "account_number": "", "document_id": "J-12345678-9" }
  ]
}
```

Show the customer `quote.secondary_total` and the `payment_instructions`. The
quote **does not move**: it is the rate at the moment the order was created.

Each line also names the listing and variant it was sold from (`listing_slug`,
`listing_title`, `variant_label`), so an order page can link back to the product and say
"12 months". They are `null` for a product that has since left its listing.

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

### Cancel an order

```http
POST /api/v1/storefront/orders/SO-20261011-0007/cancel/
```

A store whose customer changes the cart after the order exists would otherwise strand that
stock until `expires_at`. Cancelling an unpaid order gives its stock back at once and returns
the order with `customer_status: cancelled`.

- Only an order with **no payment at all** can be cancelled. One with any payment, even a
  rejected one, or that is no longer confirmed, is `409 order_not_cancellable` (with its
  `customer_status`): a person settles it, as with an expiring order.
- Cancelling an order that is already cancelled changes nothing and returns it, so a retry is
  safe.
- The `client_reference` stays used: a new attempt needs a new reference. Sending the old one
  again returns the cancelled order.
- It counts with order creation (60 a minute), not with reads.

### Follow an order

| Request | Returns |
|---------|---------|
| `GET /api/v1/storefront/orders/{order_number}/` | The order, as above without `payment_instructions` |
| `GET /api/v1/storefront/customers/{external_ref}/orders/` | That customer's orders for this channel, newest first, paged; empty for a customer with none |

A channel only ever reaches its own orders: another channel's order number is a
`404`, the same as one that does not exist. These responses are never cached.

**Hold a call to one customer.** RetailOps knows the channel, not the store's customers, so an
order number is enough to read any of the channel's orders. A store can add
`?customer=<external_ref>` to the order, payments, cancel, entitlement, supply and reveal calls:
an order or entitlement that is not that customer's is then a `404`, so a store that mixes up two
customers fails closed instead of showing one customer another's order or licence. A parameter
that is present but empty matches nothing. Without it nothing changes.

`customer_status` is one word for what to show the customer:

| Value | Meaning |
|-------|---------|
| `awaiting_payment` | Created and unpaid, or its only receipt was rejected |
| `payment_under_review` | A receipt is waiting for a person |
| `processing` | Paid; being prepared |
| `completed` | Delivered, or paid and fully delivered digitally (see [Digital delivery](#digital-delivery)) |
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

### Digital delivery

What a customer is given when they pay (a licence key, a download link, setup
instructions) is delivered by RetailOps and recorded as an **entitlement**.

**What is delivered is set per product**, in the Django admin, under the product's
**Product fulfillment**:

| Setting | Meaning |
|---------|---------|
| Mode | **Fixed text**: a link or instructions, the same for everyone, released the moment the order is paid. **Supplied**: nothing is released until a value is provided for that one order |
| Kind | `key`, `link` or `instructions`. A key cannot be fixed text: every customer would receive the same one |
| Template | The link or instructions to release, for fixed text. A link must be an `http(s)` URL |
| Duration days | How long it lasts from the moment it is delivered; empty means it does not expire |
| Per unit | One entitlement per unit ordered; otherwise one for the whole line, covering its quantity (`seats`) |

A product with no fulfillment delivers nothing. There is no pool of keys: each value
is provided for one entitlement and belongs to it.

**Entitlements exist only for paid orders.** They are created at the moment an order
becomes paid, never before, so a cancelled or expired order leaves none. Fixed text is
`active` at once. A supplied one is `pending` until its value arrives, from **staff**
(the order page in the back-office has a Deliverables card) or from **the store**,
which can issue its own keys and hand them over (below). Its term, `expires_at`,
counts from that moment. A refund **revokes** everything the order delivered, in the
same transaction.

Every order carries them:

```json
"entitlements": [
  { "id": 41, "sku": "AV-PRO-12M", "kind": "key", "status": "active", "seats": 1,
    "value": "••••••••K3F9", "activated_at": "2026-10-11T14:09:02Z", "expires_at": "2027-10-11T14:09:02Z" },
  { "id": 42, "sku": "AV-GUIDE", "kind": "link", "status": "active", "seats": 1,
    "value": "https://downloads.example.com/guide", "activated_at": "2026-10-11T14:09:02Z", "expires_at": null },
  { "id": 43, "sku": "AV-PRO-24M", "kind": "key", "status": "pending", "seats": 1,
    "value": null, "activated_at": null, "expires_at": null }
]
```

`status` is `pending`, `active` or `revoked`. `value` is the text of a link or
instructions, `null` until supplied, and **always masked for a licence key**: eight
dots and its last four characters.

`customer_status` is `completed` for a paid order once everything it sold has been
delivered: every line has entitlements and all are `active`. A line that delivers
nothing (a physical product, or one with no fulfillment) means delivery alone does not
finish the order, which stays `processing` until it is marked delivered. A revoked
entitlement keeps an order from reading as complete.

**Supply a value** (the store issues the key itself):

```http
PUT /api/v1/storefront/entitlements/43/
```

```json
{ "value": "ABCD-1234-EFGH-5678" }
```

Only a `pending` entitlement of the channel's own orders can be supplied: it becomes
`active` and its term starts now. An active or revoked one is `409`.

**Read a whole key:**

```http
POST /api/v1/storefront/entitlements/41/reveal/
```

```json
{ "id": 41, "kind": "key", "value": "ABCD-1234-EFGH-5678", "revealed_at": "2026-10-11T14:12:40Z" }
```

This is the one place a key is ever read back. It is a **POST** because it changes
state, not a read: the *first* reveal is recorded as `revealed_at`, which is the
evidence if the customer later disputes a refund, and a repeat never overwrites it.
Being a POST it cannot be triggered by a prefetch or a link. Only an `active`
entitlement can be revealed, the response is never cached, and a channel is limited to
60 supplies and reveals a minute. An expired entitlement can still be read.

**How a key is kept.** It is stored encrypted (Fernet) with keys from the
`ENTITLEMENT_ENCRYPTION_KEYS` environment variable, a comma-separated list: the first
encrypts and every one decrypts. Everything that lists or shows an entitlement uses a
four-character hint stored in clear and never decrypts a key, so a listing cannot leak
one. Staff can supply or replace a key but never read it: the back-office, the admin,
the staff API and the MCP server show it masked or not at all. Set
`ENTITLEMENT_ENCRYPTION_KEYS` before the first key is stored; `manage.py check` warns
when it is missing, and without it a key is refused (links and instructions are not
secret and do not need it). To retire an encryption key, put the new one **first**,
deploy, run `python manage.py rotate_entitlement_keys`, and only then drop the old one.

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
| `409` | `order_not_cancellable` | The order has a payment or is no longer confirmed; `customer_status` says why |
| `409` | `payment_under_review` | A receipt for this order is already waiting |
| `409` | `duplicate_transaction` | This bank transaction was already recorded |
| `422` | `receipt_field_mismatch`, `recipient_mismatch`, `amount_mismatch`, `receipt_parse_error`, … | The receipt does not match, or cannot be read. The answer says which of your fields did not match (`details.mismatches`) and never what the OCR read |
| `400` | `invalid_value` | A value to supply is blank, too long (2,000 characters) or, for a link, not an `http(s)` URL |
| `409` | `entitlement_not_pending` | Only a pending entitlement can be supplied |
| `409` | `entitlement_revoked` | A revoked entitlement can be neither supplied nor revealed |
| `409` | `entitlement_not_active` | Only an active entitlement can be revealed; `entitlement_status` says what it is |
| `503` | `encryption_not_configured`, `decryption_failed` | The server cannot store or read a key right now; retry later and tell the operator |

A refused receipt never reveals what it showed: not the reference, the amount or the bank transaction it read.
The answers name only the store's own fields that did not match, so the endpoint cannot be used to tune a
forged picture. Show your customer something like "the details do not match your receipt", not the reading.

## Events and webhooks

A store can ask RetailOps on a timer whether an order changed, or be told. Both rest on one
thing, a **feed of events** per channel. A **webhook** is a convenience on top of it that
delivers each event the moment it is recorded; the feed is how a store that was down, or lost a
delivery, catches up. So a store that uses webhooks should also read the feed, and should treat
a webhook as a nudge, not as the truth: it says *which order to read again*.

### What an event says

| `type` | When | `data` |
|--------|------|--------|
| `payment.updated` | A payment of the order is recorded, approved or rejected | `order_number`, `payment_number`, `review_status` (`verified`, `needs_review` or `rejected`) |
| `order.updated` | The order's `customer_status` changed | `order_number`, `client_reference`, `customer_status`, `reason` |
| `entitlement.updated` | A delivery of the order became `pending`, `active` or `revoked` | `order_number`, `entitlement_id`, `sku`, `kind`, `status` |

`reason` says why an order's status changed, for a mail or a log line: `paid`,
`payment_under_review`, `payment_rejected`, `shipped`, `delivered`, `cancelled`, `expired` or
`refunded`. An event carries **no customer, no amount, no key and no receipt reading**; re-read
the order (`GET /orders/{order_number}/`) and act on that.

Events are *differences*: RetailOps remembers what each order's store was last told and records one
event for each thing that is different now, from the finished state, in the same transaction as
the change. Approving a payment that pays the order is one `payment.updated`, one `order.updated`
(`processing`, reason `paid`) and one `entitlement.updated` for each delivery, in that order. A
change that is rolled back leaves no event, and a change that commits always has one.

Each event has a `sequence`: a number that belongs to the channel, **has no gaps** and follows commit
order, so a store that has seen event 41 can never see 40 later.

### Read the feed

```http
GET /api/v1/storefront/events/?after=41&limit=100
```

```json
{
  "results": [
    { "sequence": 42, "type": "payment.updated", "created_at": "2026-10-12T12:00:00Z",
      "data": { "order_number": "SO-20261012-0001", "payment_number": "PAY-20261012-0003", "review_status": "verified" } },
    { "sequence": 43, "type": "order.updated", "created_at": "2026-10-12T12:00:00Z",
      "data": { "order_number": "SO-20261012-0001", "client_reference": "WEB-1", "customer_status": "processing", "reason": "paid" } }
  ],
  "next_after": 43,
  "has_more": false
}
```

- Oldest first. `after` is the last `sequence` the store processed (`0`, the default, starts at the
  beginning); `limit` is 1 to 500 (100 by default). A value that is not a whole number in range is `400`.
- Store `next_after` and pass it next time; while `has_more` is true there is more to read now.
- A channel only ever reads its own events. The response is never cached, and the call counts with
  the other reads (300 a minute).
- Events are kept for 30 days (`manage.py prune_channel_events --days 30`, daily, or as the operator
  decides). A store that has been away longer should re-read the orders it still cares about.

### Receive webhooks

RetailOps posts one event per request:

```http
POST https://shop.example/api/retailops/webhook
Content-Type: application/json
X-RetailOps-Event: order.updated
X-RetailOps-Delivery: 118
X-RetailOps-Timestamp: 1800000000
X-RetailOps-Signature: v1=d86d8d0931486a66868f19d1b0cdb6e49e404e94b6bf33f7d8c6857025878704
```

```json
{"channel":"trend-store","created_at":"2026-10-12T12:00:00Z","data":{"order_number":"SO-20261012-0001"},"sequence":7,"type":"order.updated"}
```

The body is compact JSON with sorted keys, the same bytes on every attempt. The signature is the
hex of `HMAC-SHA256(secret, "<timestamp>.<raw body>")`, where `secret` is the channel's signing
secret (it begins `whsec_`) and `timestamp` is the header, in seconds, **of this attempt**: a retry
an hour later carries a new timestamp and a new signature. While a secret is being rotated the header
holds two, `v1=<new>,v1=<old>`.

A receiver should:

1. **Read the raw body before parsing it**; the signature covers those exact bytes.
2. **Compare in constant time** the expected signature (for each secret it holds) with each
   `v1=` value, and accept if any matches. Anything else is `401`, with no effect.
3. **Reject an old timestamp**: more than 300 seconds from its own clock is a replay (`401`).
4. **Ignore what it already handled**: delivery is *at least once*, so the same `sequence` can
   arrive twice, and two events can arrive out of order. A repeat is `200`, doing nothing.
5. **Answer `2xx` quickly** (RetailOps waits 8 seconds). Anything else, a redirect included, is a failure.
6. **Re-read the order** and act on what it says, not on the payload.
7. **Notice a gap.** If the `sequence` it receives is more than one past the last one it processed,
   read the feed from the last one.

```python
import hashlib, hmac, time

def verify(secrets, signature_header, timestamp, raw_body, tolerance=300):
    if abs(time.time() - int(timestamp)) > tolerance:
        return False
    message = f"{timestamp}.".encode() + raw_body
    given = [part[3:] for part in signature_header.split(",") if part.startswith("v1=")]
    return any(
        hmac.compare_digest(hmac.new(s.encode(), message, hashlib.sha256).hexdigest(), g)
        for s in secrets for g in given
    )
```

A test vector for any language: secret `whsec_test_secret_1234567890`, timestamp `1800000000` and
the body

```
{"channel":"trend-editorial","created_at":"2026-10-12T12:00:00Z","data":{"order_number":"SO-20261012-0001"},"sequence":7,"type":"order.updated"}
```

sign to `d86d8d0931486a66868f19d1b0cdb6e49e404e94b6bf33f7d8c6857025878704`.

### What RetailOps does when the store does not answer

| Attempt | Waits before the next |
|---------|-----------------------|
| 1st failure | 30 seconds |
| 2nd | 2 minutes |
| 3rd | 10 minutes |
| 4th | 1 hour |
| 5th | 6 hours |
| 6th | gives up on that event (it stays on the feed) |

An address that fails **ten deliveries in a row** is switched off, and nothing more is sent until an
administrator switches it back on (Django admin → **Channel webhooks** → *Switch back on*), which
sends what waited. A success starts the count again. Every delivery is kept in the admin (**Webhook
deliveries**): how often it was tried, the status and the first 300 characters of what the receiver
said. *Try again now* queues a delivery that gave up.

### Where a webhook may point

An address is checked every time it is used, not only when it is registered. It must be `https`,
carry no credentials, and **every address its name resolves to must be a public one**; the machine the
connection actually reaches is checked too, because a name can resolve differently the second time.
Redirects are not followed, an environment proxy is ignored, and waiting is bounded (3 seconds to
connect, 8 to read). Without this a webhook would be a way to make RetailOps fetch
`http://169.254.169.254/` for whoever can name an address.

For that reason **a channel cannot register its own address**: it is an operator's act, in the
Django admin (**Channel webhooks → Add**) or

```bash
python manage.py register_channel_webhook --channel trend-store \
    --url https://shop.example/api/retailops/webhook
```

The signing secret is shown once, because only an encrypted copy is kept
(`ENTITLEMENT_ENCRYPTION_KEYS`, as for licence keys; `rotate_entitlement_keys` re-encrypts these
too). `--dev-vars FILE` writes it to a `.dev.vars` file as `RETAILOPS_WEBHOOK_SECRET` instead of printing it.
The command is idempotent; `--new-secret` starts a rotation and `--drop-previous` ends it.

**Rotate a signing secret without downtime.** Run **Rotate the signing secret** in the admin (or
`--new-secret`); RetailOps signs with both. Give the store the new one, let it accept either, and then
run **Clear the previous signing secret** (or `--drop-previous`).

### Run the delivery

Delivering is a loop that sends what is due. Run one of:

```bash
python manage.py deliver_channel_webhooks --interval 5   # a service, looking every 5 seconds
python manage.py deliver_channel_webhooks --once         # cron, every minute
```

One worker is enough and two are harmless: a delivery is claimed before it is sent, and one that a
dying worker left half way is taken again after a minute. Set
`CHANNEL_WEBHOOK_MAX_FAILURES` (default 10) to change when an address is switched off.
`CHANNEL_WEBHOOK_ALLOW_INSECURE=1` lets an address be `http` and on this machine; it is for
development only and `retailops.demo_settings` turns it on.

## Try it locally

To demonstrate the flow, or to develop a storefront against RetailOps, use a **scratch
database**, never `db.sqlite3`:

```bash
export DB_NAME=.demo/demo.sqlite3 MEDIA_ROOT=.demo/media    # PowerShell: $env:DB_NAME = ...
python manage.py migrate
python manage.py init --demo --no-input --yes                 # documented demo users
python manage.py configure_demo_site --ocr-url http://127.0.0.1:9100 --rate 36.50
python manage.py import_storefront_catalog --catalog store.json \
    --dev-vars ../store/.dev.vars --base-url http://127.0.0.1:8010
python manage.py register_channel_webhook --channel my-store \
    --url http://127.0.0.1:3001/api/retailops/webhook --dev-vars ../store/.dev.vars
DJANGO_SETTINGS_MODULE=retailops.demo_settings python manage.py runserver 127.0.0.1:8010
DJANGO_SETTINGS_MODULE=retailops.demo_settings python manage.py deliver_channel_webhooks --interval 2   # another terminal
```

- **`configure_demo_site`** sets what the flow needs and nothing else: dollars as the primary
  currency and bolivares as the secondary one at a fixed rate; receipt OCR on for both receipt
  methods, pointing at a VEPay stand-in; one account to pay into per method with **recipient
  validation on**; and `kiosk_trust_client_evidence` off. It is idempotent.
- **`import_storefront_catalog`** loads a catalogue from a JSON file (the format is documented in
  the command's own docstring) and provisions the channel the file names. It matches products by
  SKU and listings by slug, so a second run updates and creates nothing twice. Every listing it
  imports is published to that channel only. The key is printed once, or written to `--dev-vars`
  (merged into the file, without a byte-order mark, and never overwritten by a later run unless
  `--new-key` is given). `--dry-run` applies everything and rolls it back.
- **Both commands refuse to run against `db.sqlite3`**, the default development database, unless
  `--allow-default-db` is given: they write demonstration settings and sample data.
- **`retailops.demo_settings`** is the normal settings with the per-channel storefront limits raised,
  because an automated run makes far more calls in a minute than a shop, with
  `CHANNEL_WEBHOOK_ALLOW_INSECURE` on so a webhook can be `http` and point at this machine, and with a
  SQLite backend that begins every transaction with `BEGIN IMMEDIATE` (`retailops.demo_db`). Three storefronts, their
  webhooks and the delivery worker all write to one SQLite file, and Django 4.2's plain `BEGIN` makes
  a transaction that read first and wrote second fail with "database is locked" the moment another
  one committed in between; asking for the write lock up front makes it wait its turn instead (up to 30
  seconds). Put the scratch database in WAL mode as well (`pragma journal_mode=wal`, which
  `tools/demo/start-demo.ps1` in the Trend Store repository does) so readers do not wait for writers.
  Nothing else changes.
- **`register_channel_webhook`** also needs `ENTITLEMENT_ENCRYPTION_KEYS` (the demonstration settings
  derive one in development), because the signing secret is stored encrypted.
- A **VEPay stand-in** (any service that answers `POST /v1/receipts/parse` and `GET /healthz`) lets
  receipts verify without Tesseract; the Trend Store repository ships one in `tools/demo/`, with a
  generator for matching, mismatching, duplicate and unreadable receipts.

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
| A paid digital order stays `processing` | A key is still `pending`: supply it, or a line delivers nothing |
| `500 server_error` on creating an order, only when two requests for the same cart arrive together | The scratch database is SQLite, which cannot take two writers at once (`database is locked`). The order was not created twice; ask again with the **same** `client_reference`, which returns the one order. Production databases do not do this |
| `503 encryption_not_configured` when supplying a key | `ENTITLEMENT_ENCRYPTION_KEYS` is not set on the server |
| `503 decryption_failed` on reveal | The key was sealed with an encryption key no longer listed in `ENTITLEMENT_ENCRYPTION_KEYS` |
| Webhooks stopped arriving | The address was switched off after ten failures in a row (**Channel webhooks** in the admin shows when and why), or `deliver_channel_webhooks` is not running |
| A webhook is refused as `401` by the store though RetailOps sent it | The body was parsed and re-serialised before verifying (sign the raw bytes), the store's clock is more than 5 minutes off, or it still holds only the old secret after a rotation |
| A delivery says `Not sent: The address does not resolve to a public address.` | The address names this machine or a private network, which production refuses; use a public `https` address (`CHANNEL_WEBHOOK_ALLOW_INSECURE` is for development only) |
| A delivery says `the signing secret could not be read` | The secret was sealed with an encryption key no longer listed in `ENTITLEMENT_ENCRYPTION_KEYS`; rotate the signing secret in the admin |
| The store sees the same event twice, or out of order | That is allowed: deduplicate by `sequence` and re-read the order |
