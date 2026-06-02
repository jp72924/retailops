# RetailOps MCP Server — Design & Implementation Plan

## 1. What This Is and Why It Matters

The Model Context Protocol (MCP) is an open standard that lets AI models (Claude, GPT, local models) and external tools connect to data sources and services through a structured, discoverable interface. Instead of an AI model guessing how to call your REST API or a developer writing custom integration code for every consumer, the MCP server advertises exactly what it can do — and any MCP-compatible client can immediately use it.

For RetailOps, this means:
- Claude (or any AI agent) can browse customers, place orders, check stock levels, and process payments through natural language — with the system enforcing all existing business rules.
- External tools (n8n, LangChain agents, custom scripts) get a single, well-documented integration point.
- No business logic is duplicated in the MCP layer. The MCP server is a thin translation layer that forwards work to the existing REST API, which already enforces role checks, inventory transactions, and order lifecycle rules.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     AI Clients                          │
│  Claude Desktop · Claude API agents · LangChain · n8n  │
└──────────────────────────┬──────────────────────────────┘
                           │  MCP Protocol (stdio / SSE)
                           ▼
┌─────────────────────────────────────────────────────────┐
│               RetailOps MCP Server                      │
│                                                         │
│  ┌──────────┐  ┌───────────┐  ┌──────────────────────┐  │
│  │  Tools   │  │ Resources │  │      Prompts         │  │
│  │ (45 ops) │  │(URI reads)│  │  (guided workflows)  │  │
│  └────┬─────┘  └─────┬─────┘  └──────────┬───────────┘  │
│       │              │                   │              │
│  ┌────▼──────────────▼───────────────────▼───────────┐  │
│  │           RetailOps API HTTP Client                │  │
│  │   (httpx · token auth · error normalisation)      │  │
│  └────────────────────────┬──────────────────────────┘  │
└───────────────────────────│─────────────────────────────┘
                            │  HTTP  Authorization: Token …
                            ▼
┌─────────────────────────────────────────────────────────┐
│          RetailOps REST API  /api/v1/                   │
│  (Django REST Framework · role-gated · atomic txns)     │
└──────────────────────────┬──────────────────────────────┘
                           │  Django ORM
                           ▼
                    SQLite  (db.sqlite3)
```

### Key design decisions

| Decision | Rationale |
|---|---|
| MCP server is **stateless** | Each tool call is an independent HTTP request to the API. No session state lives in the MCP process. |
| **No business logic** in the MCP layer | All validation, role enforcement, inventory transactions, and order transitions stay in the Django API. The MCP server never touches the database directly. |
| **One token per role** | The server is configured with a token for a specific RetailOps user (e.g., a dedicated `mcp-agent@retailops.local` account). The role of that account determines what tools can actually succeed. |
| Tools are **self-describing** | Every tool carries a full JSON Schema for its inputs, making it usable by any MCP client without additional documentation. |
| Resources provide **read-only browsing** | AI models can read collections and individual records through MCP Resources, which map 1-to-1 to GET endpoints. |
| Prompts encode **workflows** | Multi-step business workflows (create order, process payment, handle refund) are expressed as MCP Prompts — reusable instruction templates. |

---

## 3. Project Structure

Add this directory alongside the existing Django apps:

```
mcp_server/
├── __init__.py
├── server.py            ← MCP server entry point; registers tools, resources, prompts
├── config.py            ← Reads env vars; exposes Settings dataclass
├── client.py            ← Async httpx client wrapper for the RetailOps API
├── errors.py            ← Maps API error envelopes to MCP-friendly messages
│
├── tools/
│   ├── __init__.py
│   ├── auth.py          ← retailops_login, retailops_logout
│   ├── dashboard.py     ← retailops_get_dashboard
│   ├── customers.py     ← 5 customer tools (list, get, create, update, delete)
│   ├── categories.py    ← 5 category tools
│   ├── products.py      ← 6 product tools (includes get_movements)
│   ├── inventory.py     ← 3 inventory tools (list, get, adjust)
│   ├── orders.py        ← 12 order tools (CRUD + 6 transitions)
│   ├── payments.py      ← 3 payment tools (list, get, record)
│   └── users.py         ← 7 user management tools (Admin-only)
│
├── resources/
│   ├── __init__.py
│   └── handlers.py      ← URI template → GET request mapping
│
└── prompts/
    ├── __init__.py
    └── workflows.py     ← 5 guided workflow prompts
```

---

## 4. Dependencies

Add to `requirements.txt`:

```
mcp>=1.0.0              # Anthropic MCP Python SDK
httpx>=0.27.0           # Async HTTP client (replaces requests)
python-dotenv>=1.0.0    # .env file support for local dev
```

The MCP SDK (`mcp`) provides the `FastMCP` class, the `Tool`, `Resource`, and `Prompt` primitives, and transport handling (stdio for local, SSE for remote).

---

## 5. Configuration (`mcp_server/config.py`)

The MCP server needs to know how to reach the RetailOps API and how to authenticate. All configuration comes from environment variables so no secrets live in code.

```python
# mcp_server/config.py
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    base_url: str        # e.g. "http://127.0.0.1:8000/api/v1"
    api_token: str       # RetailOps auth token for the agent account
    timeout: float       # HTTP timeout in seconds (default 30)
    debug: bool          # Log raw requests/responses when True

