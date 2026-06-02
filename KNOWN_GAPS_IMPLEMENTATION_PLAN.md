Implementation is done. Now the full plan:

---

## Rate Limiting — What Was Just Implemented

**New file: `api/throttling.py`** — 4 scoped throttle classes:

| Class | Base | Scope | Rate | Applied to |
|---|---|---|---|---|
| `LoginRateThrottle` | `AnonRateThrottle` | `login` | 20/min per IP | `ObtainTokenView` |
| `PasswordChangeRateThrottle` | `UserRateThrottle` | `password_change` | 10/min per user | `UserViewSet.change_password` |
| `OrderTransitionRateThrottle` | `UserRateThrottle` | `order_transition` | 60/min per user | submit/confirm/ship/deliver/cancel/refund |
| `InventoryAdjustRateThrottle` | `UserRateThrottle` | `inventory_adjust` | 30/min per user | `InventoryMovementViewSet.adjust` |

**Key design decision:** `AnonRateThrottle` removed from `DEFAULT_THROTTLE_CLASSES`. It was applied globally but `POST /auth/token/` is the only anonymous endpoint in this API — `LoginRateThrottle` handles it directly, so the global anon slot was dead weight everywhere else.

Each ViewSet uses `get_throttles()` (the same pattern as `get_permissions()`), so action-specific throttles replace the global default rather than stacking on top of it.

---

## Full Implementation Plan for Remaining Known Gaps

---

### Feature 1 — Authentication: Forgot-Password / Self-Service Reset

**Scope:** HTML app + API layer

**The challenge:** Django's built-in password reset emails require an SMTP backend. Since this is an internal tool with no email configured, the correct approach is to wire up Django's password reset machinery with a console email backend for development and configurable SMTP for production.

#### Phase 1A — Settings & Email Backend

**`retailops/settings.py`:**
```python
EMAIL_BACKEND = os.environ.get(
    'DJANGO_EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',  # prints to console in dev
)
EMAIL_HOST     = os.environ.get('DJANGO_EMAIL_HOST', 'localhost')
EMAIL_PORT     = int(os.environ.get('DJANGO_EMAIL_PORT', 587))
EMAIL_USE_TLS  = os.environ.get('DJANGO_EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER     = os.environ.get('DJANGO_EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('DJANGO_EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL  = os.environ.get('DJANGO_DEFAULT_FROM_EMAIL', 'noreply@retailops.local')
```

#### Phase 1B — HTML App (5 new templates + URL additions)

Django ships complete reset logic in `django.contrib.auth` views — only templates and URLs are needed.

**`core/urls.py`** — add after the login/logout routes:
```python
from django.contrib.auth import views as auth_views

path('password-reset/',
     auth_views.PasswordResetView.as_view(template_name='core/password_reset_form.html',
                                           email_template_name='core/password_reset_email.txt',
                                           subject_template_name='core/password_reset_subject.txt'),
     name='password_reset'),
path('password-reset/done/',
     auth_views.PasswordResetDoneView.as_view(template_name='core/password_reset_done.html'),
     name='password_reset_done'),
path('password-reset/confirm/<uidb64>/<token>/',
     auth_views.PasswordResetConfirmView.as_view(template_name='core/password_reset_confirm.html'),
     name='password_reset_confirm'),
path('password-reset/complete/',
     auth_views.PasswordResetCompleteView.as_view(template_name='core/password_reset_complete.html'),
     name='password_reset_complete'),
```

**New templates** (all extend `base.html`, styled with existing design tokens):
- `core/password_reset_form.html` — email entry form with "Send Reset Link" button
- `core/password_reset_done.html` — "Check your email" confirmation page
- `core/password_reset_confirm.html` — new password + confirm password form
- `core/password_reset_complete.html` — "Password reset successful" + link to login
- `core/password_reset_email.txt` — plain-text email body (not HTML; plain text is safer for deliverability)
- `core/password_reset_subject.txt` — single-line subject

