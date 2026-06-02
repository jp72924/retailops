# RetailOps MCP Layer — Technical Guide

## Table of Contents

1. [What is the MCP Layer?](#1-what-is-the-mcp-layer)
2. [Architecture Overview](#2-architecture-overview)
3. [Directory Structure](#3-directory-structure)
4. [Core Components](#4-core-components)
   - 4.1 [config.py — Settings](#41-configpy--settings)
   - 4.2 [errors.py — Error Handling](#42-errorspy--error-handling)
   - 4.3 [client.py — HTTP Client](#43-clientpy--http-client)
   - 4.4 [tools/ — Tool Modules](#44-tools--tool-modules)
   - 4.5 [resources/ — Resource Handlers](#45-resources--resource-handlers)
   - 4.6 [prompts/ — Workflow Prompts](#46-prompts--workflow-prompts)
   - 4.7 [server.py — Entry Point](#47-serverpy--entry-point)
5. [Tool Catalog](#5-tool-catalog)
6. [Resource Catalog](#6-resource-catalog)
7. [Prompt Catalog](#7-prompt-catalog)
8. [Transport Modes](#8-transport-modes)
9. [Request & Response Lifecycle](#9-request--response-lifecycle)
10. [Integration Guide: Local stdio](#10-integration-guide-local-stdio)
11. [Integration Guide: SSE Transport](#11-integration-guide-sse-transport)
12. [Integration Guide: Streamable-HTTP Transport](#12-integration-guide-streamable-http-transport)
13. [Integration Guide: Custom Python Client](#13-integration-guide-custom-python-client)
14. [Integration Guide: LangChain / LangGraph](#14-integration-guide-langchain--langgraph)
15. [Configuration Reference](#15-configuration-reference)
16. [Security Model](#16-security-model)
17. [Error Reference](#17-error-reference)
18. [Troubleshooting](#18-troubleshooting)
19. [MCP Skill Card](#19-mcp-skill-card)

---

## 1. What is the MCP Layer?

**Model Context Protocol (MCP)** is an open standard that defines how AI models and external tools communicate. It provides a structured, typed interface — a contract — between the AI and a service, replacing ad-hoc API calls with a discoverable, schema-validated interaction layer.

The RetailOps MCP layer sits between any AI model (or automation tool) and the RetailOps REST API. It acts as a translation layer:

```
AI Model / External Tool
        │
        │  MCP protocol (JSON-RPC 2.0)
        ▼
  RetailOps MCP Server
        │
        │  HTTP (DRF Token auth)
        ▼
  RetailOps REST API  (/api/v1/)
        │
        │  Django ORM
        ▼
    SQLite Database
```

**Without the MCP layer**, an AI would need to know:
- The exact URL structure of the API
- The authentication mechanism (Token headers)
- The request/response format for every endpoint
- Which business rules to enforce before calling which endpoint

**With the MCP layer**, the AI sees:
- A catalog of named, typed tools with plain-English descriptions
- Guided workflow prompts that walk it through multi-step operations
- Read-only resources for browsing current system state
- Structured error messages that explain what went wrong in actionable terms

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        mcp_server/                                  │
│                                                                     │
│  ┌─────────────┐   ┌──────────────┐   ┌───────────────────────┐   │
│  │  config.py  │   │  errors.py   │   │      client.py        │   │
│  │  (Settings) │   │  (Error map) │   │  (AsyncHTTP wrapper)  │   │
│  └──────┬──────┘   └──────┬───────┘   └───────────┬───────────┘   │
│         │                 │                        │               │
│         └─────────────────┴────────────────────────┘               │
│                                    │                               │
│               ┌────────────────────┼────────────────────┐          │
│               ▼                    ▼                    ▼          │
│         ┌──────────┐        ┌────────────┐      ┌──────────────┐  │
│         │  tools/  │        │ resources/ │      │  prompts/    │  │
│         │ 10 files │        │ handlers.py│      │ workflows.py │  │
│         │ 54 tools │        │ 14 URIs    │      │  5 prompts   │  │
│         └────┬─────┘        └─────┬──────┘      └──────┬───────┘  │
│              │                    │                     │          │
│              └────────────────────┼─────────────────────┘          │
│                                   │                                │
│                          ┌────────▼────────┐                       │
│                          │   server.py     │                       │
│                          │  (FastMCP app)  │                       │
│                          └────────┬────────┘                       │
└───────────────────────────────────┼────────────────────────────────┘
                                    │
              ┌─────────────────────┼──────────────────────┐
              ▼                     ▼                      ▼
         stdio transport       SSE transport    Streamable-HTTP transport
       (Claude Desktop)    (multi-client HTTP)  (production HTTP)
```

**Key design decisions:**

- **Single shared client instance** — all 54 tool closures share one `RetailOpsClient`, while the active RetailOps API token is resolved per request.
- **Flat tool namespace** — every tool is prefixed `retailops_` so there are no naming conflicts when the server is combined with other MCP servers in a multi-server setup.
- **Error surfacing** — API errors are caught at the tool boundary and converted to human-readable `ValueError` messages. The AI receives an explanation, not a raw HTTP traceback.
- **None-param stripping** — optional fields that are not provided are absent from the HTTP request body entirely, satisfying DRF's `allow_null=False` default on optional fields.

---

## 3. Directory Structure

```
mcp_server/
├── __init__.py               # Empty — marks mcp_server as a Python package
├── config.py                 # Environment-driven settings (frozen dataclass)
├── errors.py                 # RetailOpsError + raise_for_status()
├── client.py                 # RetailOpsClient (async HTTP wrapper)
├── server.py                 # FastMCP app + registration + entry point
│
├── tools/
│   ├── __init__.py
│   ├── auth.py               # 3 tools  — login, whoami, logout
│   ├── dashboard.py          # 1 tool   — get_dashboard
│   ├── customers.py          # 5 tools  — list, get, create, update, delete
│   ├── categories.py         # 5 tools  — list, get, create, update, delete
│   ├── products.py           # 6 tools  — list, get, create, update, delete, movements
│   ├── inventory.py          # 4 tools  — list, get, adjust, bulk_adjust
│   ├── orders.py             # 15 tools — list, get, create, update, delete,
│   │                         #            submit, confirm, cancel, ship, deliver,
│   │                         #            refund, record_payment,
│   │                         #            bulk_confirm, bulk_ship, bulk_deliver
│   ├── payments.py           # 2 tools  — list, get
│   ├── settings.py           # 2 tools  — get_system_settings, update_system_settings
│   └── users.py              # 7 tools  — list, get, create, update, deactivate,
│                             #            reactivate, change_password
│
├── resources/
│   └── handlers.py           # 14 URI-addressable read-only resources
│
└── prompts/
    └── workflows.py          # 5 guided workflow prompt templates
```

---

## 4. Core Components

### 4.1 `config.py` — Settings

**Purpose:** Single source of truth for all runtime configuration. Loaded once at import time; immutable thereafter.

```python
@dataclass(frozen=True)
class Settings:
    base_url: str    # e.g. "http://127.0.0.1:8000/api/v1"
    api_token: str   # Local fallback DRF token for stdio sessions
    timeout: float   # HTTP timeout in seconds (default: 30)
    debug: bool      # Log raw HTTP traffic when True
```

Values come exclusively from environment variables. For local development, a `.env` file in the project root is loaded automatically via `python-dotenv`:

```
RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1
RETAILOPS_API_TOKEN=<local-fallback-token>
RETAILOPS_TIMEOUT=30
RETAILOPS_DEBUG=false
```

The `settings` singleton is imported throughout the codebase:

```python
from .config import settings

# Use anywhere:
settings.base_url        # "http://127.0.0.1:8000/api/v1"
settings.api_token       # "<local-fallback-token>"
settings.debug           # False
```

---

### 4.2 `errors.py` — Error Handling

**Purpose:** Normalise all non-2xx API responses into typed Python exceptions with actionable user messages.

The RetailOps API always returns errors in this envelope:

```json
{
  "error": "A human-readable description",
  "code":  "machine_readable_code",
  "details": { "field_name": ["error message"] }
}
```

`raise_for_status()` inspects every HTTP response and raises `RetailOpsError` for any non-2xx status. It handles non-JSON bodies (e.g. HTML error pages from proxies) gracefully.

`RetailOpsError.user_message()` maps status codes and API error codes to messages suitable for return directly from an MCP tool:

| HTTP Status | Code | Message returned |
|---|---|---|
| 401 | `authentication_failed` | Check the effective MCP token: HTTP Bearer, local login token, or RETAILOPS_API_TOKEN |
| 403 | `permission_denied` | Insufficient role for this action |
| 404 | `not_found` | Resource does not exist, check ID |
| 409 | `conflict` | Record blocked by a dependent record |
| 400 | (any) | Field-level validation errors, joined with `;` |
| 500 | `server_error` | Check Django dev server logs |
| any | any | Raw error + status code fallback |

Tool modules catch `RetailOpsError` at their boundary and re-raise as `ValueError`:

```python
try:
    return await client.get(f"/customers/{id}/")
except RetailOpsError as e:
    raise ValueError(e.user_message())
```

`ValueError` is what FastMCP surfaces to the AI as a tool error — so the AI receives the human-readable message, not a raw Python traceback.

---

### 4.3 `client.py` — HTTP Client

**Purpose:** Single async wrapper around the RetailOps REST API. All tool closures share one instance created in `server.py`.

The client maintains two underlying `httpx.AsyncClient` instances. All 54 tool closures share one instance created in `server.py`, but authenticated requests build the `Authorization: Token <token>` header dynamically from the MCP request Bearer token, a local `retailops_login` token, or `RETAILOPS_API_TOKEN`.

| Client | Auth header | Used by |
|---|---|---|
| `_http` | Built per request from the active MCP token | All authenticated endpoints |
| `_anon` | None | `POST /auth/token/` (login) only |

**Path normalisation** — `base_url` always ends with `/`, and `_norm(path)` strips leading slashes from paths before passing them to httpx. This ensures correct URL resolution:

```
base_url = "http://127.0.0.1:8000/api/v1/"
path     = "customers/15/"       # after _norm strips leading "/"
result   = "http://127.0.0.1:8000/api/v1/customers/15/"  ✓

# Without the trailing slash on base_url, httpx would produce:
# "http://127.0.0.1:8000/api/customers/15/"  ✗  (replaces last segment)
```

**None-value stripping** — `_clean_params()` and `_clean_body()` remove keys whose value is `None` before the request is sent. This is critical because DRF serializers default to `allow_null=False`, meaning sending `{"phone": null}` produces a validation error, while omitting `phone` entirely treats it as "not provided":

```python
# Tool is called with phone=None (user didn't provide it)
await client.post("/customers/", {
    "first_name": "Jane",
    "phone": None,        # ← stripped before sending
})
# Actual HTTP body: {"first_name": "Jane"}  ✓
```

**Public API:**

```python
await client.get(path, params=None)    # → dict
await client.post(path, body=None)     # → dict | None (None on 204)
await client.post_anon(path, body)     # → dict  (no auth header)
await client.patch(path, body)         # → dict
await client.delete(path)              # → None
await client.close()                   # Release connection pools
```

---

### 4.4 `tools/` — Tool Modules

**Purpose:** Expose RetailOps operations as callable MCP tools. Each file corresponds to one business domain.

Every tool module exports a single registration function:

```python
def register_<domain>_tools(mcp: FastMCP, client: RetailOpsClient) -> None:
    @mcp.tool()
    async def retailops_<action>(...) -> dict:
        """Docstring shown to the AI as the tool description."""
        ...
```

The `@mcp.tool()` decorator:
1. Reads the function signature to build a JSON Schema for the tool's input parameters
2. Reads the docstring to generate the description the AI uses when deciding whether to call the tool
3. Registers the function so it is callable via the MCP protocol

**Pattern used in every tool:**

```python
@mcp.tool()
async def retailops_get_customer(id: int) -> dict:
    """
    Retrieve a single customer by their numeric ID.

    Args:
        id: The customer's numeric ID (from list_customers or create_customer).
    """
    try:
        return await client.get(f"/customers/{id}/")
    except RetailOpsError as e:
        raise ValueError(e.user_message())
```

**Client-side guards** — some tools validate arguments before hitting the API to catch errors that would produce confusing server responses:

```python
# inventory.py
if quantity == 0:
    raise ValueError("quantity must be non-zero. Use a positive integer to add stock or negative to remove it.")

# orders.py
if not items:
    raise ValueError("items must contain at least one line item.")
```

**Tool module summary:**

| File | Tools | Key operations |
|---|---|---|
| `auth.py` | 3 | Login (public endpoint), whoami, logout (revoke token) |
| `dashboard.py` | 1 | Summary counts and recent activity |
| `settings.py` | 2 | Get and update system-wide currency settings |
| `customers.py` | 5 | Full CRUD on customer records |
| `categories.py` | 5 | Full CRUD on product categories |
| `products.py` | 6 | Full CRUD + inventory movement history per product |
| `inventory.py` | 4 | List movements, get single movement, single and bulk stock adjustments |
| `orders.py` | 15 | Full order lifecycle + payment recording + bulk confirm/ship/deliver |
| `payments.py` | 2 | List payments, get single payment |
| `users.py` | 7 | Full CRUD + deactivate/reactivate/change-password (Admin only) |

---

### 4.5 `resources/` — Resource Handlers

**Purpose:** Provide read-only, URI-addressable views of RetailOps data. Resources are used differently from tools — they are retrieved by URI, not by function call, and are intended for *reading current state* rather than *performing actions*.

Resources are registered with `@mcp.resource("retailops://<uri>")`. They return a string (formatted JSON).

```python
@mcp.resource("retailops://customers/{id}")
async def resource_customer(id: str) -> str:
    try:
        return _pretty(await client.get(f"/customers/{id}/"))
    except RetailOpsError as e:
        return _error_text(e)
```

Note that resources return error text rather than raising exceptions — by convention, a resource URI that cannot be resolved returns a descriptive string rather than failing completely.

**Registered resources:**

| URI | Returns |
|---|---|
| `retailops://dashboard` | Dashboard summary (counts, recent activity) |
| `retailops://customers` | Paginated customer list |
| `retailops://customers/{id}` | Single customer record |
| `retailops://products` | Paginated product list (all stocks) |
| `retailops://products/{id}` | Single product record with computed stock |
| `retailops://products/{id}/movements` | First 25 inventory movements for a specific product |
| `retailops://categories` | All product categories |
| `retailops://orders` | Paginated order list |
| `retailops://orders/{id}` | Single order with all line items |
| `retailops://payments` | Paginated payment list |
| `retailops://inventory` | Paginated inventory movement log (all products) |
| `retailops://users` | Paginated user list (Admin only) |

---

### 4.6 `prompts/` — Workflow Prompts

**Purpose:** Provide guided, multi-step instruction text that the AI model reads and then executes by calling the relevant tools in the documented order. Prompts are *not* tools — they do not call the API. They return structured instruction text.

Each prompt returns `list[dict]` with `role="user"` and a content string:

```python
@mcp.prompt()
async def retailops_create_order_workflow(
    customer_hint: Optional[str] = None,
    product_hints: Optional[str] = None,
) -> list[dict]:
    """
    Guide an AI agent through creating and submitting a complete sales order.
    """
    steps = f"""
STEP 1 — Find or create the customer
  Call retailops_list_customers with search="{customer_hint or '...'}"
  ...
""".strip()
    return [{"role": "user", "content": steps}]
```

**Registered prompts:**

| Prompt | Purpose | Parameters |
|---|---|---|
| `retailops_create_order_workflow` | End-to-end order creation and submission | `customer_hint`, `product_hints` |
| `retailops_process_payment_workflow` | Record one or more payments on a confirmed order | `order_id` |
| `retailops_cancel_or_refund_workflow` | Choose the correct reversal path based on order status | `order_id` |
| `retailops_stock_check_workflow` | Full inventory health check | none |
| `retailops_onboard_customer_workflow` | Create a new customer and optionally place first order | none |

---

### 4.7 `server.py` — Entry Point

**Purpose:** Wire all components together and expose the MCP server to clients.

`server.py` does five things in order:

1. **Reads transport configuration** from environment variables (`MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`)
2. **Creates the `FastMCP` app** with the server name and system instructions
3. **Creates the shared `RetailOpsClient`** instance
4. **Registers all tools, resources, and prompts** by calling each `register_*` function
5. **Runs the server** with graceful shutdown (client connection pools are closed on exit)

```python
mcp = FastMCP(
    name="RetailOps",
    instructions="You are connected to RetailOps — a unified retail and e-commerce order "
                 "management system...",
    host=_host,   # for SSE/streamable-http
    port=_port,
)

client = RetailOpsClient()

register_auth_tools(mcp, client)
# ... 8 more register calls ...
register_resources(mcp, client)
register_prompts(mcp)

mcp.run(transport=_transport)
```

The `instructions` string is included in every MCP session initialization response. It tells the connected AI model what system it is talking to and what kinds of operations are available — serving the same role as a system prompt.

---

## 5. Tool Catalog

Complete listing of all 54 tools with their signatures and role requirements.

### Auth (3 tools)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_login` | `email: str`, `password: str`, `activate: bool=True` | None (public) | Obtain a RetailOps API token; activates it only for stdio/local sessions |
| `retailops_whoami` | — | Any authenticated | Show the current effective RetailOps identity and token source |
| `retailops_logout` | `revoke_env_token: bool=False` | Any authenticated | Safely revoke the current effective token |

### Dashboard (1 tool)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_get_dashboard` | — | Any authenticated | Summary counts and 5 most recent orders/payments |

### Settings (2 tools)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_get_system_settings` | — | Any authenticated | Returns all 8 currency settings: `currency_code`, `currency_symbol`, `decimal_places`, `secondary_currency_enabled`, `secondary_currency_code`, `secondary_currency_symbol`, `secondary_decimal_places`, `secondary_exchange_rate` |
| `retailops_update_system_settings` | `currency_code?`, `currency_symbol?`, `decimal_places?`, `secondary_currency_enabled?`, `secondary_currency_code?`, `secondary_currency_symbol?`, `secondary_decimal_places?`, `secondary_exchange_rate?` | Manager+ | Partial update of currency display settings. `secondary_exchange_rate` is a string (e.g. `"36.5"`) to preserve decimal precision; validated as `> 0` before sending. When enabling secondary currency, `secondary_currency_symbol` must be non-empty. |

### Customers (5 tools)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_list_customers` | `search?`, `page?`, `page_size?` | Any | Paginated list with optional search |
| `retailops_get_customer` | `id: int` | Any | Single customer by ID |
| `retailops_create_customer` | `first_name`, `last_name`, `email`, `phone?`, `national_id?`, `date_of_birth?`, `gender?`, `address_line1?`, `address_line2?`, `city?`, `state?`, `postal_code?`, `country?`, `notes?` | Any | Create a new customer. `national_id` must be unique. `gender` is `"M"` or `"F"`. |
| `retailops_update_customer` | `id: int`, + any customer field | Any | Partial update (PATCH) |
| `retailops_delete_customer` | `id: int` | Any | Delete — returns 409 if customer has orders |

### Categories (5 tools)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_list_categories` | `page?`, `page_size?` | Any | All categories |
| `retailops_get_category` | `id: int` | Any | Single category by ID |
| `retailops_create_category` | `name`, `description?`, `parent_category_id?` | Manager+ | Create a category |
| `retailops_update_category` | `id: int`, + any category field | Manager+ | Partial update |
| `retailops_delete_category` | `id: int` | Manager+ | Delete category |

### Products (6 tools)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_list_products` | `stock?` (`out`/`low`/`ok`), `search?`, `page?`, `page_size?` | Any | List with stock filter |
| `retailops_get_product` | `id: int` | Any | Single product with computed `current_stock` |
| `retailops_create_product` | `sku`, `name`, `category_id`, `unit_price`, `unit_of_measure`, `low_stock_threshold?`, `description?`, `is_active?` | Manager+ | Create product |
| `retailops_update_product` | `id: int`, + any product field | Manager+ | Partial update |
| `retailops_delete_product` | `id: int` | Manager+ | Delete — returns 409 if product has inventory movements |
| `retailops_get_product_movements` | `id: int`, `page?`, `page_size?` | Any | Inventory movement history for a product |

### Inventory (4 tools)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_list_inventory_movements` | `product?`, `movement_type?`, `date_from?`, `date_to?`, `page?`, `page_size?` | Any | Filtered movement log |
| `retailops_get_inventory_movement` | `id: int` | Any | Single movement record |
| `retailops_adjust_inventory` | `product_id: int`, `quantity: int` (non-zero), `notes?` | Manager+ | Manual stock adjustment |
| `retailops_bulk_adjust_inventory` | `adjustments: list` (`{product_id, quantity, notes?}[]`) | Manager+ | Adjust multiple products; partial-success response |

### Orders (15 tools)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_list_orders` | `customer?`, `status?`, `date_from?`, `date_to?`, `page?`, `page_size?` | Any | Filtered order list |
| `retailops_get_order` | `id: int` | Any | Single order with line items |
| `retailops_create_order` | `customer_id: int`, `items: list`, `discount_amount?`, `tax_amount?`, `notes?` | Staff+ | Create draft order |
| `retailops_update_order` | `id: int`, + any updatable field | Staff+ (Draft only) | Partial update on draft orders |
| `retailops_delete_order` | `id: int` | Staff+ (Draft only) | Permanently delete a draft order |
| `retailops_submit_order` | `id: int` | Staff+ | Draft → Pending |
| `retailops_confirm_order` | `id: int` | Manager+ | Pending → Confirmed (deducts stock) |
| `retailops_cancel_order` | `id: int` | Manager+ | Confirmed → Cancelled (restores stock) |
| `retailops_ship_order` | `id: int` | Staff+ | Paid → Shipped |
| `retailops_deliver_order` | `id: int` | Staff+ | Shipped → Delivered |
| `retailops_refund_order` | `id: int` | Admin only | Paid → Refunded (restores stock) |
| `retailops_record_payment` | `sales_order_id: int`, `amount: str`, `payment_method: str`, `reference_number?`, `notes?` | Any | Record a payment; auto-transitions to Paid when fully paid |
| `retailops_bulk_confirm_orders` | `order_ids: list[int]` | Manager+ | Confirm multiple Pending orders; partial-success response |
| `retailops_bulk_ship_orders` | `order_ids: list[int]` | Manager+ | Ship multiple Paid orders; partial-success response |
| `retailops_bulk_deliver_orders` | `order_ids: list[int]` | Manager+ | Deliver multiple Shipped orders; partial-success response |

### Payments (2 tools)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_list_payments` | `sales_order?`, `payment_method?`, `date_from?`, `date_to?`, `page?`, `page_size?` | Any | Filtered payment list |
| `retailops_get_payment` | `id: int` | Any | Single payment record |

### Users (7 tools)

| Tool | Parameters | Role required | Description |
|---|---|---|---|
| `retailops_list_users` | `page?`, `page_size?` | Admin only | All users |
| `retailops_get_user` | `id: int` | Admin only | Single user |
| `retailops_create_user` | `email`, `password`, `role?`, `first_name?`, `last_name?` | Admin only | Create user |
| `retailops_update_user` | `id: int`, + any updatable field | Admin only | Partial update |
| `retailops_deactivate_user` | `id: int` | Admin only | Soft-deactivate (no login, record preserved) |
| `retailops_reactivate_user` | `id: int` | Admin only | Re-enable a deactivated user |
| `retailops_change_password` | `id: int`, `new_password: str`, `confirm_password: str` | Admin only | Reset any user's password (old password not required) |

---

## 6. Resource Catalog

Resources are read-only and fetched by URI rather than function call. They return pretty-printed JSON.

```
retailops://dashboard
retailops://customers
retailops://customers/{id}
retailops://products
retailops://products/{id}
retailops://products/{id}/movements
retailops://categories
retailops://orders
retailops://orders/{id}
retailops://payments
retailops://inventory
retailops://users
```

In Claude Desktop, resources appear in the "Attach" panel. In custom clients, they are accessed via the MCP `resources/read` method with the URI as the argument.

---

## 7. Prompt Catalog

Prompts are invoked by name and return structured instruction text that the AI uses as a guide to call subsequent tools.

```
retailops_create_order_workflow(customer_hint?, product_hints?)
retailops_process_payment_workflow(order_id)
retailops_cancel_or_refund_workflow(order_id)
retailops_stock_check_workflow()
retailops_onboard_customer_workflow()
```

In Claude Desktop, prompts appear in the `/` slash-command menu. In custom clients, they are accessed via the MCP `prompts/get` method.

---

## 8. Transport Modes

The MCP server supports three transport modes, controlled by the `MCP_TRANSPORT` environment variable.

### stdio (default)

```
MCP client ←──stdin/stdout──→ MCP server process
```

- The MCP server is a child process of the client
- Communication is over stdin/stdout using JSON-RPC 2.0
- The client spawns and owns the server process
- Best for: Claude Desktop, local development, single-client setups

**Start command:**
```bash
python -m mcp_server.server
```

### SSE (Server-Sent Events)

```
MCP client ──HTTP GET /sse──→  MCP server (persistent stream)
MCP client ──HTTP POST /messages/?session_id=...──→  MCP server
```

- The server runs as a persistent HTTP process on `host:port`
- Clients establish a long-lived SSE connection for receiving responses
- Clients send requests via POST to a session-specific messages endpoint
- Multiple clients can connect simultaneously
- Best for: multi-user setups, remote access, development with multiple tools

**Start command:**
```bash
MCP_TRANSPORT=sse python -m mcp_server.server
# Endpoint: http://127.0.0.1:8001/sse

# Accept remote connections only with MCP auth and HTTPS public metadata:
MCP_TRANSPORT=sse MCP_HOST=0.0.0.0 MCP_PORT=8001 \
  MCP_AUTH_MODE=retailops-token \
  MCP_PUBLIC_BASE_URL=https://mcp.example.com \
  MCP_ALLOWED_HOSTS=mcp.example.com \
  python -m mcp_server.server
```

### Streamable-HTTP

```
MCP client ──HTTP POST /mcp──→  MCP server (stateful session)
```

- Defined in the MCP spec as of 2025-03-26
- Single bidirectional HTTP endpoint; responses may be streamed via SSE
- More efficient than the SSE transport for request/response operations
- Preferred for production deployments
- Best for: remote or cloud deployments, production use

**Start command:**
```bash
MCP_TRANSPORT=streamable-http python -m mcp_server.server
# Endpoint: http://127.0.0.1:8001/mcp

# Remote streamable-HTTP uses the same MCP_AUTH_MODE=retailops-token guard.
```

---

## 9. Request & Response Lifecycle

Understanding what happens between the AI calling a tool and receiving a result:

```
1. AI decides to call "retailops_get_order" with {"id": 42}
        │
        │  MCP protocol (JSON-RPC 2.0)
        ▼
2. MCP server receives: tools/call { name: "retailops_get_order", arguments: {id: 42} }
        │
        │  FastMCP dispatches to registered function
        ▼
3. retailops_get_order(id=42) is called
        │
        │  client.get("/orders/42/")
        ▼
4. RetailOpsClient:
   a. _norm("/orders/42/")  →  "orders/42/"
   b. httpx.AsyncClient.get("orders/42/", headers={"Authorization": "Token ..."})
   c. Resolves against base_url: "http://127.0.0.1:8000/api/v1/orders/42/"
        │
        │  HTTP GET
        ▼
5. Django REST Framework:
   a. Authenticates token → resolves to the RetailOps user represented by the current MCP token
   b. Permission check: IsAuthenticated → passes
   c. OrderViewSet.retrieve(pk=42) → serializes order + line items
   d. Returns 200 {"id": 42, "order_number": "SO-20260410-0001", ...}
        │
        │  HTTP 200 JSON
        ▼
6. RetailOpsClient:
   a. raise_for_status() → is_success=True, no exception
   b. return r.json()  →  {"id": 42, "order_number": ...}
        │
        │  Python dict
        ▼
7. retailops_get_order returns the dict
        │
        │  FastMCP serializes to JSON
        ▼
8. MCP server sends: tools/call response { content: [{ type: "text", text: "{...}" }] }
        │
        │  MCP protocol
        ▼
9. AI receives the order data and continues
```

**Error path** (e.g. order not found):

```
Step 5d returns 404 {"error": "Not found.", "code": "not_found"}
        │
Step 6a raise_for_status() raises RetailOpsError(status=404, code="not_found")
        │
Step 7  except RetailOpsError as e: raise ValueError(e.user_message())
          ValueError: "Not found: the requested resource does not exist. Check the ID you provided."
        │
Step 8  FastMCP sends: tools/call response { isError: true, content: [{type: "text", text: "Not found: ..."}] }
        │
Step 9  AI receives the error text and explains the situation to the user
```

---

## 10. Integration Guide: Local stdio

This is the recommended setup for local single-client use. MCP clients that
support stdio spawn the RetailOps MCP process and communicate over stdin/stdout.
Claude Desktop supports this local-server flow on macOS and Windows through its
documented `claude_desktop_config.json` file. On Linux, use the same command
with an MCP client that supports stdio, or use the SSE/streamable-HTTP
transports below.

### Prerequisites

- An MCP client that supports local stdio servers
- Claude Desktop installed, if configuring Claude Desktop on macOS or Windows
- Python environment with MCP dependencies installed
- RetailOps Django dev server running (`python manage.py runserver`)

### Step 1: Verify the server starts

```bash
cd /home/<user>/retailops
python -m mcp_server.server
```

You should see:
```
Starting RetailOps MCP Server
  Transport : stdio
  API URL   : http://127.0.0.1:8000/api/v1
  Auth mode : local
  API token : runtime login / RETAILOPS_API_TOKEN fallback
```

If the server starts and waits (no error), it is ready. Press Ctrl+C to stop.

### Step 2: Locate `claude_desktop_config.json` for Claude Desktop

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Claude Desktop's local config path is documented for macOS and Windows. For
Linux, prefer a custom MCP client, Claude Code/Codex-style MCP configuration,
or one of the HTTP transports in this guide.

### Step 3: Add the MCP server entry

Create or update the `mcpServers` key:

```json
{
  "preferences": { ... },
  "mcpServers": {
    "retailops": {
      "command": "/path/to/retailops/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/retailops",
      "env": {
        "RETAILOPS_BASE_URL": "http://127.0.0.1:8000/api/v1",
        "RETAILOPS_API_TOKEN": "<local-fallback-token>"
      }
    }
  }
}
```

**Configuration fields:**

| Field | Purpose |
|---|---|
| `command` | The executable to run. Use the full path to a venv Python if needed: `"/path/to/retailops/.venv/bin/python"` |
| `args` | Arguments passed to the command. `-m mcp_server.server` runs the package as a module. |
| `cwd` | Working directory. Must be the project root so Python can resolve `mcp_server` as a package. |
| `env` | Environment variables injected into the server process. Sensitive values go here; they are not checked into source control. |

> **Note on virtual environments:** If you are using a venv, replace `"python"` with the absolute path to the venv interpreter:
> ```json
> "command": "/path/to/retailops/.venv/bin/python"
> ```

> **Windows equivalent:** use a path such as
> `"C:/Users/<user>/retailops/.venv/Scripts/python.exe"` and set `"cwd"` to
> `"C:/Users/<user>/retailops"`.

### Step 4: Restart Claude Desktop

Fully quit and relaunch Claude Desktop so it reloads the config. It will spawn
the MCP server process on the next conversation.

### Step 5: Verify the connection

In a new Claude conversation, open the tools indicator and confirm the `retailops_*` tools are listed.

You can test with a simple prompt:
```
Use the retailops_get_dashboard tool to show me the current system summary.
```

### Step 6: Using workflow prompts

In the chat input, type `/` to see available prompt templates:
```
/retailops_create_order_workflow
/retailops_stock_check_workflow
/retailops_onboard_customer_workflow
```

Select one to pre-fill a guided instruction set.

### Troubleshooting stdio setup

| Symptom | Cause | Fix |
|---|---|---|
| Tools don't appear | Server failed to start | Check Claude Desktop logs: Help → Show Logs |
| Server exits immediately | Import error or missing dependency | Run `python -m mcp_server.server` manually and read the traceback |
| All tools return auth errors | No active RetailOps token, or the token was revoked | Run `retailops_login`, verify `RETAILOPS_API_TOKEN`, or pass `Authorization: Bearer <token>` for HTTP transports |
| `cwd` not found | Wrong path in config | Use forward slashes, ensure path exists |

---

## 11. Integration Guide: SSE Transport

Use the SSE transport when you want the MCP server running as a persistent process that multiple clients can connect to simultaneously, or when your client does not support spawning child processes.

### Step 1: Start the SSE server

```bash
# Local access only (loopback)
MCP_TRANSPORT=sse python -m mcp_server.server

# Remote / LAN access, publish behind HTTPS reverse proxy
MCP_TRANSPORT=sse MCP_HOST=0.0.0.0 MCP_PORT=8001 \
  MCP_AUTH_MODE=retailops-token \
  MCP_PUBLIC_BASE_URL=https://mcp.example.com \
  MCP_ALLOWED_HOSTS=mcp.example.com \
  python -m mcp_server.server
```

Output:
```
Starting RetailOps MCP Server
  Transport : sse
  API URL   : http://127.0.0.1:8000/api/v1
  Auth mode : retailops-token
  API token : per-client MCP Bearer token
  Listening : http://127.0.0.1:8001
  Endpoint  : http://127.0.0.1:8001/sse
```

Remote SSE clients must send `Authorization: Bearer <RetailOps API token>`.
For loopback-only development, `MCP_AUTH_MODE=local` can still use
`RETAILOPS_API_TOKEN` or a token activated by `retailops_login`.

### Step 2: Verify the SSE endpoint

```bash
curl -N http://127.0.0.1:8001/sse
```

You should receive an SSE stream with the session endpoint:
```
event: endpoint
data: /messages/?session_id=<uuid>
```

### Step 3: Connect an MCP client to the SSE server

Use an MCP client that supports SSE and configure it with the running endpoint:

```json
{
  "url": "http://127.0.0.1:8001/sse",
  "headers": {
    "Authorization": "Bearer <retailops-api-token>"
  }
}
```

Do not put this `url` form into Claude Desktop's local
`claude_desktop_config.json` unless your MCP client explicitly documents remote
server URLs there. Claude Desktop local server configuration uses `command`,
`args`, and `cwd`; Claude remote connectors are configured through Claude's
connector UI/infrastructure, not by committing local config files.

### Step 4: Connect from a custom MCP client (Python)

```python
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

async def main():
    headers = {"Authorization": "Bearer <retailops-api-token>"}
    async with sse_client("http://127.0.0.1:8001/sse", headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            print([t.name for t in tools.tools])

            # Call a tool
            result = await session.call_tool(
                "retailops_get_dashboard", {}
            )
            print(result.content[0].text)

asyncio.run(main())
```

---

## 12. Integration Guide: Streamable-HTTP Transport

The streamable-HTTP transport uses a single persistent POST endpoint and is preferred for production or cloud deployments.

### Step 1: Start the server

```bash
MCP_TRANSPORT=streamable-http python -m mcp_server.server
# Endpoint: http://127.0.0.1:8001/mcp

# Production (remote access behind HTTPS reverse proxy):
MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8001 \
  MCP_AUTH_MODE=retailops-token \
  MCP_PUBLIC_BASE_URL=https://mcp.example.com \
  MCP_ALLOWED_HOSTS=mcp.example.com \
  python -m mcp_server.server
```

### Step 2: Verify the endpoint

```bash
curl -X POST http://127.0.0.1:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer <retailops-api-token>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "test", "version": "1.0"}
    }
  }'
```

A successful response includes:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "serverInfo": {"name": "RetailOps", "version": "1.27.0"},
    "capabilities": {
      "tools": {},
      "resources": {},
      "prompts": {}
    }
  }
}
```

### Step 3: Connect from a custom Python client

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    headers = {"Authorization": "Bearer <retailops-api-token>"}
    async with streamablehttp_client("http://127.0.0.1:8001/mcp", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(
                "retailops_list_orders",
                {"status": "confirmed", "page_size": 10}
            )
            print(result.content[0].text)

asyncio.run(main())
```

---

## 13. Integration Guide: Custom Python Client

This section demonstrates a complete standalone Python script that connects to the MCP server (using SSE or streamable-HTTP) and exercises multiple tools without Claude Desktop.

```python
"""
retailops_client_example.py
---------------------------
Example: retrieve dashboard, list low-stock products,
and place a draft order — all via the MCP protocol.
"""

import asyncio
import json

from mcp import ClientSession
from mcp.client.sse import sse_client  # or streamablehttp_client


def parse(result) -> dict | list:
    """Extract the first content block from a tool call result."""
    return json.loads(result.content[0].text)


async def main():
    # Connect to the running SSE server
    async with sse_client("http://127.0.0.1:8001/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ── 1. Dashboard ─────────────────────────────────────────────
            dashboard = parse(await session.call_tool(
                "retailops_get_dashboard", {}
            ))
            print(f"Orders this month: {dashboard['orders_this_month']}")
            print(f"Low stock count:   {dashboard['low_stock_count']}")

            # ── 2. Low-stock products ────────────────────────────────────
            low = parse(await session.call_tool(
                "retailops_list_products", {"stock": "low", "page_size": 5}
            ))
            print(f"\nLow-stock products ({low['count']} total):")
            for p in low["results"]:
                print(f"  [{p['sku']}] {p['name']} — stock: {p['current_stock']}")

            # ── 3. Create a draft order ──────────────────────────────────
            customers = parse(await session.call_tool(
                "retailops_list_customers", {"search": "Jane", "page_size": 1}
            ))
            if not customers["results"]:
                print("No customer found matching 'Jane'")
                return

            customer_id = customers["results"][0]["id"]
            products = parse(await session.call_tool(
                "retailops_list_products", {"stock": "ok", "page_size": 1}
            ))
            product = products["results"][0]

            order = parse(await session.call_tool(
                "retailops_create_order", {
                    "customer_id": customer_id,
                    "items": [
                        {
                            "product_id": product["id"],
                            "quantity": 2,
                            "unit_price": product["unit_price"],
                        }
                    ],
                    "notes": "Created via MCP client example",
                }
            ))
            print(f"\nCreated order: {order['order_number']} (status: {order['status']})")
            print(f"  Total: {order['total_amount']}")

            # ── 4. Read a resource ───────────────────────────────────────
            resource = await session.read_resource(
                f"retailops://orders/{order['id']}"
            )
            order_data = json.loads(resource.contents[0].text)
            print(f"\nOrder via resource URI:")
            print(f"  Items: {len(order_data['items'])}")


asyncio.run(main())
```

### Running the example

```bash
# Terminal 1: Django dev server
python manage.py runserver

# Terminal 2: MCP server (SSE transport)
MCP_TRANSPORT=sse python -m mcp_server.server

# Terminal 3: Your client script
python retailops_client_example.py
```

---

## 14. Integration Guide: LangChain / LangGraph

The MCP standard is becoming widely supported across AI frameworks. Here is how to connect the RetailOps MCP server to a LangChain or LangGraph agent.

### Install the MCP-LangChain adapter

```bash
pip install langchain-mcp-adapters
```

### LangChain example

```python
import asyncio
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate
from mcp import ClientSession
from mcp.client.sse import sse_client


async def main():
    async with sse_client("http://127.0.0.1:8001/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Load all 54 RetailOps tools as LangChain tools
            tools = await load_mcp_tools(session)

            model = ChatAnthropic(model="claude-opus-4-6")
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a RetailOps assistant. Use the available tools."),
                ("human", "{input}"),
                ("placeholder", "{agent_scratchpad}"),
            ])

            agent = create_tool_calling_agent(model, tools, prompt)
            executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

            result = await executor.ainvoke({
                "input": "Show me all confirmed orders and tell me how many there are."
            })
            print(result["output"])


asyncio.run(main())
```

### LangGraph example

```python
import asyncio
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic
from mcp import ClientSession
from mcp.client.sse import sse_client


async def main():
    async with sse_client("http://127.0.0.1:8001/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await load_mcp_tools(session)
            model = ChatAnthropic(model="claude-opus-4-6")

            graph = create_react_agent(model, tools)

            result = await graph.ainvoke({
                "messages": [{
                    "role": "user",
                    "content": "Check inventory health: list all out-of-stock products."
                }]
            })
            print(result["messages"][-1].content)


asyncio.run(main())
```

---

## 15. Configuration Reference

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `RETAILOPS_BASE_URL` | `http://127.0.0.1:8000/api/v1` | Base URL of the RetailOps REST API |
| `RETAILOPS_API_TOKEN` | `""` | Local fallback DRF token for stdio sessions. HTTP MCP clients should send their own Bearer token. |
| `RETAILOPS_TIMEOUT` | `30` | HTTP request timeout in seconds |
| `RETAILOPS_DEBUG` | `false` | Set to `true` to log all HTTP request/response details |
| `MCP_TRANSPORT` | `stdio` | Transport mode: `stdio`, `sse`, or `streamable-http` |
| `MCP_HOST` | `127.0.0.1` | Bind address for SSE/streamable-http. Set to `0.0.0.0` for remote access. |
| `MCP_PORT` | `8001` | Port for SSE/streamable-http transports |
| `MCP_AUTH_MODE` | `local` | `local` for stdio/loopback development, or `retailops-token` to require MCP Bearer auth. |
| `MCP_PUBLIC_BASE_URL` | `""` | Public HTTPS MCP URL. Required when binding HTTP transports to a non-loopback host. |
| `MCP_ALLOWED_HOSTS` | `""` | Comma-separated Host header allow-list. Required for non-loopback HTTP transports. |
| `MCP_ALLOWED_ORIGINS` | `""` | Optional comma-separated browser origin allow-list. Defaults to `MCP_PUBLIC_BASE_URL` when auth is remote. |
| `MCP_REQUIRED_SCOPES` | `retailops:access` | MCP access scopes required by FastMCP when `MCP_AUTH_MODE=retailops-token`. |

### `.env` file (local development)

Place this file in the project root (next to `manage.py`). It is loaded automatically by `config.py`:

```env
RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1
RETAILOPS_API_TOKEN=<your-local-fallback-token>
RETAILOPS_TIMEOUT=30
RETAILOPS_DEBUG=false
```

`.env` is in `.gitignore` — it should never be committed.

### Obtaining a RetailOps API token

For local stdio you can keep one fallback token in `.env`. In a fresh local
bootstrap, the demo `manager@retailops.local` account has the Manager role and
can be used for development. For production or shared environments, create a
dedicated service user in RetailOps and use that user's token instead. For
remote SSE/streamable-HTTP, each MCP client should bring its own RetailOps API
token and send it as `Authorization: Bearer <token>` to the MCP endpoint.

```bash
# Option A: Django shell
python manage.py shell
>>> from rest_framework.authtoken.models import Token
>>> from core.models import User
>>> user = User.objects.get(email="manager@retailops.local")
>>> token, _ = Token.objects.get_or_create(user=user)
>>> print(token.key)

# Option B: HTTP
curl -X POST http://127.0.0.1:8000/api/v1/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"email": "manager@retailops.local", "password": "ManagerPass123!"}'
```

---

## 16. Security Model

### Effective identity

MCP does not implement its own business authorization. It only decides which
RetailOps API token to forward to Django REST Framework; the API remains the
authority for role checks, order workflow rules, inventory protections, and
admin-only operations.

The effective token is resolved in this order:
- HTTP MCP request `Authorization: Bearer <RetailOps API token>`
- Token activated inside a local stdio process by `retailops_login(..., activate=True)`
- `RETAILOPS_API_TOKEN` as a local fallback for stdio and loopback development

For local development, the fallback token can use the demo
`manager@retailops.local` account after running `bootstrap_local --seed`.
Remote clients should not share that process token; they should send their own
RetailOps token as the MCP Bearer credential.

### Token storage

Tokens may be stored in:
- `.env` for local development (excluded from git)
- The `env` block of `claude_desktop_config.json` for Claude Desktop (local to the machine)
- Environment variables for deployed instances
- The remote MCP client's own secret store, sent as `Authorization: Bearer <token>`

Tokens are **never** stored in source code or committed to the repository.

### Network exposure

By default, the SSE and streamable-HTTP transports bind to `127.0.0.1`
(loopback only). They are not accessible from other machines unless
`MCP_HOST=0.0.0.0` is set explicitly.

When binding to a non-loopback host, startup is intentionally blocked unless:
- `MCP_AUTH_MODE=retailops-token`
- `MCP_PUBLIC_BASE_URL` is an `https://` URL
- `MCP_ALLOWED_HOSTS` is configured for DNS rebinding protection

Production deployments should still place MCP behind a reverse proxy such as
Caddy or nginx with TLS. The MCP server validates Bearer tokens, while the
reverse proxy terminates HTTPS and can add IP allow-lists, access logs, and any
organization-level authentication required around the endpoint.

Minimal Caddy example for streamable-HTTP:

```caddyfile
mcp.example.com {
    reverse_proxy 127.0.0.1:8001
}
```

Minimal nginx example for SSE or streamable-HTTP:

```nginx
server {
    listen 443 ssl;
    server_name mcp.example.com;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_read_timeout 3600;
    }
}
```

### No prompt injection in tool outputs

Tool outputs come from the RetailOps API — data entered by application users (customer names, order notes, etc.). This data is passed directly to the AI as tool results. If an attacker could control data in the database, they could attempt to inject instructions into tool outputs. Mitigation: treat MCP tool results as data, not as instructions, and configure the AI system prompt accordingly.

---

## 17. Error Reference

All tool errors follow the same format: a `ValueError` with a human-readable message derived from the API error envelope.

| Scenario | Error message pattern |
|---|---|
| Invalid token or token revoked | `Authentication failed. Check the active MCP token: HTTP clients must send Authorization: Bearer <RetailOps API token>, while local stdio sessions use retailops_login or RETAILOPS_API_TOKEN.` |
| Insufficient role | `Permission denied. The current RetailOps user does not have the required role for this action.` |
| Resource not found | `Not found: the requested resource does not exist. Check the ID you provided. (<detail>)` |
| Dependent record blocks deletion | `Conflict: <reason>. This record cannot be deleted because other records depend on it.` |
| Validation error (one field) | `Validation failed - <field>: <message>` |
| Validation error (multiple fields) | `Validation failed - <field1>: <msg1>; <field2>: <msg2>` |
| Server error | `RetailOps server error (HTTP 500). Check the Django development server logs for a traceback.` |
| MCP-layer guard (zero quantity) | `quantity must be non-zero. Use a positive integer to add stock or negative to remove it.` |
| MCP-layer guard (empty items) | `items must contain at least one line item.` |
| MCP-layer guard (empty order_ids) | `order_ids must be a non-empty list of integers.` |
| MCP-layer guard (empty adjustments) | `adjustments must be a non-empty list.` |
| MCP-layer guard (no settings field) | `At least one settings field must be provided.` |

---

## 18. Troubleshooting

### The MCP server starts but tools return authentication errors

1. For stdio/local mode, run `retailops_whoami` to inspect the token source, or run `retailops_login(email, password)` to activate a fresh token for the current MCP process.
2. For HTTP transports with `MCP_AUTH_MODE=retailops-token`, confirm the MCP client sends `Authorization: Bearer <RetailOps API token>` on the MCP connection.
3. Verify the token is valid against the REST API:
   ```bash
   curl -H "Authorization: Token <your-token>" http://127.0.0.1:8000/api/v1/dashboard/
   ```
4. If the token was revoked via `retailops_logout`, generate a new one (see section 15).

### Claude Desktop shows no tools after configuring `claude_desktop_config.json`

1. Fully quit Claude Desktop.
2. Relaunch it so the config is reloaded.
3. Open Help -> Show Logs to see if the MCP server process started or threw an error.
4. Run the server manually to confirm it starts cleanly:
   ```bash
   cd /home/<user>/retailops
   python -m mcp_server.server
   ```

### The server starts but all API calls fail with connection errors

The Django dev server must be running. Start it in a separate terminal:
```bash
python manage.py runserver
```

### SSE clients disconnect immediately

Ensure no firewall or proxy is terminating long-lived connections. The SSE transport requires the HTTP connection to stay open for streaming. If using nginx, add:
```nginx
proxy_read_timeout 3600;
proxy_buffering off;
```

### `ModuleNotFoundError: No module named 'mcp_server'`

The server must be run from the project root (the directory containing `mcp_server/`), not from inside `mcp_server/`:
```bash
# Correct:
cd /home/<user>/retailops
python -m mcp_server.server

# Wrong:
cd /home/<user>/retailops/mcp_server
python server.py
```

When using Claude Desktop, set `"cwd"` to the RetailOps project root, such as
`"/path/to/retailops"`.

### `UnicodeEncodeError` on Windows terminals

If running `test_mcp_tools.py` in a Windows terminal that defaults to `cp1252`, the test file handles this automatically by calling `sys.stdout.reconfigure(encoding="utf-8")` at startup. If you see this error in other scripts, add the same line at the top.

### A tool returns a 409 Conflict on delete

The record has dependent records. Examples:
- Deleting a **customer** who has orders → delete or cancel the orders first
- Deleting a **product** that has inventory movements → deactivate it with `retailops_update_product(is_active=False)` instead
- Deleting a **category** that has products assigned to it → reassign products first

This is correct business behavior enforced by `on_delete=PROTECT` in the database model.

---

## 19. MCP Skill Card

The RetailOps API exposes a self-contained **skill card** at a public HTTP endpoint. Any AI agent or chat client can fetch it to immediately understand the full MCP server without reading this guide.

### Endpoint

```
GET /api/v1/mcp-skill/
```

**No authentication required.** Returns a complete JSON document describing all 54 tools, 14 resources, 5 workflow prompts, connection instructions, the order lifecycle state machine, constraints, and error codes.

### Formats

**JSON (default):**
```bash
curl http://127.0.0.1:8000/api/v1/mcp-skill/
```

**Markdown** (plain text, paste-into-system-prompt ready):
```bash
curl "http://127.0.0.1:8000/api/v1/mcp-skill/?format=markdown"
# or:
curl -H "Accept: text/markdown" http://127.0.0.1:8000/api/v1/mcp-skill/
```

### Use cases

| Scenario | How to use |
|---|---|
| Bootstrapping a new agent | Fetch the card at session start; inject the Markdown into the system prompt |
| Auto-discovery in multi-server setups | Parse the JSON `tools` object to enumerate available tools and role requirements |
| Verifying deployed capabilities | Compare the card's `version` and tool list against the expected spec |
| Building a UI on top of the MCP server | Use the JSON `tools` structure as a machine-readable capability manifest |

### Keeping the card current

The skill card is generated at request time by `api/views/mcp_skill.py`. It reflects the current state of the codebase directly — there is no separate file to keep in sync. If tools are added or removed, the view must be updated to match.