settings = Settings(
    base_url=os.environ.get("RETAILOPS_BASE_URL", "http://127.0.0.1:8000/api/v1"),
    api_token=os.environ.get("RETAILOPS_API_TOKEN", ""),
    timeout=float(os.environ.get("RETAILOPS_TIMEOUT", "30")),
    debug=os.environ.get("RETAILOPS_DEBUG", "false").lower() == "true",
)
```

**`.env` file for local development (never commit this):**
```
RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1
RETAILOPS_API_TOKEN=<token obtained via POST /api/v1/auth/token/>
RETAILOPS_DEBUG=false
```

**How to obtain the token for the agent account:**
```bash
# Create a dedicated MCP agent user via Django admin or shell, then:
curl -X POST http://127.0.0.1:8000/api/v1/auth/token/ \
     -H "Content-Type: application/json" \
     -d '{"email": "mcp-agent@retailops.local", "password": "..."}'
# Response: {"token": "abc123...", "user_id": 4, "email": "...", "role_name": "Manager"}
# Copy "token" into RETAILOPS_API_TOKEN
```

---

## 6. HTTP Client (`mcp_server/client.py`)

All API communication goes through a single client class. This keeps auth headers, base URL, timeout, and error normalisation in one place.

```python
# mcp_server/client.py  (illustrative — not production code)
import httpx
from .config import settings
from .errors import RetailOpsError, raise_for_status

class RetailOpsClient:
    """Thin async wrapper around the RetailOps REST API."""

    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            headers={"Authorization": f"Token {settings.api_token}"},
            timeout=settings.timeout,
        )

    async def get(self, path: str, params: dict = None) -> dict:
        r = await self._http.get(path, params=params or {})
        raise_for_status(r)
        return r.json()

    async def post(self, path: str, body: dict = None) -> dict:
        r = await self._http.post(path, json=body or {})
        raise_for_status(r)
        return r.json()

    async def patch(self, path: str, body: dict) -> dict:
        r = await self._http.patch(path, json=body)
        raise_for_status(r)
        return r.json()

    async def delete(self, path: str) -> None:
        r = await self._http.delete(path)
        raise_for_status(r)

    async def close(self):
        await self._http.aclose()
```

---

## 7. Error Handling (`mcp_server/errors.py`)

The RetailOps API returns a consistent error envelope:
```json
{ "error": "...", "code": "...", "details": { ... } }
```

The error module converts HTTP responses into human-readable MCP tool errors.

```python
# mcp_server/errors.py
import httpx

class RetailOpsError(Exception):
    def __init__(self, status: int, error: str, code: str, details: dict = None):
        self.status = status
        self.error = error
        self.code = code
        self.details = details or {}
        super().__init__(f"[{status}] {code}: {error}")

def raise_for_status(response: httpx.Response) -> None:
    """Convert non-2xx responses to RetailOpsError."""
    if response.is_success:
        return
    try:
        body = response.json()
        raise RetailOpsError(
            status=response.status_code,
            error=body.get("error", "Unknown error"),
            code=body.get("code", "unknown"),
            details=body.get("details"),
        )
    except (ValueError, KeyError):
        raise RetailOpsError(
            status=response.status_code,
            error=response.text,
            code="http_error",
        )
```

**Error → user-facing message mapping:**

| HTTP status | API code | MCP tool response |
|---|---|---|
| 401 | `authentication_failed` | "Authentication failed. Check RETAILOPS_API_TOKEN." |
| 403 | `permission_denied` | "The agent account does not have permission for this action. Required role: {role}." |
| 404 | `not_found` | "Resource not found: {entity} ID {id}." |
| 409 | `conflict` | "Cannot delete: this {entity} has related records." |
| 400 | `validation_error` | "Validation failed: {details}" |
| 500 | `server_error` | "RetailOps server error. Check the Django logs." |

---

## 8. Tool Catalog

Tools are the core of the MCP server. Each tool is a callable that takes structured input, calls the API, and returns a human-readable or structured result. Below is the full catalog of 45 tools grouped by domain.

**Naming convention:** `retailops_{entity}_{action}`

---

### 8.1 Authentication (2 tools)

#### `retailops_login`
```yaml
Description: >
  Obtain a RetailOps API token for a user. Returns the token,
  user ID, email, and role name. Store the token to use in
  subsequent calls or to update RETAILOPS_API_TOKEN.
Inputs:
  email:    string (required) — user email address
  password: string (required) — user password
Calls: POST /auth/token/
Returns: { token, user_id, email, role_name }
Permission: Public (no token required)
Side effects: None
```

#### `retailops_logout`
```yaml
Description: >
  Revoke the current API token. The token in RETAILOPS_API_TOKEN
  will no longer work after this call.
Inputs: none
Calls: POST /auth/token/revoke/
Returns: { message: "Token revoked successfully." }
Permission: Any authenticated user
Side effects: Token deleted from database
```

---

### 8.2 Dashboard (1 tool)

#### `retailops_get_dashboard`
```yaml
Description: >
  Retrieve a business summary: orders this month, revenue this
  month, count of orders awaiting payment, count of low-stock
  products, and the 5 most recent orders.
Inputs: none
Calls: GET /dashboard/
Returns:
  orders_this_month:       integer
  revenue_this_month:      decimal string
  pending_payments_count:  integer
  low_stock_count:         integer
  recent_orders:           array of { id, order_number, customer, status, total_amount, created_at }
Permission: Any authenticated user
Side effects: None
```

---

### 8.3 Customers (5 tools)

#### `retailops_list_customers`
```yaml
Description: List customers with optional search and pagination.
Inputs:
  search:    string (optional) — searches first_name, last_name, email
  page:      integer (optional, default 1)
  page_size: integer (optional, default 25, max 100)
Calls: GET /customers/?search=…&page=…&page_size=…
Returns: paginated list of customer objects
Permission: Any authenticated user
```

#### `retailops_get_customer`
```yaml
Description: Retrieve a single customer by ID including their order history summary.
Inputs:
  id: integer (required)
