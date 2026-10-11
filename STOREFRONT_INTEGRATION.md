# RetailOps Storefront Integration

RetailOps can feed an online store or a web catalogue. The store is a **sales
channel**: a server-to-server integration that authenticates with a secret key
and reads the catalogue RetailOps publishes for it. This document covers
provisioning a channel and the catalogue endpoints. The endpoints are listed in
`API_GUIDE.md` and described in the OpenAPI schema (`/api/v1/schema/`, tag
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
- **RetailOps does not know the store's customers.** Nothing in the catalogue
  identifies a customer or an order.

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
| `429` | The channel exceeded its allowance: 300 requests per minute, counted per channel |

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

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `401` on every call | Wrong scheme (it is `ChannelKey`, not `Bearer` or `Token`), a revoked key, or an inactive channel |
| A product is missing from the list | Its listing is unpublished, the product is inactive, or it was never published to the store |
| `availability` is `unlimited` for a physical product | Its product has **Track inventory** turned off |
| A price looks stale | The store's own cache; the catalogue is cacheable for 60 seconds |
| `429` | The store polls too often; cache the catalogue instead |
