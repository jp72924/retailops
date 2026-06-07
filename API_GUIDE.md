# RetailOps API Guide

A complete reference for the RetailOps REST API, intended for developers integrating external systems or building client applications against it.

**Base URL:** `http://<host>/api/v1/`  
**Interactive docs:** `/api/v1/schema/swagger/` (Swagger UI) · `/api/v1/schema/redoc/` (ReDoc) · `/api/v1/schema/` (raw OpenAPI YAML)

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Conventions](#2-conventions)
   - [Request Format](#21-request-format)
   - [Pagination](#22-pagination)
   - [Filtering, Searching & Ordering](#23-filtering-searching--ordering)
   - [Error Responses](#24-error-responses)
   - [Authorization Roles](#25-authorization-roles)
   - [Rate Limiting](#26-rate-limiting)
3. [Step-by-Step Workflow Guide](#3-step-by-step-workflow-guide)
4. [Endpoint Reference](#4-endpoint-reference)
   - [Auth](#41-auth)
   - [Dashboard](#42-dashboard)
   - [Roles](#43-roles)
   - [Users](#44-users)
   - [Customers](#45-customers)
   - [Categories](#46-categories)
   - [Products](#47-products)
   - [Inventory](#48-inventory)
   - [Orders](#49-orders)
   - [Payments](#410-payments)
   - [Settings](#411-settings)
   - [MCP Skill Card](#412-mcp-skill-card)
5. [Object Schemas](#5-object-schemas)

---

## 1. Getting Started

### Obtain a Token

All endpoints except token issuance require authentication. Send your credentials once to receive a token, then include it in every subsequent request.

**Request:**
```http
POST /api/v1/auth/token/
Content-Type: application/json

{
  "email": "manager@retailops.local",
  "password": "ManagerPass123!"
}
```

**Response `200 OK`:**
```json
{
  "token": "9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b",
  "user_id": 2,
  "email": "manager@retailops.local",
  "role_name": "Manager"
}
```

Store the `token` value. Include it in every subsequent request as an HTTP header:

```http
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

### Make Your First Request

```http
GET /api/v1/dashboard/
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

```json
{
  "orders_this_month": 14,
  "revenue_this_month": "7240.50",
  "pending_payments_count": 3,
  "low_stock_count": 2,
  "recent_orders": [ ... ]
}
```

### Revoke a Token (Logout)

```http
POST /api/v1/auth/token/revoke/
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

Response: `204 No Content`. The token is deleted server-side; discard it on the client.

---

## 2. Conventions

### 2.1 Request Format

- All request bodies must be JSON with `Content-Type: application/json`.
- All responses are JSON.
- Datetime fields are ISO 8601 UTC strings: `"2026-04-15T14:30:00Z"`.
- Decimal fields (prices, amounts) are strings to preserve precision: `"49.99"`.

### 2.2 Pagination

All list endpoints return paginated results. The response envelope is:

```json
{
  "count": 150,
  "next": "http://host/api/v1/customers/?page=3",
  "previous": "http://host/api/v1/customers/?page=1",
  "results": [ ... ]
}
```

| Parameter | Default | Maximum | Description |
|-----------|---------|---------|-------------|
| `page` | 1 | — | Page number |
| `page_size` | 25 | 100 | Items per page |

**Example:** `GET /api/v1/products/?page=2&page_size=50`

### 2.3 Filtering, Searching & Ordering

Filters, search terms, and ordering are passed as query parameters. Each resource documents its supported parameters in the [Endpoint Reference](#4-endpoint-reference).

**Filtering** narrows the result set by exact field values or ranges:
```
GET /api/v1/orders/?status=confirmed&date_from=2026-04-01&date_to=2026-04-30
```

**Searching** performs a case-insensitive substring match across multiple fields:
```
GET /api/v1/products/?search=widget
```

**Ordering** sorts results. Prefix a field name with `-` for descending order:
```
GET /api/v1/customers/?ordering=-created_at
GET /api/v1/products/?ordering=unit_price
```

Multiple filters can be combined freely:
```
GET /api/v1/products/?category=3&stock=low&ordering=name
```

### 2.4 Error Responses

Every error response from the API uses a consistent envelope:

```json
{
  "error": "Human-readable description.",
  "code":  "machine_readable_code",
  "details": {
    "field_name": ["Specific message for this field."]
  }
}
```

The `details` key is only present on validation errors (`code: "validation_error"`). Non-field validation errors appear under `"non_field_errors"`.

**Standard error codes:**

| HTTP Status | `code` | Meaning |
|-------------|--------|---------|
| 400 | `validation_error` | Request body failed validation |
| 401 | `not_authenticated` | No token provided |
| 401 | `authentication_failed` | Token invalid or revoked |
| 403 | `permission_denied` | Token valid but role insufficient |
| 404 | `not_found` | Resource does not exist |
| 405 | `method_not_allowed` | HTTP method not supported on this URL |
| 409 | `conflict` | Action not allowed in the current state |
| 409 | `wrong_status` | Order lifecycle action blocked by current status |
| 429 | `throttled` | Too many requests |
| 500 | `server_error` | Unexpected server error |

**Validation error example:**
```json
{
  "error": "Validation failed.",
  "code": "validation_error",
  "details": {
    "email": ["A customer with this email already exists."],
    "unit_price": ["Unit price must be greater than zero."]
  }
}
```

### 2.5 Authorization Roles

The API uses four roles. Three are for human users with escalating privileges; the fourth is reserved for kiosk terminals:

| Role | Capabilities |
|------|-------------|
| **Staff** | Read all data; create and submit orders; mark orders shipped/delivered |
| **Manager** | All Staff capabilities; manage products, categories, inventory; confirm and cancel orders |
| **Admin** | All Manager capabilities; manage users; issue refunds |
| **Kiosk** | Self-checkout terminal service accounts; read settings and product catalog; initiate checkout flow only |

Endpoints that say "any authenticated user" are accessible to Staff, Manager, and Admin roles.

> **Note:** The Kiosk role is reserved for `KioskStation` service accounts, which are created automatically when a kiosk terminal is provisioned. It cannot be assigned via `POST /api/v1/users/`.

### 2.6 Rate Limiting

| Caller type | Limit |
|-------------|-------|
| Unauthenticated (token-obtain only) | 20 requests / minute |
| Authenticated (global ceiling) | 600 requests / minute |

Certain endpoint categories have their own per-user scopes that apply in addition to the global ceiling:

| Scope | Limit | Endpoints |
|-------|-------|-----------|
| `password_reset` | 5 / minute | `POST /auth/password-reset/` and `/confirm/` |
| `password_change` | 10 / minute | `POST /users/{id}/change-password/` |
| `order_transition` | 60 / minute | submit, confirm, ship, deliver, cancel, refund, bulk-transition |
| `inventory_adjust` | 30 / minute | `POST /inventory/adjust/` and `/bulk-adjust/` |
| `kiosk_identify` | 60 / minute | Customer identity verification (kiosk flow) |
| `kiosk_scan` | 120 / minute | Product barcode scan (kiosk flow) |
| `kiosk_checkout` | 30 / minute | Checkout submission (kiosk flow) |
| `kiosk_poll` | 60 / minute | Status polling (kiosk flow) |

When throttled, the response is `429 Too Many Requests` with:
```json
{
  "error": "Request was throttled. Try again in 12 seconds.",
  "code": "throttled"
}
```

---

## 3. Step-by-Step Workflow Guide

This section walks through the complete lifecycle of a sales order from an integrator's perspective.

### Step 1 — Authenticate

```http
POST /api/v1/auth/token/
Content-Type: application/json

{"email": "staff@retailops.local", "password": "StaffPass123!"}
```

Save the returned `token`. All requests below include:
```
Authorization: Token <your-token>
```

---

### Step 2 — Look Up a Customer

```http
GET /api/v1/customers/?search=Jane
```

If no match, create one:

```http
POST /api/v1/customers/
Content-Type: application/json

{
  "first_name": "Jane",
  "last_name":  "Doe",
  "email":      "jane.doe@example.com",
  "phone":      "+1-555-0100",
  "address_line1": "123 Main St",
  "city":       "Springfield",
  "state":      "IL",
  "postal_code": "62701",
  "country":    "United States"
}
```

Note the returned `id` (e.g. `14`).

---

### Step 3 — Find Products to Order

```http
GET /api/v1/products/?is_active=true&stock=ok
```

Each product in the response includes `current_stock`, `is_low_stock`, and `is_out_of_stock`. Note the `id` values for the items you want to order.

---

### Step 4 — Create a Draft Order

Orders are created in **Draft** status. Supply the customer ID and at least one line item. If `unit_price` is omitted from a line item, the product's current catalogue price is used as the snapshot.

```http
POST /api/v1/orders/
Content-Type: application/json

{
  "customer_id": 14,
  "notes": "Urgent — please prioritise.",
  "discount_amount": "0.00",
  "tax_amount": "15.00",
  "items": [
    {"product_id": 7, "quantity": 3},
    {"product_id": 12, "quantity": 1, "unit_price": "89.99"}
  ]
}
```

Response `201 Created` — a full order object including the auto-generated `order_number` (e.g. `SO-20260415-0001`). The order is in `"status": "draft"`.

---

### Step 5 — Submit the Order for Review

Moving from Draft to Pending signals that the order is ready for a manager to review. Requires **Staff** role or above.

```http
POST /api/v1/orders/42/submit/
```

Response: the updated order object with `"status": "pending"`. No request body required.

---

### Step 6 — Confirm the Order

Confirmation requires **Manager** role. This step deducts stock by creating negative `InventoryMovement` records for each line item.

```http
POST /api/v1/orders/42/confirm/
```

Response: the updated order with `"status": "confirmed"` and `confirmed_at` populated.

**Guard:** returns `409 conflict / no_items` if the order has zero line items.

---

### Step 7 — Record a Payment

Payments can only be recorded against **Confirmed** orders. The order automatically transitions to **Paid** once the cumulative amount paid reaches or exceeds `total_amount`.

```http
POST /api/v1/payments/
Content-Type: application/json

{
  "sales_order": 42,
  "amount": "329.89",
  "payment_method": "bank_transfer",
  "reference_number": "TXN-98765",
  "notes": "Wire transfer received."
}
```

Response `201 Created` — includes the auto-generated `payment_number`. If `amount_paid >= total_amount`, the order status will have transitioned to `paid` in the same atomic operation.

**Payment methods:** `cash` · `bank_transfer` · `card` · `check` · `other`

---

### Step 8 — Ship the Order

Requires **Staff** role or above. The order must be in **Paid** status.

```http
POST /api/v1/orders/42/ship/
```

---

### Step 9 — Mark as Delivered

```http
POST /api/v1/orders/42/deliver/
```

The order is now in its terminal **Delivered** state.

---

### Alternate Paths

**Cancel a confirmed order** (Manager+, must be in `confirmed` status — not yet paid):
```http
POST /api/v1/orders/42/cancel/
```
Stock is restored via positive `InventoryMovement` records.

**Refund a paid order** (Admin only, must be in `paid` status):
```http
POST /api/v1/orders/42/refund/
```
Stock is restored.

**Adjust inventory manually** (Manager+):
```http
POST /api/v1/inventory/adjust/
Content-Type: application/json

{
  "product_id": 7,
  "quantity": -3,
  "notes": "Damaged stock written off."
}
```
`quantity` is signed: positive = addition, negative = deduction. Zero is rejected.

---

## 4. Endpoint Reference

### 4.1 Auth

#### `POST /api/v1/auth/token/`

Obtain an authentication token. **Public — no token required.**

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | Yes | User's email address |
| `password` | string | Yes | User's password |

**Response `200 OK`:**

| Field | Type | Description |
|-------|------|-------------|
| `token` | string | Authentication token (40-char hex) |
| `user_id` | integer | Authenticated user's primary key |
| `email` | string | Authenticated user's email |
| `role_name` | string \| null | Role name (`Admin`, `Manager`, `Staff`) or null if no role assigned |

**Error cases:**
- `400 validation_error` — missing fields, invalid email format
- `400 validation_error` — invalid credentials (`code: "invalid_credentials"`)
- `400 validation_error` — account deactivated (`code: "account_disabled"`)

---

#### `POST /api/v1/auth/token/revoke/`

Delete the caller's token (logout). **Requires authentication.**

No request body. Response: `204 No Content`.

---

#### `GET /api/v1/auth/me/`

Return the authenticated caller's identity. **Requires authentication.**

Used by clients to verify a stored token is still valid and surface "logged in as ..." information without granting any data-access scope. Reads only — no side effects.

No request body. Response `200 OK`:

```json
{
  "user_id":    3,
  "email":      "manager@retailops.local",
  "first_name": "Maria",
  "last_name":  "Manager",
  "role_name":  "Manager",
  "is_active":  true
}
```

**Error cases:**
- `401 not_authenticated` — token missing, malformed, or revoked

---

#### `POST /api/v1/auth/password-reset/`

Request a password-reset link. **Public — no token required.**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | Yes | Email address of the account to reset |

Response `200 OK`:
```json
{"detail": "Password reset email sent."}
```

In development, the email backend is `DecodedConsoleEmailBackend` — the reset link is printed to the Django server terminal rather than sent. Override with `DJANGO_EMAIL_BACKEND` for production.

**Error cases:**
- `400 validation_error` — email field missing

> No 404 is returned for unknown email addresses — this is intentional to avoid user enumeration.

---

#### `POST /api/v1/auth/password-reset/confirm/`

Complete a password reset using the token from the reset email. **Public — no token required.**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `uid` | string | Yes | Base64-encoded user ID from the reset link |
| `token` | string | Yes | One-time reset token from the reset link |
| `new_password` | string | Yes | The new password (minimum 8 characters) |
| `confirm_password` | string | Yes | Must match `new_password` |

Response `200 OK`:
```json
{"detail": "Password has been reset successfully."}
```

**Error cases:**
- `400 validation_error` — token invalid or expired, passwords don't match, password too weak

---

### 4.2 Dashboard

#### `GET /api/v1/dashboard/`

Summary statistics for the current calendar month. **Any authenticated user.**

**Response `200 OK`:**

| Field | Type | Description |
|-------|------|-------------|
| `orders_this_month` | integer | Total orders created this month |
| `revenue_this_month` | string (decimal) | Sum of `total_amount` for Paid/Shipped/Delivered orders whose `paid_at` falls within this month |
| `pending_payments_count` | integer | Confirmed orders not yet paid |
| `low_stock_count` | integer | Products at or below their `low_stock_threshold`, including out-of-stock |
| `recent_orders` | array | The 5 most recently created orders (compact representation) |

**`recent_orders` item:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Order primary key |
| `order_number` | string | e.g. `SO-20260415-0001` |
| `customer` | string | Customer full name |
| `total_amount` | string | Order total |
| `status` | string | Current order status |
| `created_at` | string (ISO 8601) | Creation timestamp |

---

### 4.3 Roles

Read-only reference data. **Admin only.**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/roles/` | List all roles |
| `GET` | `/api/v1/roles/{id}/` | Retrieve a single role |

**Role object:**
```json
{
  "id": 1,
  "name": "Admin"
}
```

Roles are seeded system data (`Admin`, `Manager`, `Staff`) and cannot be created, modified, or deleted via the API.

---

### 4.4 Users

**Admin only**, except `GET /api/v1/users/{id}/` which allows any authenticated user to retrieve their own profile.

> **Note:** There is no `DELETE` on users. Use the `deactivate` action instead.

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| `GET` | `/api/v1/users/` | Admin | Paginated user list |
| `POST` | `/api/v1/users/` | Admin | Create a user |
| `GET` | `/api/v1/users/{id}/` | Admin or self | Retrieve a user |
| `PATCH` | `/api/v1/users/{id}/` | Admin | Update profile fields |
| `POST` | `/api/v1/users/{id}/change-password/` | Admin | Set a new password |
| `POST` | `/api/v1/users/{id}/deactivate/` | Admin | Set `is_active=false` |
| `POST` | `/api/v1/users/{id}/reactivate/` | Admin | Set `is_active=true` |

**Filters:** none  
**Search:** `?search=` matches `first_name`, `last_name`, `email`  
**Ordering:** `?ordering=first_name|last_name|email|created_at`

---

#### Create user — `POST /api/v1/users/`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | Yes | Must be unique (case-insensitive) |
| `first_name` | string | Yes | |
| `last_name` | string | Yes | |
| `role` | integer | Yes | Role primary key (1=Admin, 2=Manager, 3=Staff) |
| `password` | string | Yes (on create) | Minimum 8 characters; write-only |
| `is_active` | boolean | No | Defaults to `true` |
| `timezone` | string | No | IANA timezone name (e.g. `America/New_York`). Defaults to `UTC`. Activates per-request timezone rendering via `RegionalMiddleware`. |
| `language` | string | No | BCP 47 language code (e.g. `en`, `es`). Defaults to `en`. |

Response: `201 Created` — [User object](#user-object).

---

#### Change password — `POST /api/v1/users/{id}/change-password/`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `new_password` | string | Yes | Minimum 8 characters |
| `confirm_password` | string | Yes | Must match `new_password` |

Response `200 OK`:
```json
{"detail": "Password updated for Jane Smith."}
```

---

#### Deactivate / Reactivate — `POST /api/v1/users/{id}/deactivate/` · `/reactivate/`

No request body required.

Response `200 OK`:
```json
{"detail": "Jane Smith deactivated."}
```

**Guard:** Returns `409 conflict` if an admin attempts to deactivate their own account.

---

### 4.5 Customers

**Any authenticated user** for all operations.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/customers/` | Paginated customer list |
| `POST` | `/api/v1/customers/` | Create a customer |
| `GET` | `/api/v1/customers/{id}/` | Retrieve a customer |
| `PUT` | `/api/v1/customers/{id}/` | Full update |
| `PATCH` | `/api/v1/customers/{id}/` | Partial update |
| `DELETE` | `/api/v1/customers/{id}/` | Delete (see guard below) |

**Filters:** none  
**Search:** `?search=` matches `first_name`, `last_name`, `email`  
**Ordering:** `?ordering=first_name|last_name|email|created_at`

**Delete guard:** Returns `409 conflict` if the customer has any associated orders. The database enforces this with `on_delete=PROTECT`.

---

#### Create / update customer fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `first_name` | string | Yes | |
| `last_name` | string | Yes | |
| `email` | string | Yes | Must be unique (case-insensitive) |
| `phone` | string | No | |
| `national_id` | string \| null | No | National identity document number (e.g. `"V-12345678"`). Must be unique across all customers. |
| `date_of_birth` | string (YYYY-MM-DD) \| null | No | Customer date of birth |
| `gender` | string \| null | No | `"M"` (Masculino) or `"F"` (Femenino). Omit or send blank for unspecified. |
| `address_line1` | string | No | |
| `address_line2` | string | No | |
| `city` | string | No | |
| `state` | string | No | |
| `postal_code` | string | No | |
| `country` | string | No | Defaults to `"United States"` |
| `notes` | string | No | |
| `user` | integer \| null | No | Link to a system user account (primary key) |

Response: [Customer object](#customer-object).

---

### 4.6 Categories

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| `GET` | `/api/v1/categories/` | Any authenticated | List all categories |
| `POST` | `/api/v1/categories/` | Manager+ | Create a category |
| `GET` | `/api/v1/categories/{id}/` | Any authenticated | Retrieve a category |
| `PUT` | `/api/v1/categories/{id}/` | Manager+ | Full update |
| `PATCH` | `/api/v1/categories/{id}/` | Manager+ | Partial update |
| `DELETE` | `/api/v1/categories/{id}/` | Manager+ | Delete |

**Search:** `?search=` matches `name`, `description`  
**Ordering:** `?ordering=name|created_at`

**Delete guard:** Returns `409` if the category has products (`on_delete=PROTECT`).

---

#### Create / update category fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Must be unique |
| `description` | string | No | |
| `parent_category` | integer \| null | No | Parent category primary key; creates a subcategory |

**Validation:** A category cannot be its own parent.

Response: [Category object](#category-object).

---

### 4.7 Products

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| `GET` | `/api/v1/products/` | Any authenticated | Paginated product list |
| `POST` | `/api/v1/products/` | Manager+ | Create a product |
| `GET` | `/api/v1/products/{id}/` | Any authenticated | Retrieve a product |
| `PUT` | `/api/v1/products/{id}/` | Manager+ | Full update |
| `PATCH` | `/api/v1/products/{id}/` | Manager+ | Partial update |
| `DELETE` | `/api/v1/products/{id}/` | Manager+ | Delete |
| `GET` | `/api/v1/products/{id}/movements/` | Any authenticated | Paginated movement history |

**Filters:**

| Parameter | Values | Description |
|-----------|--------|-------------|
| `category` | integer | Filter by category primary key |
| `is_active` | `true` \| `false` | Filter by active status |
| `unit_of_measure` | `piece` · `kg` · `liter` · `meter` · `box` · `pack` | Filter by unit |
| `stock` | `out` · `low` · `ok` · `all` | `out` = stock ≤ 0; `low` = 0 < stock ≤ threshold; `ok` = stock > threshold |

**Search:** `?search=` matches `sku`, `name`, `description`  
**Ordering:** `?ordering=name|sku|unit_price|created_at`

---

#### Create / update product fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sku` | string | Yes | Stock-Keeping Unit; must be unique |
| `name` | string | Yes | |
| `description` | string | No | |
| `category_id` | integer | Yes | Category primary key |
| `unit_of_measure` | string | Yes | `piece` · `kg` · `liter` · `meter` · `box` · `pack` |
| `unit_price` | string (decimal) | Yes | Must be > 0 |
| `low_stock_threshold` | integer | No | Alert level; must be ≥ 0; defaults to 10 |
| `is_active` | boolean | No | Defaults to `true` |

**Read-only fields returned:** `current_stock`, `is_low_stock`, `is_out_of_stock`. These are computed from `InventoryMovement` records — they are not stored columns and cannot be set directly.

Response: [Product object](#product-object).

---

#### Product movement history — `GET /api/v1/products/{id}/movements/`

Returns the paginated inventory movement log for a single product, newest first. Accepts standard `page` and `page_size` parameters. Response items are [InventoryMovement objects](#inventorymovement-object).

---

### 4.8 Inventory

Inventory movements are **immutable** records. They cannot be updated or deleted.

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| `GET` | `/api/v1/inventory/` | Any authenticated | Paginated movement list |
| `GET` | `/api/v1/inventory/{id}/` | Any authenticated | Retrieve a single movement |
| `POST` | `/api/v1/inventory/adjust/` | Manager+ | Record a manual stock adjustment |
| `POST` | `/api/v1/inventory/bulk-adjust/` | Manager+ | Adjust multiple products; partial-success response |

**Filters:**

| Parameter | Values | Description |
|-----------|--------|-------------|
| `product` | integer | Filter by product primary key |
| `movement_type` | `sale` · `purchase` · `adjustment` · `return` | Filter by movement type |
| `reference_type` | `SalesOrder` · `PurchaseOrder` · `ManualAdjustment` · `Return` | Filter by reference document type |
| `date_from` | `YYYY-MM-DD` | Movements on or after this date |
| `date_to` | `YYYY-MM-DD` | Movements on or before this date |

**Ordering:** `?ordering=created_at` (default: `-created_at`, newest first)

---

#### Manual adjustment — `POST /api/v1/inventory/adjust/`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `product_id` | integer | Yes | Product primary key |
| `quantity` | integer | Yes | Signed quantity: positive = stock addition, negative = stock deduction. Zero is rejected. |
| `notes` | string | No | Free-text reason for the adjustment |

Response `201 Created`: the created [InventoryMovement object](#inventorymovement-object) with `movement_type: "adjustment"` and `reference_type: "ManualAdjustment"`.

---

#### Bulk adjustment — `POST /api/v1/inventory/bulk-adjust/`

Adjust stock for multiple products in a single request. **Manager+ only.**

Each item in the `adjustments` array is processed independently. If one item fails, the rest are not aborted — the response reports both outcomes.

**Request body:**

```json
{
  "adjustments": [
    {"product_id": 7,  "quantity": 50,  "notes": "Weekly restock"},
    {"product_id": 12, "quantity": -8,  "notes": "Damaged in transit"}
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `adjustments` | array | Yes | Non-empty array of adjustment objects |
| `adjustments[].product_id` | integer | Yes | Product primary key |
| `adjustments[].quantity` | integer | Yes | Signed quantity; non-zero |
| `adjustments[].notes` | string | No | Free-text reason |

**Response `200 OK`:**

```json
{
  "succeeded": [
    { "id": 301, "product": { "id": 7, "sku": "EL-CBL-001", "name": "USB-C Cable 2m" },
      "movement_type": "adjustment", "quantity": 50, ... }
  ],
  "failed": [
    { "product_id": 12, "error": "..." }
  ]
}
```

A `200` is returned even if all items fail — inspect `failed` to identify errors.

---

### 4.9 Orders

#### Order lifecycle

```
Draft → Pending → Confirmed → Paid → Shipped → Delivered
                     ↓                 ↓
                 Cancelled           Refunded
```

Each transition is a separate POST action. Transitions that move stock create `InventoryMovement` records atomically in the same database transaction.

| Action | Endpoint | Required status | Permission | Stock effect |
|--------|----------|-----------------|------------|--------------|
| Create | `POST /api/v1/orders/` | — | Staff+ | None |
| Submit | `POST /api/v1/orders/{id}/submit/` | `draft` | Staff+ | None |
| Confirm | `POST /api/v1/orders/{id}/confirm/` | `pending` | Manager+ | Deducts (negative movements) |
| Ship | `POST /api/v1/orders/{id}/ship/` | `paid` | Staff+ | None |
| Deliver | `POST /api/v1/orders/{id}/deliver/` | `shipped` | Staff+ | None |
| Cancel | `POST /api/v1/orders/{id}/cancel/` | `confirmed` | Manager+ | Restores (positive movements) |
| Refund | `POST /api/v1/orders/{id}/refund/` | `paid` | Admin | Restores (positive movements) |
| Bulk confirm | `POST /api/v1/orders/bulk-transition/` | `pending` (per order) | Manager+ | Deducts per confirmed order |
| Bulk ship | `POST /api/v1/orders/bulk-transition/` | `paid` (per order) | Manager+ | None |
| Bulk deliver | `POST /api/v1/orders/bulk-transition/` | `shipped` (per order) | Manager+ | None |

All single-order transition endpoints accept **no request body** and return the updated [Order object](#order-object).

**Submit guard:** Returns `409 no_items` if the order has zero line items. (Confirm carries the same guard.)

---

#### Order CRUD

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| `GET` | `/api/v1/orders/` | Any authenticated | Paginated order list |
| `POST` | `/api/v1/orders/` | Staff+ | Create a Draft order |
| `GET` | `/api/v1/orders/{id}/` | Any authenticated | Retrieve an order |
| `PUT` | `/api/v1/orders/{id}/` | Staff+ | Full update (Draft only) |
| `PATCH` | `/api/v1/orders/{id}/` | Staff+ | Partial update (Draft only) |
| `DELETE` | `/api/v1/orders/{id}/` | Staff+ | Delete (Draft only) |

**Edit/delete guard:** Returns `409 wrong_status` for any write on a non-Draft order.

**Filters:**

| Parameter | Values | Description |
|-----------|--------|-------------|
| `customer` | integer | Filter by customer primary key |
| `status` | `draft` · `pending` · `confirmed` · `paid` · `shipped` · `delivered` · `cancelled` · `refunded` | Filter by status |
| `date_from` | `YYYY-MM-DD` | Orders created on or after this date |
| `date_to` | `YYYY-MM-DD` | Orders created on or before this date |

**Search:** `?search=` matches `order_number`  
**Ordering:** `?ordering=created_at|total_amount` (default: `-created_at`)

---

#### Create / update order fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `customer_id` | integer | Yes | Customer primary key |
| `items` | array | Yes (on create) | Line items array (see below). Required on create; optional on update |
| `discount_amount` | string (decimal) | No | Discount applied to the order total |
| `tax_amount` | string (decimal) | No | Tax applied to the order total |
| `notes` | string | No | Free-text notes |

**Line item fields (`items` array):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `product_id` | integer | Yes | Must be an active product |
| `quantity` | integer | Yes | Minimum 1 |
| `unit_price` | string (decimal) | No | Price snapshot. If omitted, the product's current `unit_price` is used |

When items are supplied on an update, they **replace** all existing line items and totals are recalculated.

**Confirm guard:** Returns `409 no_items` if the order has zero line items at the time of confirmation.

---

#### Bulk transition — `POST /api/v1/orders/bulk-transition/`

Apply one lifecycle action (`confirm`, `ship`, or `deliver`) to multiple orders in a single request. **Manager+ only.**

Each order is processed independently. Orders that fail (wrong status, not found, validation error) are collected in `failed` without aborting the rest.

**Request body:**

```json
{
  "order_ids": [42, 43, 44],
  "action": "confirm"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `order_ids` | array of integers | Yes | Non-empty list of order primary keys |
| `action` | string | Yes | `confirm`, `ship`, or `deliver` |

**Response `200 OK`:**

```json
{
  "succeeded": [ { "id": 42, "status": "confirmed", ... }, ... ],
  "failed":    [ { "id": 44, "error": "Order is not in pending status." } ]
}
```

A `200` is returned even when all orders fail — inspect `failed` for individual errors.

---

### 4.10 Payments

Payments are **immutable** financial records. They cannot be updated or deleted.

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| `GET` | `/api/v1/payments/` | Any authenticated | Paginated payment list |
| `POST` | `/api/v1/payments/` | Any authenticated | Record a payment |
| `GET` | `/api/v1/payments/{id}/` | Any authenticated | Retrieve a payment |

**Filters:**

| Parameter | Values | Description |
|-----------|--------|-------------|
| `sales_order` | integer | Filter by order primary key |
| `payment_method` | `cash` · `bank_transfer` · `card` · `check` · `other` | Filter by method |
| `date_from` | `YYYY-MM-DD` | Payments on or after this date |
| `date_to` | `YYYY-MM-DD` | Payments on or before this date |

**Ordering:** `?ordering=created_at|amount` (default: `-created_at`)

---

#### Record a payment — `POST /api/v1/payments/`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sales_order` | integer | Yes | Order primary key. Must be in `confirmed` status |
| `amount` | string (decimal) | Yes | Must be > 0 |
| `payment_method` | string | Yes | `cash` · `bank_transfer` · `card` · `check` · `other` |
| `reference_number` | string | No | External transaction reference |
| `notes` | string | No | |

**Auto-transition:** If the cumulative `amount_paid` across all payments for the order reaches or exceeds `total_amount`, the order automatically transitions to `paid` in the same atomic transaction.

Response `201 Created`: the created [Payment object](#payment-object), including the auto-generated `payment_number` (`PAY-YYYYMMDD-XXXX`).

---

### 4.11 Settings

System-wide currency display settings. A singleton row — there is always exactly one `SystemSettings` record.

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| `GET` | `/api/v1/settings/` | Any authenticated | Retrieve the current settings |
| `PATCH` | `/api/v1/settings/` | Manager+ | Partial update of one or more fields |
| `POST` | `/api/v1/settings/secondary-rate/refresh/` | Manager+ | Fetch the secondary-currency rate from the configured source and save it |

---

#### `GET /api/v1/settings/`

**Response `200 OK`:**

```json
{
  "currency_code": "USD",
  "currency_symbol": "$",
  "decimal_places": 2,
  "secondary_currency_enabled": false,
  "secondary_currency_code": "",
  "secondary_currency_symbol": "",
  "secondary_decimal_places": 2,
  "secondary_exchange_rate": "1.00000000",
  "secondary_rate_auto_update_enabled": false,
  "secondary_rate_source_url": "https://ve.dolarapi.com/v1/dolares/oficial",
  "secondary_rate_source_field": "promedio",
  "secondary_rate_updated_at": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `currency_code` | string | ISO 4217 code (e.g. `USD`, `EUR`, `GBP`). Max 3 characters. |
| `currency_symbol` | string | Display symbol prepended to amounts (e.g. `$`, `€`, `Bs`). Max 4 characters. |
| `decimal_places` | integer | Number of decimal places for monetary display (typical values: 0 for JPY, 2 for USD/EUR, 3 for KWD). |
| `secondary_currency_enabled` | boolean | Feature flag. When `false`, all secondary fields are stored but not displayed anywhere. |
| `secondary_currency_code` | string | ISO 4217 code for the secondary currency (e.g. `VES`). Max 3 characters. Empty when unused. |
| `secondary_currency_symbol` | string | Display symbol for secondary amounts (e.g. `Bs.`). Max 4 characters. Empty when unused. |
| `secondary_decimal_places` | integer | Decimal places for secondary amounts (0–4 typical). |
| `secondary_exchange_rate` | string (decimal) | Units of secondary currency per 1 unit of primary (e.g. `"36.50000000"`). Must be > 0 when secondary is enabled. Returned with 8 decimal places. |
| `secondary_rate_auto_update_enabled` | boolean | When `true`, the rate can be refreshed from `secondary_rate_source_url`. Requires both source fields to be set. |
| `secondary_rate_source_url` | string (URL) | JSON endpoint to fetch the rate from. Default: DolarApi BCV official rate. |
| `secondary_rate_source_field` | string | Dotted path to the numeric rate in the source JSON (e.g. `promedio`, `data.rate`). |
| `secondary_rate_updated_at` | string (datetime) or null | Read-only. Timestamp of the last successful automatic update. |

---

#### `POST /api/v1/settings/secondary-rate/refresh/`

Fetch the secondary-currency exchange rate from `secondary_rate_source_url`
(reading `secondary_rate_source_field`) and persist it to
`secondary_exchange_rate`. Manager or Admin only.

**Response `200 OK`:**

```json
{
  "secondary_exchange_rate": "36.42",
  "secondary_rate_updated_at": "2026-06-07T13:20:00Z"
}
```

**Response `502 Bad Gateway`** — the rate source was unreachable, returned an
error, or the configured field was missing/non-numeric:

```json
{
  "errors": ["Could not reach the rate source."],
  "code": "network_error"
}
```

---

#### `PATCH /api/v1/settings/`

Partial update — only the fields you provide are changed. All fields are optional, but at least one must be present.

**Request body example (enable secondary currency):**
```json
{
  "secondary_currency_enabled": true,
  "secondary_currency_code": "VES",
  "secondary_currency_symbol": "Bs.",
  "secondary_decimal_places": 2,
  "secondary_exchange_rate": "36.5"
}
```

**Primary currency example:**
```json
{
  "currency_code": "EUR",
  "currency_symbol": "€",
  "decimal_places": 2
}
```

Response `200 OK`: the updated settings object (same shape as `GET`).

**Validation rules for secondary currency:**
- If `secondary_currency_enabled` is `true`, `secondary_currency_symbol` must be non-empty (even in a PATCH that omits it — the server merges with the stored value before validating).
- `secondary_exchange_rate` must be `> 0` whenever provided.
- Violations return `400 validation_error` with per-field details.

**Note:** Changing these settings takes effect immediately for all new back-office renders and API responses. Existing prices and totals stored in the database are raw decimals — they are not converted, only the display changes. The secondary exchange rate is a static, admin-set value used for back-office display only; it has no effect on the kiosk PWA, which maintains its own live exchange rate pipeline.

---

### 4.12 MCP Skill Card

A public capability descriptor intended for AI agents. Returns a structured document describing all MCP tools, resources, workflow prompts, the order lifecycle, constraints, and error codes.

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| `GET` | `/api/v1/mcp-skill/` | **None — public** | Returns the MCP skill card |

**Formats:**

| Format | How to request | Content-Type |
|--------|---------------|--------------|
| JSON (default) | `GET /api/v1/mcp-skill/` | `application/json` |
| Markdown | `GET /api/v1/mcp-skill/?format=markdown` | `text/markdown` |
| Markdown | `Accept: text/markdown` header | `text/markdown` |

The JSON response shape:
```json
{
  "schema_version": "1.0",
  "skill_id": "retailops-mcp",
  "display_name": "RetailOps MCP Server",
  "version": "1.0.0",
  "description": "...",
  "api_base_url": "http://...",
  "mcp_connection": { "transports": [...], "configuration": {...} },
  "authentication": { "type": "bearer_token", ... },
  "role_hierarchy": { "roles": {...} },
  "tools": { "<domain>": [ { "name", "role", "description", "params" }, ... ] },
  "resources": [ { "uri", "description" }, ... ],
  "workflows": [ { "name", "description", "params", "steps" }, ... ],
  "order_lifecycle": { "states", "transitions", "key_rules" },
  "constraints": { "foreign_key_guards", "immutability", "validation" },
  "errors": { "<code>": "<recovery message>" }
}
```

---

## 5. Object Schemas

### Customer object

```json
{
  "id": 14,
  "full_name": "Jane Doe",
  "first_name": "Jane",
  "last_name": "Doe",
  "email": "jane.doe@example.com",
  "phone": "+1-555-0100",
  "national_id": "V-12345678",
  "date_of_birth": "1985-03-22",
  "gender": "F",
  "address_line1": "123 Main St",
  "address_line2": "",
  "city": "Springfield",
  "state": "IL",
  "postal_code": "62701",
  "country": "United States",
  "notes": "",
  "user": null,
  "created_at": "2026-04-01T09:00:00Z",
  "updated_at": "2026-04-01T09:00:00Z"
}
```

`national_id` is unique across all customers and `null` when not provided. `date_of_birth` is an ISO 8601 date string or `null`. `gender` is `"M"`, `"F"`, or `""` (empty string = unspecified).

### Category object

```json
{
  "id": 3,
  "name": "Cables",
  "description": "Cables and connectors",
  "parent_category": 1,
  "display_name": "Electronics › Cables",
  "subcategories": [],
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

`display_name` reflects the full breadcrumb path using `›` as the separator for nested categories. `subcategories` is a list of child category primary keys.

### Product object

```json
{
  "id": 7,
  "sku": "EL-CBL-001",
  "name": "USB-C Cable 2m",
  "description": "High-speed USB-C charging and data cable.",
  "category": {
    "id": 3,
    "name": "Cables",
    "display_name": "Electronics › Cables"
  },
  "unit_of_measure": "piece",
  "unit_price": "12.99",
  "low_stock_threshold": 20,
  "is_active": true,
  "current_stock": 85,
  "is_low_stock": false,
  "is_out_of_stock": false,
  "created_at": "2026-01-10T00:00:00Z",
  "updated_at": "2026-04-10T08:30:00Z"
}
```

`category_id` is accepted on write but not returned on read (the nested `category` object is returned instead).

### InventoryMovement object

```json
{
  "id": 201,
  "product": {
    "id": 7,
    "sku": "EL-CBL-001",
    "name": "USB-C Cable 2m"
  },
  "movement_type": "sale",
  "movement_type_display": "Sale",
  "quantity": -5,
  "reference_type": "SalesOrder",
  "reference_id": 42,
  "notes": "Stock deducted on confirmation of SO-20260415-0042",
  "created_by": "Jane Smith",
  "created_at": "2026-04-15T14:30:00Z"
}
```

`quantity` is signed: negative = stock removed, positive = stock added.

### Order object

```json
{
  "id": 42,
  "order_number": "SO-20260415-0042",
  "customer": { "...": "full customer object" },
  "status": "confirmed",
  "status_display": "Confirmed",
  "subtotal": "38.97",
  "tax_amount": "3.90",
  "discount_amount": "0.00",
  "total_amount": "42.87",
  "amount_paid": "0.00",
  "amount_outstanding": "42.87",
  "notes": "Urgent — please prioritise.",
  "items": [
    {
      "id": 88,
      "product": { "...": "full product object" },
      "quantity": 3,
      "unit_price": "12.99",
      "tax_rate": "0.00",
      "line_total": "38.97"
    }
  ],
  "created_by": 3,
  "confirmed_by": 2,
  "created_at": "2026-04-15T10:00:00Z",
  "updated_at": "2026-04-15T14:30:00Z",
  "confirmed_at": "2026-04-15T14:30:00Z",
  "paid_at": null
}
```

`amount_paid` and `amount_outstanding` are computed from the order's payments; they are not stored columns. `created_by` and `confirmed_by` are user primary keys.

### Payment object

```json
{
  "id": 17,
  "payment_number": "PAY-20260415-0017",
  "sales_order": 42,
  "sales_order_number": "SO-20260415-0042",
  "amount": "42.87",
  "payment_method": "bank_transfer",
  "payment_method_display": "Bank Transfer",
  "reference_number": "TXN-98765",
  "notes": "Wire transfer received.",
  "recorded_by": 3,
  "recorded_by_name": "Staff User",
  "created_at": "2026-04-15T15:00:00Z"
}
```

### SystemSettings object

```json
{
  "currency_code": "USD",
  "currency_symbol": "$",
  "decimal_places": 2,
  "secondary_currency_enabled": false,
  "secondary_currency_code": "",
  "secondary_currency_symbol": "",
  "secondary_decimal_places": 2,
  "secondary_exchange_rate": "1.00000000",
  "secondary_rate_auto_update_enabled": false,
  "secondary_rate_source_url": "https://ve.dolarapi.com/v1/dolares/oficial",
  "secondary_rate_source_field": "promedio",
  "secondary_rate_updated_at": null
}
```

Singleton — there is always exactly one row. Returned by `GET /api/v1/settings/` and accepted by `PATCH /api/v1/settings/`. The `secondary_*` fields are always present in responses; when `secondary_currency_enabled` is `false` their display values are stored but ignored by all display logic. `secondary_rate_updated_at` is read-only and set by the rate-refresh endpoint or the `update_bcv_rate` command.

---

### User object

```json
{
  "id": 2,
  "email": "manager@retailops.local",
  "first_name": "Manager",
  "last_name": "User",
  "role": {
    "id": 2,
    "name": "Manager"
  },
  "role_name": "Manager",
  "is_active": true,
  "is_staff": false,
  "timezone": "America/New_York",
  "language": "en",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

`timezone` is an IANA timezone string. `language` is a BCP 47 language code. Both are activated per-request by `RegionalMiddleware` so all datetime rendering and UI strings respect the user's locale. Password and internal Django session fields are never included in responses.