Calls: GET /customers/{id}/
Returns: full customer object
Permission: Any authenticated user
```

#### `retailops_create_customer`
```yaml
Description: Create a new customer record.
Inputs:
  first_name:   string (required)
  last_name:    string (required)
  email:        string (required, unique)
  phone:        string (optional)
  address_line1: string (optional)
  address_line2: string (optional)
  city:          string (optional)
  state:         string (optional)
  postal_code:   string (optional)
  country:       string (optional, default "United States")
  notes:         string (optional)
Calls: POST /customers/
Returns: created customer object (includes assigned ID)
Permission: Any authenticated user
Side effects: New Customer row created
```

#### `retailops_update_customer`
```yaml
Description: Update one or more fields on an existing customer.
Inputs:
  id: integer (required)
  + any subset of the same fields as retailops_create_customer
Calls: PATCH /customers/{id}/
Returns: updated customer object
Permission: Any authenticated user
```

#### `retailops_delete_customer`
```yaml
Description: >
  Permanently delete a customer. Fails with a 409 error if the
  customer has any sales orders (on_delete=PROTECT guard).
Inputs:
  id: integer (required)
Calls: DELETE /customers/{id}/
Returns: { message: "Customer deleted." }
Permission: Any authenticated user
Side effects: Customer row deleted (hard delete)
```

---

### 8.4 Product Categories (5 tools)

#### `retailops_list_categories`
```yaml
Description: List all product categories including subcategory relationships.
Inputs:
  page:      integer (optional)
  page_size: integer (optional)
Calls: GET /categories/
Returns: paginated list of { id, name, description, parent_category, display_name, subcategories }
Permission: Any authenticated user
```

#### `retailops_get_category`
```yaml
Description: Retrieve a single category by ID.
Inputs:
  id: integer (required)
Calls: GET /categories/{id}/
Returns: full category object with subcategories list
Permission: Any authenticated user
```

#### `retailops_create_category`
```yaml
Description: Create a new product category (optionally nested under a parent).
Inputs:
  name:            string (required, unique)
  description:     string (optional)
  parent_category: integer (optional) — ID of parent category
Calls: POST /categories/
Returns: created category object
Permission: Manager or Admin
Side effects: New ProductCategory row created
```

#### `retailops_update_category`
```yaml
Description: Update a category's name, description, or parent.
Inputs:
  id: integer (required)
  + any subset of: name, description, parent_category
Calls: PATCH /categories/{id}/
Returns: updated category object
Permission: Manager or Admin
```

#### `retailops_delete_category`
```yaml
Description: >
  Delete a category. Fails with 409 if any products are assigned to it
  (on_delete=PROTECT guard).
Inputs:
  id: integer (required)
Calls: DELETE /categories/{id}/
Returns: { message: "Category deleted." }
Permission: Manager or Admin
```

---

### 8.5 Products (6 tools)

#### `retailops_list_products`
```yaml
Description: >
  List products with live stock levels. Use the stock filter to
  quickly surface out-of-stock or low-stock items.
Inputs:
  search:           string (optional) — searches SKU, name, description
  category:         integer (optional) — filter by category ID
  is_active:        boolean (optional)
  stock:            enum ["out", "low", "ok"] (optional)
  unit_of_measure:  enum ["piece","kg","liter","meter","box","pack"] (optional)
  ordering:         string (optional) — e.g. "unit_price" or "-created_at"
  page:             integer (optional)
  page_size:        integer (optional)
Calls: GET /products/
Returns: paginated list including current_stock, is_low_stock, is_out_of_stock per product
Permission: Any authenticated user
```

#### `retailops_get_product`
```yaml
Description: Retrieve a single product by ID with stock level and category details.
Inputs:
  id: integer (required)
Calls: GET /products/{id}/
Returns: full product object
Permission: Any authenticated user
```

#### `retailops_create_product`
```yaml
Description: Create a new product in the catalog.
Inputs:
  sku:               string (required, unique)
  name:              string (required)
  category_id:       integer (required)
  unit_price:        decimal string (required, must be > 0)
  unit_of_measure:   enum ["piece","kg","liter","meter","box","pack"] (required)
  description:       string (optional)
  low_stock_threshold: integer (optional, default 10)
  is_active:         boolean (optional, default true)
Calls: POST /products/
Returns: created product object (stock will be 0 until inventory movements are recorded)
Permission: Manager or Admin
```

#### `retailops_update_product`
```yaml
Description: Update product details such as price, description, or low-stock threshold.
Inputs:
  id: integer (required)
  + any subset of: name, description, unit_price, low_stock_threshold, is_active, category_id
Calls: PATCH /products/{id}/
Returns: updated product object
Permission: Manager or Admin
```

#### `retailops_delete_product`
```yaml
Description: Delete a product. Fails if any sales orders reference it (PROTECT guard).
Inputs:
  id: integer (required)
Calls: DELETE /products/{id}/
Returns: { message: "Product deleted." }
Permission: Manager or Admin
```

#### `retailops_get_product_movements`
```yaml
Description: >
  Retrieve the full inventory movement history for a product —
  every stock addition, deduction, and adjustment.
Inputs:
  id:        integer (required) — product ID
  page:      integer (optional)
  page_size: integer (optional)
Calls: GET /products/{id}/movements/
Returns: paginated list of movement records (type, quantity, reference, created_by, created_at)
Permission: Any authenticated user
```

---

### 8.6 Inventory Movements (3 tools)

#### `retailops_list_inventory_movements`
```yaml
Description: Query the inventory movement log across all products.
Inputs:
  product:       integer (optional) — filter by product ID
  movement_type: enum ["sale","purchase","adjustment","return"] (optional)
  reference_type: enum ["SalesOrder","PurchaseOrder","ManualAdjustment","Return"] (optional)
  date_from:     date string YYYY-MM-DD (optional)
  date_to:       date string YYYY-MM-DD (optional)
  page:          integer (optional)
  page_size:     integer (optional)
