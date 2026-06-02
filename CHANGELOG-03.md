# CHANGELOG-03

## RetailOps — Session 3 Build Log

This session completed the REST API layer for RetailOps. Sessions 1 and 2 built the
full Django HTML application (models, views, templates, URL routing, seed command).
Session 3 added a versioned REST API in a new `api/` Django app, leaving the HTML
application at `/` entirely untouched.

The session was structured around the `API_DESIGN.md` document written at its start
(see Session 3, Phase 0 below), which drove all subsequent implementation decisions.
Five numbered phases were executed in order.

---

## Phase 0 — API Design Document

**File created:** `API_DESIGN.md`

Before writing any code, the entire API was designed in a 15-section Markdown document.
This document governed all subsequent implementation work.

**Sections:**
1. Overview — what the API is and what it does not touch
2. Technology Decisions — DRF 3.15 vs FastAPI/ninja, Token vs JWT authentication
3. Repository Layout — `api/` app structure, which existing files are modified
4. Settings Changes — `INSTALLED_APPS`, `REST_FRAMEWORK` config block
5. URL Structure — complete route tree under `/api/v1/`
6. Authentication — `POST /auth/token/`, email-not-username gotcha
7. Permissions — `role_permission()` factory, per-resource matrix
8. Serializer Design — design rules, read/write splits, per-model notes
9. ViewSet Design — order transitions, edit/delete guards, customer delete guard
10. Line-Item Convention — JSON `items` array replacing HTML `product_N`/`quantity_N`
11. Error Response Format — `{error, code, details}` envelope, HTTP code table
12. Pagination and Filtering — `PageNumberPagination`, per-resource filter tables
13. Dashboard Endpoint — shape of `GET /api/v1/dashboard/` response
14. Implementation Phases — 5 ordered phases with deliverables
15. Critical Gotchas — 8 problems that would cause silent bugs or security holes

**Key design decisions recorded in the document:**

- **DRF over FastAPI/ninja**: Django ORM, `AUTH_USER_MODEL`, admin, and middleware
  are already integrated — no adaptation layer needed.
- **Token auth over JWT**: `rest_framework.authtoken` adds zero migration complexity.
  JWT upgrade path noted for v2 when refresh-token semantics are needed.
- **`app_name = 'api'`** in `api/urls.py` required to prevent URL name collision with
  `core/urls.py`, which registers `customer-list` and `user-list` at root level.
- **`SequenceCounter` model** planned to fix the race condition in order/payment number
  generation before Phase 4 could be deployed to a multi-worker server.

---

## Phase 1 — Foundation

**Files created:**
- `api/__init__.py`
- `api/apps.py`
- `api/exceptions.py`
- `api/permissions.py`
- `api/serializers/__init__.py`
- `api/views/__init__.py`
- `api/views/auth.py`
- `api/urls.py`

**Files modified:**
- `requirements.txt`
- `retailops/settings.py`
- `retailops/urls.py`
- `CLAUDE.md`

### `requirements.txt`

Added three new packages:

```
djangorestframework>=3.15,<3.16
django-filter>=24.0
```

(`drf-spectacular` was added in Phase 5.)

### `CLAUDE.md`

Three corrections applied to the project guidance file:

| Item | Before | After |
|------|--------|-------|
| View count | "29 views" | "30 views" |
| `SalesOrder` computed properties | Not mentioned | `amount_paid` and `amount_outstanding` documented as computed `@property` methods |
| Template dual-use | Not mentioned | `customer_form.html` and `user_form.html` noted as serving both create and edit operations |

### `retailops/settings.py`

Added to `INSTALLED_APPS`:
```python
'rest_framework',
'rest_framework.authtoken',
'django_filters',
'api',
```

Added `REST_FRAMEWORK` config block:
```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [...],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
    'DEFAULT_RENDERER_CLASSES': [...],
    'DEFAULT_FILTER_BACKENDS': [...],
    'NON_FIELD_ERRORS_KEY': 'errors',
    'EXCEPTION_HANDLER': 'api.exceptions.custom_exception_handler',
}
```