**`core/templates/core/login.html`** — add "Forgot your password?" link below the form.

#### Phase 1C — API Layer (new endpoints in `api/views/auth.py`)

```
POST /api/v1/auth/password-reset/
    Body: {"email": "user@example.com"}
    Response 200: {"detail": "Password reset email sent if the address is registered."}
    Permission: AllowAny
    Note: Always returns 200 regardless of whether the email exists (prevents
    user-enumeration). Internally uses Django's PasswordResetForm to generate
    the HMAC-signed uid+token and send the email.

POST /api/v1/auth/password-reset/confirm/
    Body: {"uid": "MQ", "token": "abc-123def", "new_password": "NewPass123!"}
    Response 200: {"detail": "Password has been reset successfully."}
    Permission: AllowAny
    Validates uid (base64-decoded PK) and token via Django's default_token_generator.
    Sets the new password and invalidates the token.
```

**`api/serializers/auth.py`** (new file):
- `PasswordResetRequestSerializer` — validates `email` field (EmailField)
- `PasswordResetConfirmSerializer` — validates `uid`, `token`, `new_password`; the `validate()` method decodes the uid, loads the user, and calls `default_token_generator.check_token(user, token)` — raises `ValidationError` with `code='invalid_token'` if invalid or expired

**Throttle:** Both endpoints get a `PasswordResetRateThrottle` (new class, AnonRateThrottle subclass, scope `'password_reset'`, rate `'5/min'`) added to `api/throttling.py` and `DEFAULT_THROTTLE_RATES`.

**`api/urls.py`** — register both views.

---

### Feature 2 — Authorization: HTML App Role Restriction on Order Views

**Scope:** `core/views.py` only — 2-line fix

**Problem:** `order_detail` (handles both GET display and POST edits) and `order_submit` have no role restriction. Any logged-in user — including one with no role — can edit and submit orders.

**Fix:** Add `@role_required('Staff', 'Manager', 'Admin')` to both view functions.

```python
# Before
@login_required
def order_detail(request, pk):
    ...

@require_POST
@login_required
def order_submit(request, pk):
    ...

# After
@login_required
@role_required('Staff', 'Manager', 'Admin')
def order_detail(request, pk):
    ...

@require_POST
@login_required
@role_required('Staff', 'Manager', 'Admin')
def order_submit(request, pk):
    ...
```

**Note on decorator order:** `@login_required` must come before `@role_required` in execution order (outermost first) because `role_required` accesses `request.user.role`, which assumes the user is already authenticated. The existing pattern in `core/views.py` uses `@login_required` on the outer position — follow the same convention.

**CLAUDE.md update:** Remove this entry from the Known Gaps table.

---

### Feature 3 — Validation: Line-Item Guard on Order Submit

**Scope:** API `submit` action + HTML app submit view

#### Phase 3A — API: `submit` action in `api/views/order.py`

The `confirm` action already has this guard. Mirror it in `submit`:

```python
@action(detail=True, methods=['post'])
def submit(self, request, pk=None):
    order = self.get_object()
    err = self._require_status(order, SalesOrder.DRAFT)
    if err:
        return err

    # ADD: guard that mirrors the confirm action
    if not order.items.exists():
        return Response(
            {'error': 'Cannot submit an order with no line items.', 'code': 'no_items'},
            status=status.HTTP_409_CONFLICT,
        )

    order.status = SalesOrder.PENDING
    order.save(update_fields=['status', 'updated_at'])
    return Response(SalesOrderReadSerializer(order).data)
```

#### Phase 3B — HTML App: `order_submit` view in `core/views.py`

Add the same guard before the status transition:

```python
def order_submit(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    if order.status != SalesOrder.DRAFT:
        messages.error(request, 'Only Draft orders can be submitted.')
        return redirect('order_detail', pk=pk)

    # ADD:
    if not order.items.exists():
        messages.error(request, 'Cannot submit an order with no line items.')
        return redirect('order_detail', pk=pk)

    order.status = SalesOrder.PENDING
    order.save(update_fields=['status', 'updated_at'])
    ...
```