Calls: GET /inventory/
Returns: paginated movement records
Permission: Any authenticated user
```

#### `retailops_get_inventory_movement`
```yaml
Description: Retrieve a single inventory movement record by ID.
Inputs:
  id: integer (required)
Calls: GET /inventory/{id}/
Returns: full movement record
Permission: Any authenticated user
```

#### `retailops_adjust_inventory`
```yaml
Description: >
  Record a manual stock adjustment for a product. Use a positive
  quantity to add stock (e.g. receiving a purchase) or a negative
  quantity to remove stock (e.g. damage write-off). This creates an
  immutable InventoryMovement record with movement_type=adjustment.
Inputs:
  product_id: integer (required)
  quantity:   integer (required, non-zero — positive adds, negative removes)
  notes:      string (optional) — reason for the adjustment
Calls: POST /inventory/adjust/
Returns: created InventoryMovement record
Permission: Manager or Admin
Side effects:
  - Creates InventoryMovement (movement_type=adjustment, reference_type=ManualAdjustment)
  - Immediately affects product.current_stock (computed from all movements)
```

---

### 8.7 Sales Orders (12 tools)

This is the most complex domain. Orders follow a strict status machine and several transitions trigger inventory side effects.

#### `retailops_list_orders`
```yaml
Description: List sales orders with optional filters.
Inputs:
  customer:  integer (optional) — filter by customer ID
  status:    enum ["draft","pending","confirmed","paid","shipped","delivered","cancelled","refunded"] (optional)
  date_from: date string YYYY-MM-DD (optional)
  date_to:   date string YYYY-MM-DD (optional)
  page:      integer (optional)
  page_size: integer (optional)
Calls: GET /orders/
Returns: paginated order list including amount_paid, amount_outstanding per order
Permission: Any authenticated user
```

#### `retailops_get_order`
```yaml
Description: Retrieve a full order including line items, payment history, and computed totals.
Inputs:
  id: integer (required)
Calls: GET /orders/{id}/
Returns:
  - order header (number, status, customer, dates, totals)
  - items: array of { product, quantity, unit_price, tax_rate, line_total }
  - amount_paid, amount_outstanding
  - confirmed_by, created_by (user details)
Permission: Any authenticated user
```

#### `retailops_create_order`
```yaml
Description: >
  Create a new sales order in Draft status. The order will not affect
  stock until it is confirmed. Provide at least one line item.
Inputs:
  customer_id:    integer (required)
  items:          array (required, min 1 item) of:
    product_id:   integer (required)
    quantity:     integer (required, >= 1)
    unit_price:   decimal string (optional — defaults to product.unit_price)
    tax_rate:     decimal string (optional, default 0)
  discount_amount: decimal string (optional, default "0.00")
  tax_amount:      decimal string (optional, default "0.00")
  notes:           string (optional)
Calls: POST /orders/
Returns: created order object with auto-generated order_number (SO-YYYYMMDD-XXXX)
Permission: Staff, Manager, or Admin
Side effects: None (Draft orders do not affect inventory)
```

#### `retailops_update_order`
```yaml
Description: >
  Edit a sales order. Only allowed while the order is in Draft or
  Pending status. Can replace all line items, adjust discount, or
  update notes.
Inputs:
  id: integer (required)
  items: array (optional) — replaces all existing line items if provided
  discount_amount: decimal string (optional)
  notes: string (optional)
Calls: PATCH /orders/{id}/
Returns: updated order object
Permission: Staff, Manager, or Admin
Constraint: Fails with 400 if order status is not Draft or Pending
```

#### `retailops_delete_order`
```yaml
Description: Permanently delete a sales order. Only allowed for Draft orders.
Inputs:
  id: integer (required)
Calls: DELETE /orders/{id}/
Returns: { message: "Order deleted." }
Permission: Staff, Manager, or Admin
Constraint: Fails with 400 if order is not in Draft status
Side effects: Cascades to delete all SalesOrderItem rows
```

#### `retailops_submit_order`
```yaml
Description: >
  Advance an order from Draft to Pending. This signals that the
  order is ready for manager review but has not yet been confirmed
  and does NOT affect inventory.
Inputs:
  id: integer (required)
Calls: POST /orders/{id}/submit/
Returns: updated order object (status: "pending")
Permission: Staff, Manager, or Admin
Constraint: Order must currently be in Draft status
Side effects: None
```

#### `retailops_confirm_order`
```yaml
Description: >
  Confirm a Pending order, advancing it to Confirmed status.
  This is the critical inventory step: stock is deducted for
  every line item by creating negative InventoryMovement records
  (movement_type=sale). This operation is atomic — either all
  stock deductions succeed or none do.
Inputs:
  id: integer (required)
Calls: POST /orders/{id}/confirm/
Returns: updated order object (status: "confirmed")
Permission: Manager or Admin
Constraints:
  - Order must currently be in Pending status
  - Order must have at least one line item
Side effects:
  - Creates one negative InventoryMovement per line item
  - Sets order.confirmed_by and order.confirmed_at
  - Product stock levels decrease immediately
```

#### `retailops_ship_order`
```yaml
Description: Mark a paid order as Shipped.
Inputs:
  id: integer (required)
Calls: POST /orders/{id}/ship/
Returns: updated order object (status: "shipped")
Permission: Staff, Manager, or Admin
Constraint: Order must currently be in Paid status
Side effects: None (inventory already deducted at confirmation)
```

#### `retailops_deliver_order`
```yaml
Description: Mark a shipped order as Delivered (order lifecycle complete).
Inputs:
  id: integer (required)