(Settings were substantially refactored again in Phase 5 — see below.)

### `retailops/urls.py`

```python
path('api/v1/', include('api.urls', namespace='api')),
```

The `namespace='api'` is required because `core/urls.py` registers URL names like
`customer-list` and `user-list` at the root level. Without namespacing, Python's
last-registration wins, and the API root would resolve to core's routes.

### `api/exceptions.py`

Custom DRF exception handler that enforces a consistent error envelope across all
endpoints:

```json
{"error": "Human-readable message.", "code": "machine_readable", "details": {...}}
```

- Translates `Http404` and `PermissionDenied` to DRF equivalents before processing
- Maps every DRF exception to the correct `code` value (`validation_error`,
  `not_authenticated`, `permission_denied`, `not_found`)
- 500s are logged and never expose internal details to the client
- `details` key is only present for 400 validation errors (per-field messages)

### `api/permissions.py`

`role_permission(*roles)` factory function that mirrors `core/decorators.role_required`
but returns a DRF `BasePermission` class:

```python
IsAdminRole      = role_permission('Admin')
IsManagerOrAdmin = role_permission('Manager', 'Admin')
IsStaffOrAbove   = role_permission('Staff', 'Manager', 'Admin')
```

Critical: every check guards `request.user.role is not None` before accessing `.name`
because `User.role` is a nullable FK.

### `api/views/auth.py`

**`ObtainTokenView`** — accepts `{email, password}`, returns
`{token, user_id, email, role_name}`. Uses a custom `TokenObtainSerializer` that
declares an `email` field and passes it as `username` to `authenticate()` — required
because DRF's built-in `ObtainAuthToken` expects a `username` field but the custom
`User` model sets `USERNAME_FIELD = 'email'`.

**`RevokeTokenView`** — `POST /auth/token/revoke/`, deletes the token, returns 204.
Tolerates the case where the token is already absent.

---

## Phase 2 — Reference Data

**Files created:**
- `api/serializers/role.py`
- `api/serializers/user.py`
- `api/serializers/customer.py`
- `api/serializers/category.py`
- `api/views/role.py`
- `api/views/user.py`
- `api/views/customer.py`
- `api/views/category.py`

### Serializers

**`RoleSerializer`** — read-only; all fields.

**`UserReadSerializer` / `UserWriteSerializer`**
- Read: nested `RoleSerializer`, excludes `password`, `last_login`, `is_superuser`
- Write: `password` write-only, validated in `create()`/`update()` using
  `user.set_password()` — never relies on model `save()` to hash
- `ChangePasswordSerializer`: separate serializer for the change-password action,
  validates `new_password == confirm_password`
- Email uniqueness validated in the serializer (case-insensitive, excludes self on update)

**`CustomerSerializer`**
- `full_name`: `SerializerMethodField` computed from `first_name + last_name`
- `user`: optional FK accepted as PK on write
- Email uniqueness validated (case-insensitive)

**`ProductCategoryNestedSerializer` / `ProductCategorySerializer`**
- Nested: compact `{id, name, display_name}` used when embedding category in product
  responses; `display_name` uses `ProductCategory.__str__()` which returns
  `"Parent › Child"` for nested categories
- Full: includes `subcategories` (PK list), self-parent validation in `validate()`

### ViewSets

**`RoleViewSet`** — `ReadOnlyModelViewSet`, Admin-only.

**`UserViewSet`** — list/create/update: Admin-only. `retrieve` also allows
`request.user == instance` (self-access). Custom `@action` methods:
- `change_password` — POST, updates password via `ChangePasswordSerializer`
- `deactivate` — POST, sets `is_active=False`; returns 409 if user tries to
  deactivate their own account
- `reactivate` — POST, sets `is_active=True`

**`CustomerViewSet`** — full `ModelViewSet`. `destroy()` checks
`customer.orders.exists()` before touching the DB and returns 409 if true.
This is required because `SalesOrder.customer` has `on_delete=PROTECT` — without this
guard, the DB raises an `IntegrityError` which surfaces as a 500.