**CLAUDE.md update:** Update the Validation row to reflect both `submit` and `confirm` now validate this.

---

### Feature 4 — Inventory: HTML UI for Manual Stock Adjustments

**Scope:** HTML app only (the API already has `POST /api/v1/inventory/adjust/`)

**Design:** A slide-in panel/modal on `inventory_list.html` (consistent with the existing inventory movements slide-in panel), gated at Manager+.

#### Phase 4A — New view in `core/views.py`

```python
@require_POST
@login_required
@role_required('Manager', 'Admin')
def inventory_adjust(request):
    """
    POST /inventory/adjust/
    Records a manual InventoryMovement for a product.
    Body fields: product_id, quantity (signed int), notes
    """
    product_id = request.POST.get('product_id')
    quantity_raw = request.POST.get('quantity', '').strip()
    notes = request.POST.get('notes', '').strip()

    product = get_object_or_404(Product, pk=product_id, is_active=True)

    try:
        quantity = int(quantity_raw)
    except (ValueError, TypeError):
        messages.error(request, 'Quantity must be a non-zero integer.')
        return redirect('inventory_list')

    if quantity == 0:
        messages.error(request, 'Quantity must be non-zero.')
        return redirect('inventory_list')

    InventoryMovement.objects.create(
        product=product,
        movement_type=InventoryMovement.ADJUSTMENT,
        quantity=quantity,
        reference_type='ManualAdjustment',
        reference_id=0,
        notes=notes or f'Manual adjustment by {request.user.get_full_name()}',
        created_by=request.user,
    )

    direction = 'added' if quantity > 0 else 'removed'
    messages.success(
        request,
        f'Stock adjustment recorded: {abs(quantity)} units {direction} for {product.name}.',
    )
    return redirect('inventory_list')
```

#### Phase 4B — URL

**`core/urls.py`:**
```python
path('inventory/adjust/', views.inventory_adjust, name='inventory_adjust'),
```

#### Phase 4C — Template update (`inventory_list.html`)

Add an "Adjust Stock" button (Manager+, conditionally rendered based on `request.user.role`) that opens a modal containing:
- Product dropdown (active products only)
- Quantity field (signed integer, with helper text: positive = add stock, negative = remove)
- Notes textarea (optional)
- Submit button

The modal follows the existing JS modal pattern already in `base.html`. Only show the button when `request.user.role.name in ('Manager', 'Admin')` — use a template `{% if %}` block, not a separate permission check (the view handles that).

---

### Bulk Operations Plan

These are the operations with genuine real-world utility for a retail/e-commerce internal tool. The selection criteria: operations that a manager or fulfillment team would realistically need to do on multiple records at once (end-of-day processing, stocktake, batch fulfillment).

#### API Bulk Endpoints

**1. `POST /api/v1/orders/bulk-transition/`** — Manager+

Replaces individual submit/confirm/cancel calls when processing a batch. The request specifies the action and an array of order IDs:

```json
{
    "action": "confirm",
    "order_ids": [12, 15, 18, 22]
}
```

Response:
```json
{
    "succeeded": [12, 15, 22],
    "failed": [
        {"id": 18, "error": "Order is currently 'draft', expected 'pending'.", "code": "wrong_status"}
    ]
}
```

Supported actions: `confirm`, `ship`, `deliver` (the batch-friendly ones; `cancel` and `refund` intentionally excluded — those are consequential reversals that should be deliberate, single-record operations). Each order transition runs in its own `transaction.atomic()` block so one failure doesn't roll back the others.

**Implementation:** New `@action(detail=False, methods=['post'], url_path='bulk-transition')` on `OrderViewSet`. New `BulkTransitionSerializer` validates `action` (choices) and `order_ids` (list of PKs, max 100). Permission: `IsManagerOrAdmin`.

