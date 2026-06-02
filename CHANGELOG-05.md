# CHANGELOG-05 — Authorization, Validation, Inventory UI, and Bulk Operations

**Session date:** 2026-04-11
**Scope:** Four features closing all remaining non-security Known Gaps: HTML app role enforcement on order views, server-side order validation, an inventory stock adjustment UI, and bulk API operations with matching MCP tools.

---

## Overview

This session continued directly from Session 5. The primary goal was to close out all remaining items from the Known Gaps table in CLAUDE.md (excluding the Security row, which requires infrastructure work). Four discrete features were implemented and verified:

1. **Feature 2 — Authorization**: Restricted `order_create`, `order_detail`, and `order_delete` views to Staff/Manager/Admin roles. Previously all three required only `@login_required`; a roleless authenticated user could access them freely.

2. **Feature 3 — Validation**: Added a server-side guard to both the HTML `order_submit` view and the API `submit` action that rejects orders with zero line items. The API `confirm` action already had this guard; `submit` did not.

3. **Feature 4 — Inventory UI**: Added a full "Adjust Stock" modal to `inventory_list.html` (Manager/Admin only) with a supporting `POST /inventory/adjust/` view and URL. The API has always supported `POST /api/v1/inventory/adjust/`; the HTML app had no equivalent.

4. **Bulk Operations**: Added two new API endpoints (`POST /api/v1/orders/bulk-transition/` and `POST /api/v1/inventory/bulk-adjust/`) and four new MCP tools for batch-processing orders and inventory adjustments. Both endpoints use a partial-success response pattern — each item is processed independently, and failures are collected into a `failed` list without aborting the rest of the batch.

Additionally, a test user without any role (`norole@retailops.local`) was created in the database specifically for browser verification of the 403 response. This user can be left in place or removed; it has no role and cannot perform any role-gated actions.

---

## Feature 2 — Authorization: HTML App Role Restriction

### Problem

`order_create`, `order_detail`, and `order_delete` used `@login_required` as their only access control. Any authenticated user — including users with no role assigned — could create, view, and delete orders. The existing `order_submit` already had `@role_required('Staff', 'Manager', 'Admin')`, creating an inconsistency where an unauthenticated user could create a Draft order but not submit it.

### Solution

Replaced `@login_required` with `@role_required('Staff', 'Manager', 'Admin')` on all three views. The `role_required` decorator already wraps `@login_required` internally (see `core/decorators.py`), so no double-wrapping occurs. The `@require_POST` decorator on `order_delete` was preserved as the outermost decorator to ensure 405 Method Not Allowed is returned for GET requests before any auth check runs.

### Changes — `core/views.py`

```python
# Before
@login_required
def order_create(request): ...

@login_required
def order_detail(request, pk): ...

@require_POST
@login_required
def order_delete(request, pk): ...

# After
@role_required('Staff', 'Manager', 'Admin')
def order_create(request): ...

@role_required('Staff', 'Manager', 'Admin')
def order_detail(request, pk): ...

@require_POST
@role_required('Staff', 'Manager', 'Admin')
def order_delete(request, pk): ...
```

Docstrings were updated to document the `Allowed roles: Staff, Manager, Admin.` constraint.

### Verification

Browser tests run against the live server:

| User | Role | `GET /orders/new/` | `GET /orders/9/` |
|---|---|---|---|
| `norole@retailops.local` | None | **403 Forbidden** ✓ | **403 Forbidden** ✓ |
| `staff@retailops.local` | Staff | **200 New Order** ✓ | **200 Order Detail** ✓ |
| `admin@retailops.local` | Admin | **200 New Order** ✓ | — |

---

## Feature 3 — Validation: Minimum Line Items on Submit

### Problem

`order_submit` (HTML) and the API `submit` action allowed promoting a zero-item Draft to Pending status. The `confirm` action already had a `not order.items.exists()` guard at line 194 of `api/views/order.py`, but `submit` had no equivalent. This meant an empty order could enter the Pending queue and then fail on confirmation instead of at submission.