Calls: POST /orders/{id}/deliver/
Returns: updated order object (status: "delivered")
Permission: Staff, Manager, or Admin
Constraint: Order must currently be in Shipped status
Side effects: None
```

#### `retailops_cancel_order`
```yaml
Description: >
  Cancel a Confirmed order. Stock deductions made at confirmation
  are reversed by creating positive InventoryMovement records
  (movement_type=return). The order cannot be cancelled after payment.
Inputs:
  id: integer (required)
Calls: POST /orders/{id}/cancel/
Returns: updated order object (status: "cancelled")
Permission: Manager or Admin
Constraint: Order must currently be in Confirmed status (before payment)
Side effects:
  - Creates one positive InventoryMovement per line item (restores stock)
  - Product stock levels increase immediately
```

#### `retailops_refund_order`
```yaml
Description: >
  Refund a Paid order. Stock is returned to inventory via
  InventoryMovement records (movement_type=return). This is the
  most destructive transition and is Admin-only.
Inputs:
  id: integer (required)
Calls: POST /orders/{id}/refund/
Returns: updated order object (status: "refunded")
Permission: Admin only
Constraint: Order must currently be in Paid status
Side effects:
  - Creates one positive InventoryMovement per line item (restores stock)
  - Product stock levels increase immediately
```

#### `retailops_record_payment`
```yaml
Description: >
  Record a payment against a Confirmed order. If the running total
  of payments meets or exceeds the order total, the order automatically
  transitions to Paid status. Payments are immutable financial records.
Inputs:
  sales_order_id:   integer (required) — must be a Confirmed order
  amount:           decimal string (required, > 0)
  payment_method:   enum ["cash","bank_transfer","card","check","other"] (required)
  reference_number: string (optional) — e.g. cheque number, bank ref
  notes:            string (optional)
Calls: POST /payments/
Returns: created payment object with auto-generated payment_number (PAY-YYYYMMDD-XXXX)
Permission: Any authenticated user
Side effects:
  - Creates immutable Payment record
  - If sum(payments) >= order.total_amount: order.status → "paid", order.paid_at = now()
  - The transition to Paid uses SELECT FOR UPDATE to prevent concurrent double-transitions
```

---

### 8.8 Payments (3 tools)

*Note: `retailops_record_payment` is listed under Orders because its primary effect is on order status.*

#### `retailops_list_payments`
```yaml
Description: Query the payment ledger.
Inputs:
  sales_order:    integer (optional) — filter by order ID
  payment_method: enum ["cash","bank_transfer","card","check","other"] (optional)
  date_from:      date string YYYY-MM-DD (optional)
  date_to:        date string YYYY-MM-DD (optional)
  page:           integer (optional)
  page_size:      integer (optional)
Calls: GET /payments/
Returns: paginated payment records
Permission: Any authenticated user
```

#### `retailops_get_payment`
```yaml
Description: Retrieve a single payment record by ID.
Inputs:
  id: integer (required)
Calls: GET /payments/{id}/
Returns: full payment record including payment_number, sales_order, recorded_by
Permission: Any authenticated user
```

---

### 8.9 User Management (7 tools) — Admin only

#### `retailops_list_users`
```yaml
Description: List all user accounts with their roles and active status.
Inputs:
  page:      integer (optional)
  page_size: integer (optional)
Calls: GET /users/
Returns: paginated user list
Permission: Admin only
```

#### `retailops_get_user`
```yaml
Description: Retrieve a single user by ID.
Inputs:
  id: integer (required)
Calls: GET /users/{id}/
Returns: user object (no password field)
Permission: Admin only (or the user themselves)
```

#### `retailops_create_user`
```yaml
Description: Create a new user account and assign them a role.
Inputs:
  email:      string (required, unique)
  password:   string (required, >= 8 characters)
  first_name: string (optional)
  last_name:  string (optional)
  role:       integer (required) — Role ID (1=Admin, 2=Manager, 3=Staff)
  is_active:  boolean (optional, default true)
Calls: POST /users/
Returns: created user object
Permission: Admin only
Side effects: User created; password is hashed
```

#### `retailops_update_user`
```yaml
Description: Update a user's profile fields (not password — use retailops_change_password).
Inputs:
  id:         integer (required)
  email:      string (optional)
  first_name: string (optional)
  last_name:  string (optional)
  role:       integer (optional) — Role ID
  is_active:  boolean (optional)
Calls: PATCH /users/{id}/
Returns: updated user object
Permission: Admin only
```

#### `retailops_change_password`
```yaml
Description: Set a new password for a user account.
Inputs:
  id:               integer (required)
  new_password:     string (required, >= 8 characters)
  confirm_password: string (required, must match new_password)
Calls: POST /users/{id}/change-password/
Returns: { message: "Password updated successfully." }
Permission: Admin only
Side effects: Password hash updated; existing tokens remain valid
```

#### `retailops_deactivate_user`
```yaml
Description: >
  Deactivate a user account (soft delete — is_active=False). The user
  can no longer log in. Cannot be used on your own account.
Inputs:
  id: integer (required)
Calls: POST /users/{id}/deactivate/
Returns: { message: "User deactivated." }
Permission: Admin only
Constraint: Cannot deactivate yourself (guard enforced server-side)
Side effects: is_active=False; existing tokens may still exist but login is blocked
```

#### `retailops_reactivate_user`
```yaml
Description: Restore a previously deactivated user account.
Inputs:
  id: integer (required)