**Throttle:** New `BulkOperationRateThrottle` (UserRateThrottle, scope `'bulk_operation'`, 10/min) — bulk operations are expensive; tighter ceiling than regular endpoints.

---

**2. `POST /api/v1/inventory/bulk-adjust/`** — Manager+

Stocktake reconciliation — adjust multiple products in a single call:

```json
{
    "adjustments": [
        {"product_id": 3, "quantity": -5, "notes": "Damaged"},
        {"product_id": 7, "quantity": 20, "notes": "New stock received"},
        {"product_id": 12, "quantity": -2, "notes": "Shrinkage"}
    ]
}
```

Response:
```json
{
    "succeeded": [3, 7, 12],
    "created_movements": [45, 46, 47],
    "failed": []
}
```

All adjustments run in a single `transaction.atomic()` — the whole batch succeeds or fails together (stocktake reconciliations should be atomic). A `max_items` guard of 200 items prevents abuse.

**Implementation:** New `@action(detail=False, methods=['post'], url_path='bulk-adjust')` on `InventoryMovementViewSet`. New `BulkAdjustmentSerializer` with a nested list of `ManualAdjustmentSerializer`. Uses `InventoryMovement.objects.bulk_create()` after validation. Same `BulkOperationRateThrottle` as above.

---

#### MCP Tools for Bulk Operations

**3 new MCP tools in `mcp_server/tools/orders.py`:**

```python
@mcp.tool()
async def retailops_bulk_confirm_orders(order_ids: list[int]) -> dict:
    """Confirm multiple pending orders in one call. Each order is confirmed
    independently — failures are reported per-order without blocking others."""
    ...

@mcp.tool()
async def retailops_bulk_ship_orders(order_ids: list[int]) -> dict:
    """Mark multiple paid orders as shipped."""
    ...

@mcp.tool()
async def retailops_bulk_deliver_orders(order_ids: list[int]) -> dict:
    """Mark multiple shipped orders as delivered."""
    ...
```

All three call `POST /api/v1/orders/bulk-transition/` with the corresponding action.

**1 new MCP tool in `mcp_server/tools/inventory.py`:**

```python
@mcp.tool()
async def retailops_bulk_adjust_inventory(
    adjustments: list[dict]  # [{product_id, quantity, notes?}]
) -> dict:
    """Record multiple stock adjustments in a single atomic operation.
    Useful for stocktake reconciliation."""
    ...
```

Calls `POST /api/v1/inventory/bulk-adjust/`.

These 4 new tools bring the total to **47 MCP tools**. Update `server.py` registrations, `MCP_GUIDE.md` tool catalog, and the smoke test (`test_mcp_tools.py`) with new test groups for both bulk endpoints.

---

### Summary Table

| # | Area | Scope | New files | Files modified |
|---|---|---|---|---|
| 1 | Auth: password reset | HTML + API | `api/serializers/auth.py`, 5 templates | `settings.py`, `core/urls.py`, `api/urls.py`, `api/views/auth.py`, `api/throttling.py`, `login.html` |
| 2 | Authorization: role gates | HTML only | — | `core/views.py` (2 decorators) |
| 3 | Validation: empty order guard | API + HTML | — | `api/views/order.py`, `core/views.py` |
| 4 | Inventory: adjust UI | HTML only | — | `core/views.py`, `core/urls.py`, `inventory_list.html` |
| 5 | Rate limiting *(done)* | API | `api/throttling.py` | `settings.py`, 4 view files |
| 6 | Bulk transitions | API + MCP | — | `api/views/order.py`, `api/serializers/order.py`, `mcp_server/tools/orders.py`, `test_mcp_tools.py` |
| 7 | Bulk inventory adjust | API + MCP | — | `api/views/inventory.py`, `api/serializers/inventory.py`, `mcp_server/tools/inventory.py`, `test_mcp_tools.py` |