### Changes — `core/views.py` (`order_submit`)

```python
order = get_object_or_404(SalesOrder, pk=pk, status=SalesOrder.DRAFT)

if not order.items.exists():
    messages.error(request, 'Cannot submit an order with no line items.')
    return redirect('order-detail', pk=pk)

order.status = SalesOrder.PENDING
order.save()
```

### Changes — `api/views/order.py` (`OrderViewSet.submit`)

```python
if not order.items.exists():
    return Response(
        {'error': 'Cannot submit an order with no line items.', 'code': 'no_items'},
        status=status.HTTP_409_CONFLICT,
    )
```

The error envelope (`code: 'no_items'`, HTTP 409) matches the existing pattern used by the `confirm` action.

### Verification

HTML path: POST to `/orders/24/submit/` (empty draft) → order remains Draft, error toast rendered on page.

API path:
```
POST /api/v1/orders/24/submit/
→ HTTP 409
{"error": "Cannot submit an order with no line items.", "code": "no_items"}
```

---

## Feature 4 — Inventory: HTML UI for Manual Stock Adjustments

### Problem

The HTML app had no way to record stock purchases, corrections, or returns. The only inventory movements created through the UI were the automatic deductions/restorations triggered by order confirmation/cancellation/refund. The API already provided `POST /api/v1/inventory/adjust/`, but operators without API access had no equivalent.

### New view — `core/views.py`: `inventory_adjust`

```python
@require_POST
@role_required('Manager', 'Admin')
def inventory_adjust(request):
    ...
```

Accepts `product_id`, `movement_type` (`purchase` | `adjustment` | `return`), `quantity` (non-zero int), and optional `notes`. Creates an `InventoryMovement` with `reference_type='ManualAdjustment'` and `reference_id=0`. Redirects back to `inventory-list` with a success or error message.

Validation:
- `product_id` required and must resolve to an existing `Product`
- `movement_type` must be one of the three allowed values
- `quantity` must be a non-zero integer

### New URL — `core/urls.py`

```python
# POST /inventory/adjust/  — record a manual stock adjustment (Manager/Admin)
path('inventory/adjust/', views.inventory_adjust, name='inventory-adjust'),
```

### Template changes — `core/templates/core/inventory_list.html`

Three additions:

**1. Header button** (Manager/Admin only):
```html
<button type="button" class="btn btn-secondary"
  onclick="openAdjustModal(null, null)">Adjust Stock</button>
```
Opens the modal with a product dropdown, allowing adjustment of any product without navigating to a specific row.

**2. Per-row "Adjust" button** (Manager/Admin only, in the Actions column):
```html
<button type="button" class="btn btn-secondary btn-sm"
  onclick="openAdjustModal({{ product.pk }}, '{{ product.sku|escapejs }} — {{ product.name|escapejs }}')">
  Adjust
</button>
```
Opens the modal with the product pre-filled (no dropdown shown).

**3. "Adjust Stock" modal** (in `{% block modals %}`):
- Shared modal instance reused for both entry points
- `<input type="hidden" name="product_id">` drives form submission; the product `<select>` has no `name` attribute — only the hidden input is submitted, updated via a `change` listener on the select
- Fields: Product (label or dropdown depending on entry point), Movement Type, Quantity (positive = add, negative = deduct), Notes (optional)
- Submits via standard POST form to `{% url 'inventory-adjust' %}`

**JavaScript** (`{% block extra_js %}`):
```javascript
function openAdjustModal(productId, productLabel) {
    // productId = null  → show dropdown (header button)
    // productId = <int> → show label, pre-fill hidden input (row button)
    ...
}
```

### Verification

Manager session, browser test:

1. Clicked "Adjust" on the "A4 Ruled Notebook" row → modal opened with product label pre-filled, dropdown hidden
2. Filled quantity = 25, notes = "Test restock from supplier", movement type = Purchase
3. Submitted → redirected to inventory list with success toast: `"Purchase of +25 recorded for NB-A4-RULED — A4 Ruled Notebook."`
4. Database confirmation:

```
Movement: NB-A4-RULED +25 (Purchase)
  type: purchase | qty: 25 | notes: Test restock from supplier
  created_by: manager@retailops.local
  product current_stock now: 140
```

5. Staff user: no "Adjust" buttons or "Adjust Stock" header button visible (template gate: `user.role.name == 'Manager' or user.role.name == 'Admin'`)

---

## Feature 5 — Bulk Operations API and MCP Tools

### Design

Both bulk endpoints use a **partial-success pattern**: each item in the batch is processed independently inside its own `try/except` block. The response is always HTTP 200 with a `{succeeded: [...], failed: [...]}` shape. This allows callers to make progress on valid items even when some items in the batch are invalid or in the wrong state.

### `POST /api/v1/orders/bulk-transition/`

Added as `@action(detail=False, methods=['post'], url_path='bulk-transition', permission_classes=[IsAuthenticated, IsManagerOrAdmin])` on `OrderViewSet`.

**Request body:**
```json
{
    "order_ids": [1, 2, 3],
    "action": "confirm" | "ship" | "deliver"
}
```

**Supported actions:**

| action | Required source status | Side-effect |
|---|---|---|
| `confirm` | `pending` | Creates negative `InventoryMovement` per line item (deducts stock); stamps `confirmed_by` / `confirmed_at` |
| `ship` | `paid` | None |
| `deliver` | `shipped` | None |

**Response (HTTP 200):**
```json
{
    "succeeded": [ <SalesOrderReadSerializer>, ... ],
    "failed": [ {"id": <int>, "error": "<reason>"}, ... ]
}
```

Failure reasons include: order not found, wrong status, no line items (for `confirm`), unexpected exception.

The `confirm` path replicates the full logic from the single-order `confirm` action including `bulk_create` of `InventoryMovement` rows inside `transaction.atomic()`. This ensures each confirmation is all-or-nothing even within a bulk call.

The action is covered by `OrderTransitionRateThrottle` (added `bulk_transition` to the `get_throttles` check). `get_serializer_class` was updated to return `SalesOrderReadSerializer` for `bulk_transition`.

**Validation:**
- `order_ids` must be a non-empty list → HTTP 400 `code: invalid_request`
- `action` must be `confirm`, `ship`, or `deliver` → HTTP 400 `code: invalid_action`
- Individual order errors → entry in `failed` list (no HTTP error)

### `POST /api/v1/inventory/bulk-adjust/`

Added as `@action(detail=False, methods=['post'], permission_classes=[IsAuthenticated, IsManagerOrAdmin], url_path='bulk-adjust')` on `InventoryMovementViewSet`.

**Request body:**
```json
{
    "adjustments": [
        {"product_id": 1, "quantity": 50, "notes": "Weekly restock"},
        {"product_id": 2, "quantity": -5, "notes": "Damaged in transit"}
    ]
}
```

Each entry's rules mirror `POST /inventory/adjust/`:
- `product_id` must be an integer resolving to an existing product
- `quantity` must be a non-zero integer
- `notes` is optional
- `movement_type` is fixed to `adjustment`; `reference_type` fixed to `ManualAdjustment`; `reference_id` fixed to 0

Covered by `InventoryAdjustRateThrottle` (`bulk_adjust` added to `get_throttles` check).

**Verification:**
```bash
POST /api/v1/inventory/bulk-adjust/
{
  "adjustments": [
    {"product_id": 16, "quantity": 10, "notes": "Restock test"},  # valid
    {"product_id": 9999, "quantity": 5},                          # not found
    {"product_id": 17, "quantity": 0}                             # zero qty
  ]
}
→ succeeded: 1 | failed: [
    {"product_id": 9999, "error": "Product not found."},
    {"product_id": 17,   "error": "\"quantity\" must be a non-zero integer."}
  ]
```