Calls: POST /users/{id}/reactivate/
Returns: { message: "User reactivated." }
Permission: Admin only
Side effects: is_active=True
```

---

## 9. Resources

MCP Resources let AI models browse data through URI-addressable read-only endpoints. Resources map directly to GET endpoints and are suitable for "what is in this system?" queries.

| URI Template | Maps To | Description |
|---|---|---|
| `retailops://dashboard` | `GET /dashboard/` | Current business summary |
| `retailops://customers` | `GET /customers/` | All customers (first page) |
| `retailops://customers/{id}` | `GET /customers/{id}/` | Single customer |
| `retailops://products` | `GET /products/` | Product catalog |
| `retailops://products/{id}` | `GET /products/{id}/` | Single product with stock level |
| `retailops://products/{id}/movements` | `GET /products/{id}/movements/` | Movement history |
| `retailops://categories` | `GET /categories/` | Category tree |
| `retailops://orders` | `GET /orders/` | All orders (first page) |
| `retailops://orders/{id}` | `GET /orders/{id}/` | Single order with line items |
| `retailops://payments` | `GET /payments/` | All payments (first page) |
| `retailops://inventory` | `GET /inventory/` | All inventory movements (first page) |
| `retailops://users` | `GET /users/` | User list (Admin token only) |

Resources always return raw JSON from the API. Tools should be preferred when the AI model needs to filter, paginate, or write data.

---

## 10. Prompts (Guided Workflows)

MCP Prompts are reusable instruction templates that guide an AI model through multi-step operations. They do not execute actions directly — they return a structured message that the AI model then uses to invoke the appropriate tools in sequence.

### `retailops_create_order_workflow`
```
Purpose: Guide an AI agent through creating and submitting a complete order.

Steps injected into model context:
1. Call retailops_list_customers to find the customer (or retailops_create_customer if new).
2. Call retailops_list_products with stock="ok" to find available products.
3. Call retailops_create_order with customer_id, items array, and any discount/notes.
4. Confirm the order details with the user.
5. Call retailops_submit_order to move it to Pending.
6. Inform the user the order number and that it awaits manager confirmation.

Arguments: customer_hint (optional name/email to pre-search), product_hints (optional list)
```

### `retailops_process_payment_workflow`
```
Purpose: Record one or more payments against a Confirmed order.

Steps injected into model context:
1. Call retailops_get_order to show current totals and amount_outstanding.
2. Confirm the payment amount and method with the user.
3. Call retailops_record_payment with the details.
4. If amount_outstanding > 0 after payment, ask if another partial payment should be recorded.
5. If the order transitions to Paid, report the payment_number and new order status.

Arguments: order_id (required)
```

### `retailops_cancel_or_refund_workflow`
```
Purpose: Walk through the correct reversal path based on order status.

Steps injected into model context:
1. Call retailops_get_order to check current status.
2. If status=confirmed: explain cancellation restores stock, call retailops_cancel_order.
3. If status=paid: explain refund requires Admin role and restores stock, call retailops_refund_order.
4. If status=draft or pending: advise to delete the order (retailops_delete_order) or use update.
5. If status=shipped/delivered: report that reversal is not supported via the system.

Arguments: order_id (required)
```

### `retailops_stock_check_workflow`
```
Purpose: Surface inventory health across the catalog.

Steps injected into model context:
1. Call retailops_get_dashboard for the low_stock_count summary.
2. Call retailops_list_products with stock="out" to list zero-stock products.
3. Call retailops_list_products with stock="low" to list products below threshold.
4. For each critical product, optionally call retailops_adjust_inventory to record a stock receipt.

Arguments: none
```

### `retailops_onboard_customer_workflow`
```
Purpose: Create a new customer and optionally place their first order.

Steps injected into model context:
1. Collect customer details and call retailops_create_customer.
2. Ask if a first order should be placed now.
3. If yes, follow the retailops_create_order_workflow with the new customer_id pre-filled.

Arguments: none
```

---

## 11. Implementation Steps

This section is the step-by-step build guide. Each step is self-contained and testable.

---

### Step 1 — Create the agent user in RetailOps

Before writing any MCP code, create a dedicated RetailOps account for the MCP server to use. This account's role determines what tools will actually work at runtime.

```bash
python manage.py shell -c "
from core.models import User, Role
role = Role.objects.get(name='Manager')   # or 'Admin' for full access
u = User.objects.create_user(
    email='mcp-agent@retailops.local',
    password='MCPAgentPass123!',
    first_name='MCP',
    last_name='Agent',
)
u.role = role
u.save()
print('Created:', u.email)
"
```

Then obtain the token:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token/ \
     -H "Content-Type: application/json" \
     -d '{"email":"mcp-agent@retailops.local","password":"MCPAgentPass123!"}' \
  | python -m json.tool
