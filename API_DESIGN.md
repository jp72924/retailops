# RetailOps REST API — Design Plan

## Table of Contents

1. [Overview](#overview)
2. [Technology Decisions](#technology-decisions)
3. [Repository Layout](#repository-layout)
4. [Settings Changes](#settings-changes)
5. [URL Structure](#url-structure)
6. [Authentication](#authentication)
7. [Permissions](#permissions)
8. [Serializer Design](#serializer-design)
9. [ViewSet Design](#viewset-design)
10. [Line-Item Convention](#line-item-convention)
11. [Error Response Format](#error-response-format)
12. [Pagination and Filtering](#pagination-and-filtering)
13. [Dashboard Endpoint](#dashboard-endpoint)
14. [Implementation Phases](#implementation-phases)
15. [Critical Gotchas](#critical-gotchas)

---

## Overview

This plan adds a versioned REST API layer to RetailOps without modifying any existing
code in `core/`. The API lives in a new Django app (`api/`) that imports and reuses
`core` models and their business logic. External tools — scripts, integrations,
mobile clients — interact exclusively through this layer.

The HTML application at `/` continues to work unchanged. The API lives at `/api/v1/`.

---

## Technology Decisions

### DRF vs. Alternatives

**Django REST Framework (DRF) 3.15.x** is the correct choice.

- The project is already a standard Django 4.2 application. DRF integrates directly with
  the existing `AUTH_USER_MODEL`, ORM, admin, and middleware without an adaptation layer.
- `ModelSerializer` handles computed-vs-stored field distinction via `SerializerMethodField`
  and `read_only=True` without extra ceremony.
- DRF's `permission_classes` system maps directly onto the existing `@role_required`
  decorator pattern — one factory function, reused everywhere.
- `@action(detail=True, methods=['post'])` on a `ViewSet` is the natural home for the
  six order state-transition endpoints, keeping them co-located with the order resource.
- Alternatives like FastAPI would require re-implementing auth, ORM access, and admin
  integration from scratch. `django-ninja` is viable but less mature. Neither offers
  meaningful advantages given the existing Django investment.

### Authentication Strategy

**Token Authentication for v1, with a clear upgrade path to JWT.**

- Sessions are inappropriate for programmatic, stateless API access.
- DRF's `rest_framework.authtoken` adds a `Token` model linked to `core.User`
  (`AUTH_USER_MODEL`). No migration complexity beyond `python manage.py migrate`.
- `POST /api/v1/auth/token/` accepts `{email, password}` and returns
  `{token, user_id, email, role_name}`. Clients include the token in every request:
  `Authorization: Token <key>`.
- **JWT** (via `djangorestframework-simplejwt`) is the noted v2 upgrade when
  refresh-token semantics are needed. It is not added in v1 — the added complexity
  (refresh endpoint, token blacklisting, clock skew handling) provides no benefit for
  an internal tool.
- Session auth is kept as a secondary backend so the browsable DRF API works during
  development. It is removed in production.

---

## Repository Layout

The API is a self-contained Django app. Nothing in `core/` is modified.

```
api/
  __init__.py
  apps.py
  urls.py                    ← DefaultRouter + auth routes
  exceptions.py              ← custom exception handler (standardised envelope)
  permissions.py             ← role_permission() factory + named shortcuts
  filters.py                 ← FilterSet classes for products, orders, payments
  serializers/
    __init__.py
    auth.py
    role.py
    user.py
    customer.py
    category.py
    product.py
    order.py
    payment.py
    inventory.py
  views/
    __init__.py
    auth.py                  ← ObtainTokenView, RevokeTokenView
    role.py
    user.py
    customer.py
    category.py
    product.py
    order.py
    payment.py
    inventory.py
    dashboard.py
```

**Modified existing files:**

| File | Change |
|------|--------|
| `requirements.txt` | Add `djangorestframework>=3.15`, `django-filter>=24.0` |
| `retailops/settings.py` | Add `rest_framework`, `rest_framework.authtoken`, `api` to `INSTALLED_APPS`; add `REST_FRAMEWORK` config block |
| `retailops/urls.py` | Add `path('api/v1/', include('api.urls'))` |

---

## Settings Changes

### `INSTALLED_APPS` additions

```python
'rest_framework',
'rest_framework.authtoken',
'api',
```

### `REST_FRAMEWORK` config block

Add after `DEFAULT_AUTO_FIELD`:

```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        # Kept for the browsable API — remove SessionAuthentication in production
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',  # remove when DEBUG=False
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'NON_FIELD_ERRORS_KEY': 'errors',
    'EXCEPTION_HANDLER': 'api.exceptions.custom_exception_handler',
}
```

---

## URL Structure

All endpoints are versioned under `/api/v1/`. The browsable API root is at `/api/v1/`.

```
/api/v1/
│
├── auth/
│   ├── token/                    POST    Obtain token {email, password}
│   └── token/revoke/             POST    Delete token (logout)
│
├── roles/                        GET     List (Admin only)
│   └── <id>/                     GET     Detail
│
├── users/                        GET     List (Admin only)
│   ├── invite/                   POST    Create user with role
│   └── <id>/
│       ├──                       GET, PATCH
│       ├── change-password/      POST
│       ├── deactivate/           POST
│       └── reactivate/           POST
│
├── customers/                    GET, POST
│   └── <id>/                     GET, PUT, PATCH, DELETE
│
├── categories/                   GET, POST
│   └── <id>/                     GET, PUT, PATCH, DELETE
│
├── products/                     GET, POST
│   └── <id>/
│       ├──                       GET, PUT, PATCH, DELETE
│       └── movements/            GET     Paginated movement history
│
├── orders/                       GET, POST
│   └── <id>/
│       ├──                       GET, PUT, PATCH, DELETE  (DELETE: Draft only)
│       ├── submit/               POST    Draft → Pending       (Staff+)
│       ├── confirm/              POST    Pending → Confirmed   (Manager+)
│       ├── ship/                 POST    Paid → Shipped        (Staff+)
│       ├── deliver/              POST    Shipped → Delivered   (Staff+)
│       ├── cancel/               POST    Confirmed → Cancelled (Manager+)
│       └── refund/               POST    Paid → Refunded       (Admin only)
│
├── payments/                     GET, POST
│   └── <id>/                     GET
│
├── inventory/                    GET
│   └── adjust/                   POST    Manual stock adjustment (Manager+)
│
└── dashboard/                    GET     Summary stats
```

---

## Authentication

### `api/views/auth.py`

Two views; no ViewSet needed:

**`ObtainTokenView`** — subclasses DRF's `ObtainAuthToken`. Overrides `serializer_class`
with a custom serializer that accepts `email` (not `username`, because the custom `User`
model sets `USERNAME_FIELD = 'email'`). Calls `authenticate(request, username=email, ...)`.
Returns `{token, user_id, email, role_name}`.

**`RevokeTokenView`** — `APIView` that calls `request.user.auth_token.delete()` and
returns `204 No Content`.

### Token usage

```http
POST /api/v1/auth/token/
Content-Type: application/json

{"email": "staff@retailops.local", "password": "StaffPass123!"}
```

```http
GET /api/v1/orders/
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

---

## Permissions

### `api/permissions.py`

Direct equivalent of `core/decorators.py`. The factory function returns a class,
which is what DRF's `permission_classes` list expects.

```python
from rest_framework.permissions import BasePermission

def role_permission(*roles):
    """
    Returns a DRF permission class that allows only users whose role.name
    is in `roles`. Mirrors core.decorators.role_required exactly.
    """
    class RolePermission(BasePermission):
        allowed_roles = roles

        def has_permission(self, request, view):
            return (
                request.user.is_authenticated
                and request.user.role is not None
                and request.user.role.name in self.allowed_roles
            )

    RolePermission.__name__ = f"Is{'Or'.join(roles)}"
    return RolePermission

# Convenience aliases used across all ViewSets
IsAdminRole      = role_permission('Admin')
IsManagerOrAdmin = role_permission('Manager', 'Admin')
IsStaffOrAbove   = role_permission('Staff', 'Manager', 'Admin')
```

**Critical detail**: `User.role` is `null=True`. Every permission check must guard
against `request.user.role is None` before accessing `.name`. The factory above
handles this — do not replicate `.role.name` access elsewhere without the null check.

### Per-resource permission matrix

| Resource | Read | Write | Delete |
|----------|------|-------|--------|
| roles | Admin | — | — |
| users | Admin | Admin | Admin (soft only) |
| customers | Any auth | Any auth | Any auth (409 if has orders) |
| categories | Any auth | Manager+ | Manager+ |
| products | Any auth | Manager+ | Manager+ |
| orders | Any auth | Staff+ (edit), role-gated per transition | Admin (Draft only) |
| payments | Any auth | Any auth (record) | — |
| inventory | Any auth | Manager+ (adjust) | — |
| dashboard | Any auth | — | — |

The known CLAUDE.md gap — "`order_detail` POST has no role restriction" — is **fixed**
in the API. `update`/`partial_update` on `OrderViewSet` require `IsStaffOrAbove`.

---

## Serializer Design

### Design rules

1. **Auto-generated fields** (`order_number`, `payment_number`) have `editable=False`
   on the model. DRF excludes them from writable serializers automatically. Still list
   them explicitly in `read_only_fields` as documentation.

2. **Computed properties** (`current_stock`, `is_low_stock`, `is_out_of_stock`,
   `amount_paid`, `amount_outstanding`) are `SerializerMethodField` — always read-only.
   They call `@property` methods that fire aggregate SQL queries. See
   [Critical Gotchas](#critical-gotchas) §1.

3. **FK write pattern**: Writable FK fields use `PrimaryKeyRelatedField` with a
   `write_only=True` source override. Read responses embed a nested serializer.
   Example for `Product.category`:
   ```python
   category    = ProductCategoryNestedSerializer(read_only=True)
   category_id = PrimaryKeyRelatedField(
       queryset=ProductCategory.objects.all(),
       source='category',
       write_only=True,
   )
   ```

4. **Read/Write split on orders**: `OrderViewSet.get_serializer_class()` returns
   `SalesOrderReadSerializer` for `list`/`retrieve` and `SalesOrderWriteSerializer`
   for `create`/`update`/`partial_update`.

5. **Injected fields** (`created_by`, `recorded_by`, `confirmed_by`) are never
   accepted from the client. Injected in `perform_create()` from `request.user`.

### Per-model serializer notes

#### `user.py` — two serializers

- `UserReadSerializer`: includes nested `RoleSerializer`, excludes `password`.
- `UserWriteSerializer`: `password` is `write_only=True`, `required=False` on update.
  Call `user.set_password()` explicitly in `create()`/`update()` — do not rely on
  the model `save()` to hash it.

#### `product.py` — computed stock fields

```python
class ProductSerializer(ModelSerializer):
    category    = ProductCategoryNestedSerializer(read_only=True)
    category_id = PrimaryKeyRelatedField(
        queryset=ProductCategory.objects.all(), source='category', write_only=True
    )
    current_stock   = SerializerMethodField()
    is_low_stock    = SerializerMethodField()
    is_out_of_stock = SerializerMethodField()

    def get_current_stock(self, obj):
        # Phase 3: use obj._stock annotation instead to avoid N+1
        return obj.current_stock

    class Meta:
        model = Product
        read_only_fields = [
            'id', 'current_stock', 'is_low_stock', 'is_out_of_stock',
            'created_at', 'updated_at',
        ]
```

#### `order.py` — the most complex serializer

**`SalesOrderItemSerializer`** (nested):
- `line_total` is `read_only` — computed in `SalesOrderItem.save()`, never in the
  serializer.
- `unit_price` is optional on write; defaults to `product.unit_price` in
  `SalesOrderWriteSerializer.validate_items()`.

**`SalesOrderReadSerializer`** (GET only):
- All fields `read_only`.
- `items` → nested `SalesOrderItemSerializer(many=True)`.
- `amount_paid`, `amount_outstanding` → `SerializerMethodField`.
- Queryset must `prefetch_related('items__product', 'payments')`.

**`SalesOrderWriteSerializer`** (POST/PUT/PATCH):
- Accepts: `customer_id`, `discount_amount`, `tax_amount`, `notes`, `items` (list).
- `items.create()` and `items.update()` replicate `_save_order_items()` from
  `views.py`, wrapped in `transaction.atomic()`.
- `update()` sub-cases: create (no `id`), update (has `id`), delete (absent from list).

**Validation rules in `validate_items()`:**
1. List must not be empty.
2. Each `product_id` must reference an active product (`is_active=True`).
3. `quantity` must be a positive integer.
4. Omitted `unit_price` defaults to `product.unit_price`.

#### `payment.py`

- `payment_number` → `read_only=True` (auto-generated in `Payment.save()`).
- `recorded_by` → injected in `PaymentViewSet.perform_create()`.
- Auto-transition-to-Paid logic (from `payment_create` view) lives in
  `PaymentViewSet.perform_create()`, not in the serializer.

#### `inventory.py`

- For manual adjustments: `movement_type` defaults to `'adjustment'`,
  `reference_type` defaults to `'ManualAdjustment'`.
- Validate `quantity != 0`.

---

## ViewSet Design

### `OrderViewSet` — state transitions

All six transitions are `@action(detail=True, methods=['post'])` methods.
Each follows the same pattern:

```
1. get_object() — fetches order, checks object permissions
2. Validate current status matches precondition
   → HTTP 409 if wrong: {"error": "Order is in <status>. Expected <required>."}
3. Perform state change + inventory side-effects inside transaction.atomic()
4. Return updated order via SalesOrderReadSerializer
```

```python
@action(detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsStaffOrAbove])
def submit(self, request, pk=None):    # Draft → Pending

@action(detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsManagerOrAdmin])
def confirm(self, request, pk=None):   # Pending → Confirmed + stock deduction

@action(detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsStaffOrAbove])
def ship(self, request, pk=None):      # Paid → Shipped

@action(detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsStaffOrAbove])
def deliver(self, request, pk=None):   # Shipped → Delivered

@action(detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsManagerOrAdmin])
def cancel(self, request, pk=None):    # Confirmed → Cancelled + stock restore

@action(detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsAdminRole])
def refund(self, request, pk=None):    # Paid → Refunded + stock restore
```

Inventory side-effects in `confirm`, `cancel`, and `refund` are copied verbatim from
the corresponding `views.py` functions. Do not abstract yet.

### `OrderViewSet` — edit and delete guards

- `update()`/`partial_update()`: only allowed when `status in (DRAFT, PENDING)`.
  Return `HTTP 409` otherwise.
- `destroy()`: only allowed when `status == DRAFT`. Return `HTTP 409` otherwise.

### `CustomerViewSet` — delete guard

Check `customer.orders.exists()` and return `HTTP 409` before attempting delete.
The DB enforces `on_delete=PROTECT` on `SalesOrder.customer` anyway, but a clean
409 is better than an unhandled `IntegrityError`.

### `PaymentViewSet` — create-only

Inherits from `CreateModelMixin, RetrieveModelMixin, ListModelMixin, GenericViewSet`
(not `ModelViewSet`) to avoid accidentally exposing `PUT`/`PATCH`/`DELETE`.

### `UserViewSet` — self-retrieval exception

Admin-only globally, except `retrieve` is also allowed when `request.user == instance`
so a user can fetch their own profile.

---

## Line-Item Convention

### The HTML app's pattern (not exposed in the API)

The existing `_parse_line_items()` function parses 1-indexed form fields
(`product_1`, `quantity_1`, `unit_price_1` … `product_N`). This is a
form-encoding artifact for HTML `<form>` submissions. It has no place in a
JSON API.

### API convention — nested array

Order creation and update send a standard nested `items` array:

```json
POST /api/v1/orders/
{
  "customer_id": 42,
  "discount_amount": "10.00",
  "tax_amount": "0.00",
  "notes": "Rush order",
  "items": [
    {"product_id": 7,  "quantity": 3, "unit_price": "29.99"},
    {"product_id": 12, "quantity": 1}
  ]
}
```

Omitting `unit_price` defaults to the product's current price. This is a complete
redesign of the input shape; it replaces `_parse_line_items` entirely for the API.

---

## Error Response Format

A custom exception handler in `api/exceptions.py` standardises all error responses:

```json
{
  "error": "Human-readable summary.",
  "code": "machine_readable_code",
  "details": {
    "field_name": ["Specific validation message."]
  }
}
```

`details` is omitted when there are no per-field errors (e.g., 401, 403, 404, 409).

| HTTP | Situation | `code` |
|------|-----------|--------|
| 400 | Validation failure | `validation_error` |
| 401 | No / invalid credentials | `not_authenticated` |
| 403 | Authenticated but wrong role | `permission_denied` |
| 404 | Resource not found | `not_found` |
| 409 | State machine violation or FK conflict | `invalid_transition` / `conflict` |
| 500 | Unhandled exception (never expose details) | `server_error` |

Configure in settings: `'EXCEPTION_HANDLER': 'api.exceptions.custom_exception_handler'`

---

## Pagination and Filtering

### Pagination

`PageNumberPagination`, `PAGE_SIZE = 25`. `?page_size=N` override, capped at 100.

```json
{
  "count": 150,
  "next": "http://localhost:8000/api/v1/orders/?page=2",
  "previous": null,
  "results": [...]
}
```

### Filtering per resource

| Endpoint | `?search=` targets | FilterSet fields | Notes |
|----------|--------------------|------------------|-------|
| `customers/` | first_name, last_name, email | — | |
| `products/` | sku, name, description | category, is_active | `?stock=low\|out\|ok\|all` via custom FilterSet |
| `orders/` | order_number, customer name | status, customer | `?date_from=`, `?date_to=` on `created_at` |
| `payments/` | payment_number, order_number | payment_method, sales_order | `?date_from=`, `?date_to=` |
| `inventory/` | — | product, movement_type | `?date_from=`, `?date_to=` |

`?stock=low|out|ok|all` on products requires a custom `FilterSet` in `api/filters.py`
because `is_low_stock` and `is_out_of_stock` are computed properties, not stored columns.
Phase 3 will implement this with queryset annotations.

---

## Dashboard Endpoint

`GET /api/v1/dashboard/` — an `APIView`, not a ViewSet. Returns:

```json
{
  "orders_this_month": 42,
  "revenue_this_month": "18450.00",
  "pending_payments_count": 7,
  "low_stock_count": 3,
  "recent_orders": [
    {"order_number": "SO-20260409-0012", "customer": "Jane Doe",
     "total_amount": "349.00", "status": "confirmed", "created_at": "..."}
  ]
}
```

Uses the same queryset logic as the `dashboard` view in `core/views.py`.
Permission: `IsAuthenticated`.

---

## Implementation Phases

### Phase 1 — Foundation

Everything else depends on this phase being correct before proceeding.

| Step | Action |
|------|--------|
| 1 | Add `djangorestframework>=3.15` and `django-filter>=24.0` to `requirements.txt` |
| 2 | Add `rest_framework`, `rest_framework.authtoken`, `api` to `INSTALLED_APPS` |
| 3 | Add `REST_FRAMEWORK` config block to `settings.py` |
| 4 | Add `path('api/v1/', include('api.urls'))` to `retailops/urls.py` |
| 5 | Create `api/__init__.py`, `api/apps.py` |
| 6 | Create `api/exceptions.py` |
| 7 | Create `api/permissions.py` |
| 8 | Create `api/views/__init__.py`, `api/views/auth.py` |
| 9 | Create `api/urls.py` with auth routes only |
| 10 | `python manage.py migrate` — creates `authtoken_token` table |
| 11 | Smoke-test: `POST /api/v1/auth/token/` with seeded credentials |

**Deliverable**: Authentication works. All other endpoints return 404 (not yet registered).

### Phase 2 — Reference Data (read-only, low risk)

Serializers + ViewSets for `Role`, `User`, `Customer`, `ProductCategory`.
Verify Admin-only enforcement on users and roles.

**Deliverable**: Customers, categories, users, and roles are readable via API.

### Phase 3 — Products and Inventory

- `ProductSerializer` with computed stock fields.
- Queryset annotation (`Coalesce(Sum(...), Value(0))`) to fix N+1 on stock properties.
- `api/filters.py` with `ProductFilter` for `?stock=low|out|ok|all`.
- `products/<id>/movements/` `@action`.
- `InventoryMovementViewSet` with `adjust/` endpoint — **fills the CLAUDE.md gap**
  (currently no UI for manual stock adjustments).

**Deliverable**: Products browsable with stock levels. Manual inventory adjustments
possible via API.

### Phase 4 — Orders and Payments (core business logic)

- Both order serializer variants (read/write).
- `OrderViewSet` with all six transition `@action` methods.
- Each transition: validate precondition → 409 if wrong → atomic state change +
  inventory movements.
- `PaymentViewSet` with auto-transition-to-Paid logic replicated from `payment_create`.
- Full transition testing with correct and incorrect roles.

**Deliverable**: Full order lifecycle accessible via API. Payments can be recorded.

### Phase 5 — Polish

- `dashboard/` summary endpoint.
- Throttling (`DEFAULT_THROTTLE_RATES`) to rate-limit unauthenticated token requests.
- Strip `BrowsableAPIRenderer` when `DEBUG=False`.
- `drf-spectacular` for OpenAPI schema auto-generation (add to requirements, optional).
- Address CLAUDE.md security items: rotate `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`.

---

## Critical Gotchas

### 1. N+1 queries on computed properties

`Product.current_stock`, `SalesOrder.amount_paid`, and `SalesOrder.amount_outstanding`
are Python `@property` methods that call `.aggregate()`. Each object in a list response
fires one SQL query per property. At 25 results per page with 3 properties, that is
75 extra queries per page load.

**Fix in Phase 3**: annotate the queryset instead of calling the property:

```python
from django.db.models import Coalesce, Sum, Value
from django.db.models import IntegerField

Product.objects.annotate(
    _stock=Coalesce(Sum('inventory_movements__quantity'), Value(0))
)
```

Override `get_current_stock(self, obj)` in the serializer to use `obj._stock` when
the annotation is present, falling back to `obj.current_stock`. Do not ship Phase 3
without this fix.

### 2. Race condition on order/payment number generation

Both `SalesOrder` and `Payment` use a "find last sequence number for today, add 1"
strategy in their `save()` overrides. Under concurrent API load (multiple processes
writing simultaneously), this produces duplicate numbers and hits the `unique=True`
constraint with an `IntegrityError`.

The HTML app avoids this in practice because it runs on a single development process.
The API will be hit concurrently. Document this and plan `select_for_update()` on a
lock row — or switch to UUID-based identifiers — in a follow-up sprint. Do not deploy
Phase 4 to a multi-worker production server without this fix.

### 3. `email` not `username` for authentication

The custom `User` model sets `USERNAME_FIELD = 'email'`. DRF's default
`ObtainAuthToken` serializer expects a field named `username`. Override with a custom
serializer that declares an `email` field and passes it as `username` to `authenticate()`:

```python
user = authenticate(request, username=email, password=password)
```

This works because Django's `ModelBackend` looks up by `USERNAME_FIELD`.

### 4. `customer.delete()` raises `IntegrityError`, not a clean error

`SalesOrder.customer` has `on_delete=PROTECT`. Attempting to delete a customer with
orders raises an `IntegrityError` at the DB level, which surfaces as a 500.
`CustomerViewSet.destroy()` must check `customer.orders.exists()` and return
`HTTP 409` with `{"error": "Cannot delete a customer with existing orders.", "code": "conflict"}`
before touching the DB.

### 5. The `order_detail` POST role gap from CLAUDE.md is fixed here

The HTML view has no `@role_required` on `order_detail` POST or `order_submit`. The
API intentionally does not inherit this gap:
- `update`/`partial_update` on `OrderViewSet` require `IsStaffOrAbove`.
- `submit` action requires `IsStaffOrAbove`.

### 6. `user_edit` dual-action pattern is not replicated

The HTML view uses a hidden `action` field to dispatch between profile-update and
password-change on the same URL. The API uses two separate endpoints:
- `PATCH /api/v1/users/<id>/` — profile fields
- `POST /api/v1/users/<id>/change-password/` — password change

### 7. CSRF and `SessionAuthentication`

Token authentication clients do not send CSRF tokens. DRF automatically exempts
`TokenAuthentication`-authenticated requests from CSRF. However, with
`SessionAuthentication` also configured (for the browsable API), DRF enforces CSRF
for session-authenticated requests — which is correct behaviour for browser access.
No action needed; just be aware that removing `SessionAuthentication` in production
also removes the CSRF requirement for session-based requests.

### 8. `User.role` is nullable everywhere

Every access to `request.user.role.name` must be guarded with a null check.
The `role_permission()` factory in `api/permissions.py` already does this. Do not
add direct `.role.name` accesses elsewhere in the API code without the same guard.