**`ProductCategoryViewSet`** — `get_permissions()` split: read=`IsAuthenticated`,
write=`IsManagerOrAdmin`.

---

## Phase 3 — Products and Inventory

**Files created:**
- `api/serializers/product.py`
- `api/serializers/inventory.py`
- `api/filters.py`
- `api/views/product.py`
- `api/views/inventory.py`

**Critical Gotcha #1 addressed**: N+1 queries on computed stock properties.

### N+1 Fix

`Product.current_stock`, `is_low_stock`, and `is_out_of_stock` are `@property` methods
that each call `.aggregate()`. On a 25-row list page that is up to 75 extra SQL queries.

**Fix**: `ProductViewSet.get_queryset()` annotates the queryset:

```python
Product.objects.annotate(
    _stock=Coalesce(
        Sum('inventory_movements__quantity'),
        Value(0, output_field=IntegerField()),
    )
)
```

`ProductSerializer` reads `obj._stock` when the annotation is present, falls back to
`obj.current_stock` when not (e.g., in the shell or tests).

An additional N+1 was found after the first fix: `ProductCategory.__str__()` calls
`self.parent_category`, triggering a query per category row. Resolved by changing:
```python
.select_related('category')
# → 
.select_related('category', 'category__parent_category')
```
Result: entire product list page = **1 SQL query**.

Note: `Coalesce` must be imported from `django.db.models.functions`, not
`django.db.models` — an `ImportError` was hit and fixed during development.

### `api/filters.py` — `ProductFilter`

Custom `FilterSet` with a `?stock=out|low|ok|all` parameter backed by the `_stock`
annotation:

```python
def filter_stock(self, queryset, name, value):
    if value == 'out': return queryset.filter(_stock__lte=0)
    if value == 'low': return queryset.filter(_stock__gt=0, _stock__lte=F('low_stock_threshold'))
    if value == 'ok':  return queryset.filter(_stock__gt=F('low_stock_threshold'))
    return queryset
```

### `api/views/product.py`

`ProductViewSet` with `get_permissions()` split: read=`IsAuthenticated`,
write=`IsManagerOrAdmin`. A `movements` `@action` (`GET /products/<id>/movements/`)
returns a paginated `InventoryMovementSerializer` list.

### `api/serializers/inventory.py` + `api/views/inventory.py`

`InventoryMovementViewSet` — list and retrieve only (no CRUD; movements are immutable).

**Fills CLAUDE.md gap**: `POST /api/v1/inventory/adjust/` records manual stock
adjustments (Manager+). `ManualAdjustmentSerializer` fixes `movement_type='adjustment'`
and `reference_type='ManualAdjustment'`; validates `quantity != 0`.

---

## Phase 4 — Orders and Payments

**Files created:**
- `core/migrations/0002_sequencecounter.py`
- `api/serializers/order.py`
- `api/serializers/payment.py`
- `api/views/order.py`
- `api/views/payment.py`

**Files modified:**
- `core/models.py`
- `api/filters.py`
- `api/urls.py`

**Critical Gotcha #2 addressed**: Race condition on order/payment number generation.

### `SequenceCounter` model (`core/models.py`)

The original `_generate_order_number()` and `_generate_payment_number()` methods used
a "find last sequence number for today, increment by 1" strategy. Under concurrent
API load (multiple workers writing simultaneously) this produces duplicate numbers and
hits the `unique=True` constraint with an `IntegrityError`.

**Fix**: New `SequenceCounter` model with `prefix` (unique) + `last_value` fields.
`next_value(prefix)` classmethod acquires a row-level lock via `select_for_update()`
inside `transaction.atomic()`:

```python
@classmethod
def next_value(cls, prefix: str) -> int:
    with transaction.atomic():
        cls.objects.get_or_create(prefix=prefix, defaults={'last_value': 0})
        obj = cls.objects.select_for_update().get(prefix=prefix)
        obj.last_value += 1
        obj.save(update_fields=['last_value'])
        return obj.last_value
```

All concurrent callers for the same prefix are serialised through the row lock.