```

Copy the `token` value. This goes into `RETAILOPS_API_TOKEN`.

---

### Step 2 — Install dependencies

```bash
pip install "mcp>=1.0.0" "httpx>=0.27.0" "python-dotenv>=1.0.0"
```

Add these to `requirements.txt`:

```
mcp>=1.0.0
httpx>=0.27.0
python-dotenv>=1.0.0
```

---

### Step 3 — Scaffold the directory structure

```bash
mkdir -p mcp_server/tools mcp_server/resources mcp_server/prompts
touch mcp_server/__init__.py
touch mcp_server/server.py mcp_server/config.py mcp_server/client.py mcp_server/errors.py
touch mcp_server/tools/__init__.py
touch mcp_server/resources/__init__.py mcp_server/resources/handlers.py
touch mcp_server/prompts/__init__.py mcp_server/prompts/workflows.py
# One file per domain in tools/
touch mcp_server/tools/auth.py mcp_server/tools/dashboard.py
touch mcp_server/tools/customers.py mcp_server/tools/categories.py
touch mcp_server/tools/products.py mcp_server/tools/inventory.py
touch mcp_server/tools/orders.py mcp_server/tools/payments.py
touch mcp_server/tools/users.py
```

---

### Step 4 — Write `config.py`

Implement the Settings dataclass as shown in Section 5. Create `.env` in the project root:

```
RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1
RETAILOPS_API_TOKEN=<paste token here>
```

---

### Step 5 — Write `errors.py`

Implement `RetailOpsError` and `raise_for_status` as shown in Section 7. This is used by every tool to convert API failures into readable MCP errors.

---

### Step 6 — Write `client.py`

Implement `RetailOpsClient` as shown in Section 6. Key points:
- The client is instantiated once and reused across tool calls.
- `settings.api_token` must be set before any tool is invoked.
- All methods call `raise_for_status(response)` before returning.
- The `delete` method returns `None` (204 No Content) on success.

---

### Step 7 — Write tool modules (one domain at a time)

Each tool module follows this pattern:

```python
# mcp_server/tools/customers.py
from mcp.server.fastmcp import FastMCP
from ..client import RetailOpsClient
from ..errors import RetailOpsError