### MCP Tools (4 new, `mcp_server/tools/`)

#### `mcp_server/tools/orders.py` — 3 new tools

All three call `POST /api/v1/orders/bulk-transition/` with the appropriate `action` value.

**`retailops_bulk_confirm_orders(order_ids: list) -> dict`**
- Confirms multiple Pending orders in one request
- Pre-validates that `order_ids` is non-empty
- Describes side-effects (inventory deduction) in docstring

**`retailops_bulk_ship_orders(order_ids: list) -> dict`**
- Marks multiple Paid orders as Shipped

**`retailops_bulk_deliver_orders(order_ids: list) -> dict`**
- Marks multiple Shipped orders as Delivered

All three follow the same partial-success contract: the response always has `succeeded` and `failed` keys.

#### `mcp_server/tools/inventory.py` — 1 new tool

**`retailops_bulk_adjust_inventory(adjustments: list) -> dict`**
- Calls `POST /api/v1/inventory/bulk-adjust/`
- `adjustments` is a list of dicts: `{product_id, quantity, notes?}`
- Pre-validates non-empty list
- Documents both the per-entry rules and the partial-success response shape

---

## Files Modified

| File | Change |
|---|---|
| `core/views.py` | `order_create`, `order_detail`, `order_delete`: `@login_required` → `@role_required('Staff', 'Manager', 'Admin')`; `order_submit`: added zero-items guard; new `inventory_adjust` view (POST, Manager+) |
| `core/urls.py` | Added `path('inventory/adjust/', views.inventory_adjust, name='inventory-adjust')` |
| `core/templates/core/inventory_list.html` | Added header "Adjust Stock" button, per-row "Adjust" buttons, `{% block modals %}` with the adjust modal, and `openAdjustModal()` JS function |
| `api/views/order.py` | `submit` action: added zero-items guard; `bulk_transition` action added; `get_serializer_class` and `get_throttles` updated to cover `bulk_transition` |
| `api/views/inventory.py` | `bulk_adjust` action added; `get_throttles` updated to cover `bulk_adjust` |
| `mcp_server/tools/orders.py` | Added `retailops_bulk_confirm_orders`, `retailops_bulk_ship_orders`, `retailops_bulk_deliver_orders` |
| `mcp_server/tools/inventory.py` | Added `retailops_bulk_adjust_inventory` |
| `CLAUDE.md` | Removed Validation and Inventory rows from Known Gaps; updated MCP tool count 43 → 47; documented new API endpoints; added Inventory Adjust architecture note |
| `MCP_GUIDE.md` | Updated tool count 43 → 47 in ASCII diagram and catalog header; updated Inventory section to 4 tools, Orders section to 15 tools; added all 4 new tools to catalog table |

---

## Known Gaps (updated)

The Validation and Inventory rows have been removed. Only the Security row remains:

| Area | Gap |
|---|---|
| Security | `SECRET_KEY`, `DEBUG`, and `ALLOWED_HOSTS` are env-driven. Running `DEBUG=False` without `DJANGO_SECRET_KEY` raises `RuntimeError` at startup. Defaults are still insecure for local dev. |

---

## API Quick Reference — New Endpoints

```
POST /api/v1/orders/bulk-transition/
  Body:    {"order_ids": [int, ...], "action": "confirm"|"ship"|"deliver"}
  Auth:    Manager or Admin
  Returns: {"succeeded": [<order>, ...], "failed": [{"id": int, "error": str}, ...]}

POST /api/v1/inventory/bulk-adjust/
  Body:    {"adjustments": [{"product_id": int, "quantity": int, "notes": str}, ...]}
  Auth:    Manager or Admin
  Returns: {"succeeded": [<movement>, ...], "failed": [{"product_id": int, "error": str}, ...]}

POST /inventory/adjust/    (HTML app)
  Body:    product_id, movement_type (purchase|adjustment|return), quantity, notes
  Auth:    Manager or Admin (session)
  Returns: Redirect to /inventory/ with toast message
```