`_generate_order_number()` and `_generate_payment_number()` were updated to call
`SequenceCounter.next_value(prefix)` instead of scanning existing rows.

### `core/migrations/0002_sequencecounter.py`

Creates the `SequenceCounter` table. Includes a `RunPython` operation
`initialize_counters` that scans all existing `SalesOrder.order_number` and
`Payment.payment_number` values, extracts prefix/sequence via `rpartition('-')`,
and creates a `SequenceCounter` row at `max(seq)` per prefix. New numbers continue
seamlessly from the existing sequence.

### `api/serializers/order.py`

**`SalesOrderItemWriteSerializer`** — validates a single `{product_id, quantity, unit_price?}` entry. `unit_price` defaults to `product.unit_price` if omitted.

**`SalesOrderReadSerializer`** — all fields read-only; nested `CustomerSerializer` and
`SalesOrderItemReadSerializer`; `amount_paid`/`amount_outstanding` as
`SerializerMethodField` with `_amount_paid` annotation fallback (same N+1 fix
applied as for products).

**`SalesOrderWriteSerializer`** — accepts `{customer_id, discount_amount, tax_amount, notes, items[]}`. Replaces the HTML app's `product_N`/`quantity_N` convention entirely.
`_apply_items()` atomically replaces all line items and recalculates `subtotal` and
`total_amount`. Validates that `items` is non-empty on create.

### `api/filters.py` additions

`SalesOrderFilter` — `?customer`, `?status`, `?date_from`, `?date_to`.  
`PaymentFilter` — `?sales_order`, `?payment_method`, `?date_from`, `?date_to`.

### `api/views/order.py`

`OrderViewSet` with all six state-transition `@action` methods, each following the pattern:

```
1. get_object()
2. _require_status(order, expected) → 409 if wrong
3. Atomic state change + inventory side-effects
4. Return updated order via SalesOrderReadSerializer
```

| Action | Transition | Role | Side-effect |
|--------|-----------|------|-------------|
| `submit` | Draft → Pending | Staff+ | — |
| `confirm` | Pending → Confirmed | Manager+ | Negative `InventoryMovement` per item (bulk_create) |
| `ship` | Paid → Shipped | Staff+ | — |
| `deliver` | Shipped → Delivered | Staff+ | — |
| `cancel` | Confirmed → Cancelled | Manager+ | Positive `InventoryMovement` per item (bulk_create) |
| `refund` | Paid → Refunded | Admin only | Positive `InventoryMovement` per item (bulk_create) |

Edit/update guarded to Draft-only (409 otherwise). Delete guarded to Draft-only.

`create()` and `update()` both return `SalesOrderReadSerializer` for the response —
DRF's default `CreateModelMixin.create()` would return the write serializer, so both
methods are overridden.

**Fixes CLAUDE.md role gap**: `update`/`partial_update` require `IsStaffOrAbove`;
`submit` requires `IsStaffOrAbove` — the HTML `order_detail` POST and `order_submit`
views had no role restriction at all.

### `api/views/payment.py`

`PaymentViewSet` — create/list/retrieve only (no update/delete; payments are immutable
financial records). `PaymentSerializer` validates that the target order is in
`CONFIRMED` status.

**Auto-Paid transition**: `perform_create()` runs inside `transaction.atomic()`.
After saving the payment it re-reads the total paid with `select_for_update()` to
prevent a concurrent race condition where two simultaneous payments both trigger the
transition:

```python
total_paid = Payment.objects.filter(sales_order=order).select_for_update()
             .aggregate(total=Sum('amount'))['total']
if total_paid >= order.total_amount:
    order.status  = SalesOrder.PAID
    order.paid_at = timezone.now()
    order.save(...)
```

---

## Phase 5 — Polish and Production Readiness

**Files created:**
- `api/pagination.py`
- `api/views/dashboard.py`

**Files modified:**
- `retailops/settings.py` (substantial refactor)
- `api/urls.py`
- `api/views/auth.py`
- `api/views/dashboard.py`
- `api/serializers/category.py`
- `api/serializers/customer.py`
- `api/serializers/inventory.py`
- `api/serializers/order.py`
- `api/serializers/payment.py`
- `api/serializers/product.py`
- `api/serializers/user.py`
- `requirements.txt`