def register_customer_tools(mcp: FastMCP, client: RetailOpsClient):

    @mcp.tool()
    async def retailops_list_customers(
        search: str = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """List customers with optional search and pagination."""
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        return await client.get("/customers/", params)

    @mcp.tool()
    async def retailops_get_customer(id: int) -> dict:
        """Retrieve a single customer by ID."""
        return await client.get(f"/customers/{id}/")

    @mcp.tool()
    async def retailops_create_customer(
        first_name: str,
        last_name: str,
        email: str,
        phone: str = None,
        address_line1: str = None,
        address_line2: str = None,
        city: str = None,
        state: str = None,
        postal_code: str = None,
        country: str = "United States",
        notes: str = None,
    ) -> dict:
        """Create a new customer record."""
        body = {k: v for k, v in locals().items() if v is not None and k != "client"}
        return await client.post("/customers/", body)

    @mcp.tool()
    async def retailops_update_customer(id: int, **fields) -> dict:
        """Update one or more fields on an existing customer."""
        return await client.patch(f"/customers/{id}/", fields)

    @mcp.tool()
    async def retailops_delete_customer(id: int) -> dict:
        """Delete a customer. Fails with 409 if they have orders."""
        await client.delete(f"/customers/{id}/")
        return {"message": "Customer deleted."}
```

Repeat this pattern for each domain module. The most complex is `orders.py` because it has 12 tools including the 6 transition actions.

**Order transition tool pattern:**
```python
@mcp.tool()
async def retailops_confirm_order(id: int) -> dict:
    """
    Confirm a Pending order (PENDING → CONFIRMED).
    Deducts stock for all line items via InventoryMovement records.
    Requires Manager or Admin role.
    """
    return await client.post(f"/orders/{id}/confirm/")
```

---

### Step 8 — Write `resources/handlers.py`

```python
# mcp_server/resources/handlers.py
from mcp.server.fastmcp import FastMCP
from ..client import RetailOpsClient

def register_resources(mcp: FastMCP, client: RetailOpsClient):

    @mcp.resource("retailops://dashboard")
    async def dashboard_resource() -> str:
        data = await client.get("/dashboard/")
        import json
        return json.dumps(data, indent=2)

    @mcp.resource("retailops://customers")
    async def customers_resource() -> str:
        data = await client.get("/customers/")
        import json
        return json.dumps(data, indent=2)

    @mcp.resource("retailops://customers/{id}")
    async def customer_resource(id: str) -> str:
        data = await client.get(f"/customers/{id}/")
        import json
        return json.dumps(data, indent=2)

    # ... repeat for products, orders, payments, inventory, etc.
```

---

### Step 9 — Write `prompts/workflows.py`

```python
# mcp_server/prompts/workflows.py
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

def register_prompts(mcp: FastMCP):

    @mcp.prompt()
    async def retailops_create_order_workflow(
        customer_hint: str = None,
        product_hints: str = None,
    ) -> list[TextContent]:
        steps = """
You are helping create a RetailOps sales order. Follow these steps:

1. Use retailops_list_customers to find the customer.
   Search hint: {customer_hint}
   If not found, offer to create them with retailops_create_customer.

2. Use retailops_list_products with stock="ok" to find available products.
   Product hints: {product_hints}

3. Confirm the customer and product selection, including quantities and prices.

4. Call retailops_create_order with:
   - customer_id (from step 1)
   - items array (from step 2-3)
   - discount_amount and notes if provided

5. Show the order summary (order_number, total_amount, line items).

6. Ask the user if they want to submit it now.
   If yes, call retailops_submit_order to move it to Pending status.

7. Inform the user: order is now awaiting manager confirmation.
""".format(
            customer_hint=customer_hint or "(not provided)",
            product_hints=product_hints or "(not provided)",
        )
        return [TextContent(type="text", text=steps)]

    # ... repeat for other workflows
```

---

### Step 10 — Write `server.py` (the entry point)

```python
# mcp_server/server.py
from mcp.server.fastmcp import FastMCP
from .client import RetailOpsClient
from .tools.auth import register_auth_tools
from .tools.dashboard import register_dashboard_tools
from .tools.customers import register_customer_tools
from .tools.categories import register_category_tools
from .tools.products import register_product_tools
from .tools.inventory import register_inventory_tools
from .tools.orders import register_order_tools
from .tools.payments import register_payment_tools
from .tools.users import register_user_tools
from .resources.handlers import register_resources
from .prompts.workflows import register_prompts

mcp = FastMCP("RetailOps")
client = RetailOpsClient()

# Register all tools
register_auth_tools(mcp, client)
register_dashboard_tools(mcp, client)
register_customer_tools(mcp, client)
register_category_tools(mcp, client)
register_product_tools(mcp, client)
register_inventory_tools(mcp, client)
register_order_tools(mcp, client)
register_payment_tools(mcp, client)
register_user_tools(mcp, client)

# Register resources and prompts
register_resources(mcp, client)
register_prompts(mcp)

if __name__ == "__main__":
    mcp.run()          # Default: stdio transport
```

---

### Step 11 — Smoke test each tool group

With the Django dev server running (`python manage.py runserver`), test each tool group using the MCP inspector or a simple Python script:

```python
# test_mcp_tools.py  (run standalone, not via Django)
import asyncio
from mcp_server.client import RetailOpsClient
from mcp_server.errors import RetailOpsError

async def main():
    client = RetailOpsClient()

    # Test dashboard
    dash = await client.get("/dashboard/")
    print("Dashboard OK:", dash.keys())

    # Test customer list
    customers = await client.get("/customers/", {"page_size": 5})
    print("Customers OK:", customers["count"], "total")

    # Test order list
    orders = await client.get("/orders/", {"status": "draft", "page_size": 5})
    print("Draft orders:", orders["count"])

    await client.close()

asyncio.run(main())
```

---

### Step 12 — Connect to Claude Desktop

Add the MCP server to Claude Desktop's configuration file:

**On macOS/Linux:** `~/.config/claude-desktop/claude_desktop_config.json`  
**On Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "retailops": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "C:/Users/Workstation/Desktop/test",
      "env": {
        "RETAILOPS_BASE_URL": "http://127.0.0.1:8000/api/v1",
        "RETAILOPS_API_TOKEN": "<your-token-here>"
      }
    }
  }
}
```

Restart Claude Desktop. The RetailOps tools will appear in the tool picker.

---

### Step 13 — Connect via SSE (for remote / multi-client use)

For remote deployments or when multiple clients need to connect simultaneously, run the MCP server in SSE mode:

```python
# Add to server.py
if __name__ == "__main__":
    import os
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "sse":
        mcp.run(transport="sse", host="0.0.0.0", port=8001)
    else:
        mcp.run()  # stdio
```

Run:
```bash
MCP_TRANSPORT=sse python -m mcp_server.server
```

Claude Desktop config for SSE:
```json
{
  "mcpServers": {
    "retailops": {
      "transport": "sse",
      "url": "http://localhost:8001/sse"
    }
  }
}
```

---

## 12. Security Considerations

| Concern | How it is handled |
|---|---|
| **Token exposure** | Token lives only in env vars / `.env` file (never in code or committed files). `.env` must be in `.gitignore`. |
| **Role enforcement** | All role checks happen server-side in the Django API. The MCP layer does not re-implement authorization. |
| **Least privilege** | Create the agent account with the minimum role needed. A read-only agent should use a Staff account; an agent that confirms orders needs Manager. |
| **Token rotation** | Use `retailops_login` to obtain a fresh token periodically. `retailops_logout` revokes the old one. |
| **Sensitive output** | The MCP server never logs passwords. Only the `retailops_login` tool accepts a password and it is not echoed back. |
| **Network isolation** | For local use, the MCP server only talks to `localhost:8000`. For production, restrict `ALLOWED_HOSTS` and use HTTPS. |
| **Audit trail** | Every payment, inventory movement, and order transition records `created_by` / `confirmed_by` — the agent user will appear in these fields, making AI-initiated actions traceable. |

---

## 13. Capability Matrix by Role

This table answers the question: "If I configure the MCP server with a `{Role}` token, what can it actually do?"

| Tool Group | Staff | Manager | Admin |
|---|---|---|---|
| Dashboard | ✅ | ✅ | ✅ |
| Customers (read) | ✅ | ✅ | ✅ |
| Customers (write/delete) | ✅ | ✅ | ✅ |
| Categories (read) | ✅ | ✅ | ✅ |
| Categories (write) | ❌ | ✅ | ✅ |
| Products (read + stock) | ✅ | ✅ | ✅ |
| Products (write) | ❌ | ✅ | ✅ |
| Inventory movements (read) | ✅ | ✅ | ✅ |
| Inventory adjust | ❌ | ✅ | ✅ |
| Orders (read) | ✅ | ✅ | ✅ |
| Orders (create / update / delete Draft) | ✅ | ✅ | ✅ |
| Orders (submit / ship / deliver) | ✅ | ✅ | ✅ |
| Orders (confirm / cancel) | ❌ | ✅ | ✅ |
| Orders (refund) | ❌ | ❌ | ✅ |
| Payments (record) | ✅ | ✅ | ✅ |
| Users (read/write) | ❌ | ❌ | ✅ |
| Roles (read) | ❌ | ❌ | ✅ |

---

## 14. What Is NOT Included (and Why)

| Omitted | Reason |
|---|---|
| Direct database access | The MCP server is API-only. Direct DB access would bypass all business logic (inventory transactions, sequence counters, status guards). |
| HTML views integration | The HTML app exists for human users in browsers. MCP clients consume JSON. The REST API is the correct integration point. |
| Webhook / event push | The MCP protocol is request-response. Push notifications require a separate eventing layer (e.g., Django Channels or Celery signals) not present in the current system. |
| Batch operations | The current API has no bulk endpoints. Each record requires its own tool call. A future `/orders/bulk-confirm/` endpoint could add a `retailops_bulk_confirm_orders` tool. |
| Forgot-password flow | Not implemented in the system (documented gap in CLAUDE.md). |
| Manual stock purchase UI | The `retailops_adjust_inventory` tool with a positive quantity covers this use case via the API. |