### `retailops/settings.py` — Security Hardening

The three CLAUDE.md security gaps were addressed:

**`SECRET_KEY`** — read from `DJANGO_SECRET_KEY` env var. If the env var is absent,
falls back to the insecure placeholder **only when `DEBUG=True`**. When `DEBUG=False`,
startup raises `RuntimeError` immediately — impossible to accidentally deploy with the
insecure key:

```python
if not DEBUG and SECRET_KEY == _SECRET_KEY_DEFAULT:
    raise RuntimeError('DJANGO_SECRET_KEY must be set...')
```

**`DEBUG`** — read from `DJANGO_DEBUG` env var; defaults to `True` for local
development safety.

**`ALLOWED_HOSTS`** — read from `DJANGO_ALLOWED_HOSTS` env var (comma-separated list).
When absent, defaults to `['localhost', '127.0.0.1']` in dev and `[]` in production.

**Conditional DRF renderers and authentication**: `SessionAuthentication` and
`BrowsableAPIRenderer` are appended to their respective lists only when `DEBUG=True`.
In production Django automatically serves pure JSON with token-only authentication —
no code change required at deploy time.

**Throttling** added to `REST_FRAMEWORK`:
```python
'DEFAULT_THROTTLE_CLASSES': [
    'rest_framework.throttling.AnonRateThrottle',
    'rest_framework.throttling.UserRateThrottle',
],
'DEFAULT_THROTTLE_RATES': {
    'anon': '20/min',   # limits credential-stuffing on POST /auth/token/
    'user': '600/min',
},
```

### `api/pagination.py` — `CappedPageNumberPagination`

Replaces the bare `PageNumberPagination` configured in settings. Clients may request
a custom page size via `?page_size=N` but it is hard-capped at 100:

```python
class CappedPageNumberPagination(PageNumberPagination):
    page_size_query_param = 'page_size'
    max_page_size = 100
```

### `api/views/dashboard.py` — Dashboard Endpoint

`GET /api/v1/dashboard/` — `APIView`, `IsAuthenticated`.

Returns:
```json
{
    "orders_this_month":      13,
    "revenue_this_month":     "152.37",
    "pending_payments_count": 2,
    "low_stock_count":        1,
    "recent_orders":          [...]
}
```

`low_stock_count` uses a queryset annotation (`_stock`) resolved in SQL — avoids the
HTML dashboard's approach of loading all products into Python and iterating. Two SQL
queries total: one for `low` stock, one for `out` stock.

`revenue_this_month` counts orders that reached `Paid`/`Shipped`/`Delivered` and whose
`paid_at` falls in the current calendar month — mirrors `core/views.dashboard` exactly.

### `drf-spectacular` — OpenAPI Schema

Added to `requirements.txt`: `drf-spectacular>=0.27`

Three new routes registered in `api/urls.py`:
```
GET /api/v1/schema/          Raw OpenAPI 3 YAML/JSON
GET /api/v1/schema/swagger/  Swagger UI
GET /api/v1/schema/redoc/    ReDoc UI
```

`SPECTACULAR_SETTINGS` block added to `settings.py`:
```python
{
    'TITLE': 'RetailOps API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
}
```

`DEFAULT_SCHEMA_CLASS` set to `drf_spectacular.openapi.AutoSchema`.

### Schema Annotation Fixes

`drf-spectacular` initially generated 2 Errors (endpoints excluded from schema) and
15 warnings (fields defaulting to `string` type). All were resolved:

| Problem | Fix |
|---------|-----|
| `RevokeTokenView` excluded (no serializer_class) | `@extend_schema(request=None, responses={204: None})` |
| `ObtainTokenView` response undocumented | `@extend_schema(request=TokenObtainSerializer, responses={200: inline_serializer(...)})` |
| `DashboardView` excluded (no serializer_class) | `@extend_schema(responses={200: _DashboardResponseSerializer})` with `inline_serializer` |
| `SerializerMethodField` type unknown (15 instances) | Python return-type annotations on all `get_*` methods (`-> str`, `-> int`, `-> bool`) |
| `_CreatedByField` type unknown | `@extend_schema_field(OpenApiTypes.STR)` class decorator |
| `get_product` in inventory serializer | `@extend_schema_field({'type': 'object', 'properties': {...}})` |
| `role_name` model property type unknown | Explicit `role_name = serializers.CharField(read_only=True, allow_null=True)` declaration in `UserReadSerializer` |

Final schema state: **0 errors, 0 warnings**. All 30+ endpoints documented.

---

## Complete File Inventory

### New files created this session

```
api/
  __init__.py
  apps.py
  exceptions.py
  filters.py
  pagination.py
  permissions.py
  urls.py
  serializers/
    __init__.py
    auth.py           (TokenObtainSerializer — no standalone file; lives in views/auth.py)
    category.py
    customer.py
    inventory.py
    order.py
    payment.py
    product.py
    role.py
    user.py
  views/
    __init__.py
    auth.py
    category.py
    customer.py
    dashboard.py
    inventory.py
    order.py
    payment.py
    product.py
    role.py
    user.py

core/migrations/
  0002_sequencecounter.py
```

### Existing files modified this session

```
CLAUDE.md                   — 3 corrections (view count, SalesOrder properties, template dual-use)
requirements.txt            — added djangorestframework, django-filter, drf-spectacular
retailops/settings.py       — INSTALLED_APPS, REST_FRAMEWORK block, security hardening
retailops/urls.py           — added api/v1/ include with namespace
core/models.py              — SequenceCounter model; updated _generate_order_number,
                              _generate_payment_number; added `from django.db import transaction`
```

---

## Bugs Found and Fixed During This Session

| Bug | Root cause | Fix |
|-----|-----------|-----|
| API root showed `{}` (empty) | Server started with `--noreload` before ViewSet registrations; stale process | Kill all Python processes, restart |
| API root pointed to core routes (`/users/` not `/api/v1/users/`) | URL name collision between `api/urls.py` and `core/urls.py` | Added `app_name = 'api'` + `namespace='api'` |
| `ImportError: cannot import name 'Coalesce' from 'django.db.models'` | `Coalesce` is in `django.db.models.functions`, not `django.db.models` | Fixed import |
| 4 SQL queries per product list despite N+1 fix | `ProductCategory.__str__()` calls `self.parent_category`; `select_related` only covered `category`, not `category__parent_category` | Extended to `select_related('category', 'category__parent_category')` |
| Order create response returned write serializer fields (no `id`, `order_number`) | DRF `CreateModelMixin.create()` returns the write serializer by default | Overrode `create()` in `OrderViewSet` to return `SalesOrderReadSerializer` |
| `revenue_this_month` showed excessive decimal places (`152.370000000000`) | `str(Decimal)` preserves all trailing digits from SQL `SUM` precision | Changed to `f'{value:.2f}'` |
| `_CreatedByField` and `DashboardView`/`RevokeTokenView` excluded from OpenAPI schema | drf-spectacular cannot introspect custom `Field` subclasses or plain `APIView` without hints | `@extend_schema_field(OpenApiTypes.STR)` on field class; `@extend_schema(...)` on views |

---

## Known Remaining Gaps

The following items are out of scope for this session but documented for future work:

| Area | Gap |
|------|-----|
| Auth | No forgot-password / self-service password reset flow (pre-existing) |
| Security | `DEBUG=True` and insecure `SECRET_KEY` remain defaults for local development — set env vars before any deployment |
| API | No rate-limiting per-user on specific endpoints beyond the global `UserRateThrottle` |
| Orders | Server-side validation that an order has `> 0` line items before `confirm` is called (partial: added to `confirm` action; not present on `submit`) |
| Schema | Transition `@action` endpoints (submit, confirm, ship, deliver, cancel, refund) show generic request body in Swagger because they take no request body — could add `@extend_schema(request=None)` for clarity |
| Tests | No test suite exists for either the HTML app or the API |
