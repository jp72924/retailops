# CHANGELOG-08 — Kiosk Overhaul, Customer Identity Fields, Venezuelan Seed Data, Kiosk Search Fix, and Category CRUD

**Session date:** 2026-04-18 / 2026-04-19  
**Scope:** Five interconnected workstreams across this session: (1) a comprehensive kiosk integration overhaul removing the verification mechanism, switching to real emails, and rebuilding the registration screen to match the Farmatodo autopago demo; (2) customer identity fields (national ID, date of birth, gender) surfaced throughout the admin UI; (3) Venezuelan seed data replacing the US-centric placeholder records; (4) a kiosk product search bug fix caused by a mismatched authentication realm; and (5) full CRUD for product categories.

---

## Overview

This session touched every layer of the application: Django models, migrations, views, templates, URLs, REST API, the kiosk PWA JavaScript, and the seed command. The unifying theme is consistency — the kiosk now collects the same customer identity data the admin UI displays, the seed database now reflects the Venezuelan deployment context, and categories now have the same management interface already offered for customers, products, and users.

---

## 1 — Kiosk Integration Overhaul

### 1.1 Verification Mechanism Removed

The `VerificationRequest` model and its entire supporting infrastructure were removed. The previous design required an employee to approve high-value kiosk purchases in a separate UI before the order was marked as delivered. This was replaced with immediate delivery on checkout completion.

**Migration `core/migrations/0005_remove_verification.py`** — single migration combining:
- `DeleteModel('VerificationRequest')`
- `RemoveField('kioskstation', 'verification_rate')`
- `AddField('customer', 'date_of_birth')` — `DateField(null=True, blank=True)`
- `AddField('customer', 'gender')` — `CharField(max_length=1, choices=[('M','Masculino'),('F','Femenino')], blank=True, default='')`

**`core/models.py`**
- Removed the `VerificationRequest` class (≈45 lines)
- Removed `verification_rate` from `KioskStation`
- Added `date_of_birth` and `gender` fields to `Customer`

**`core/admin.py`**
- Removed `VerificationRequest` from imports and registrations
- Removed `verification_rate` from `KioskStationAdmin.list_display`

**`api/kiosk/views.py`** — complete rewrite of the checkout flow:
- Removed `KioskVerificationStatusView`, `VerificationPendingListView`, `VerificationApproveView`, `VerificationRejectView` (4 views eliminated)
- `_execute_checkout()` now unconditionally sets `order.status = SalesOrder.DELIVERED` inside the atomic transaction; removed the random roll and `VerificationRequest.objects.create()` call
- `KioskReceiptView` — removed verification check and `verification_required` field from receipt serializer

**`api/kiosk/urls.py`** — 4 URL patterns removed; 6 remain (identify, register, product-lookup, checkout, receipt, heartbeat)

**`api/kiosk/serializers.py`**
- Removed `VerificationListSerializer`
- Removed `verification_required` field from `KioskReceiptSerializer`

**Kiosk PWA — `kiosk/app/`**
- `services/orders.js` — removed `pollVerification` export; `atomicCheckout` no longer includes `verification_required` in its return
- `store.js` — removed `'verification_required'` from `PERSISTED_KEYS`
- `components/ProcessingScreen.js` — removed all polling infrastructure (`_polling`, `_pollTimer`, `POLL_INTERVAL_MS`, `_showVerificationWaiting`, `_startPolling`, `_showRejected`); `_runCheckout()` always calls `navigate('success')` on success
- `components/SuccessScreen.js` — removed `verificationRequired` store read; always shows the simple "Puedes retirarte con tus productos. ¡Hasta pronto!" message

### 1.2 Real Email Addresses in Kiosk Registration

The previous kiosk flow auto-generated placeholder emails (`kiosk-<uuid>@kiosk.internal`) to satisfy the `Customer.email` unique constraint while keeping the registration form short. This was replaced with a required real email field.

**`api/kiosk/serializers.py` — `KioskRegisterSerializer`**
- Added `email = serializers.EmailField(max_length=254)` as a required field
- Added `validate_email()` checking uniqueness: raises `ValidationError` with code `'duplicate_email'` if already registered
- `create()` passes `email=validated_data['email']` instead of the generated placeholder

**`kiosk/app/services/customer.js`** — `createCustomer()` now accepts `email` in its parameter object and includes it in the POST body

**`kiosk/app/api.js`** — added `'duplicate_email'` → Spanish error mapping in `_codeToSpanish()`

### 1.3 Registration Screen Rebuilt (Farmatodo Autopago Demo)

The 3-field placeholder registration form was replaced with an 8-field form matching the Farmatodo autopago kiosk demo exactly.

**`kiosk/app/components/RegistroScreen.js`** — complete rewrite:

| Field | Input type | Notes |
|-------|-----------|-------|
| Nombre | text | `id="reg-first"` |
| Apellido | text | `id="reg-last"` |
| Correo electrónico | email | `id="reg-email"` |
| Teléfono | tel | `id="reg-phone"` |
| Fecha de nacimiento | date | `id="reg-dob"` |
| Sexo | toggle buttons | `.sexo-btn` (M/F), state in `selectedGender` variable |
| Estado | select | Populated from `_ESTADOS` (24 Venezuelan states) |
| Ciudad | select | Dynamically populated from `_CIUDADES_MAP` on estado `change` |

- `_CIUDADES_MAP` — 24-key object mapping every Venezuelan state to its major cities, identical to the Farmatodo demo source
- `_validate()` — checks all 8 fields; gender validated via `selectedGender !== ''`; maps API error codes (`date_of_birth`, `gender`, `state`, `city`) to field-level error element IDs
- `createCustomer()` call passes: `{ firstName, lastName, email, phone, dateOfBirth, gender, state, city }`

**`kiosk/styles.css`** — added `.sexo-btns`, `.sexo-btn`, and `.sexo-btn.selected` styles mirroring the `.cedula-type-btn` toggle pattern already present

**`api/kiosk/serializers.py` — `KioskRegisterSerializer`** — extended with the new fields required by the rebuilt form:
- `date_of_birth = serializers.DateField()`
- `gender = serializers.ChoiceField(choices=['M', 'F'])`
- `state = serializers.CharField(max_length=100)`
- `city = serializers.CharField(max_length=100)`
- `create()` passes all fields to `Customer.objects.create()`; `country` hardcoded to `'Venezuela'`

### 1.4 Inline Category Creation in Product Form

A "+ New" shortcut was added to the Product form's Category field, letting Manager/Admin users create a category without leaving the product form.

**`core/views.py` — `category_create_ajax`** (new view):
- `@require_POST`, `@login_required`, `@role_required('Manager', 'Admin')`
- Accepts: `name` (required, unique), `description` (optional), `parent_id` (optional FK)
- Returns JSON: `{"ok": true, "id": pk, "name": name, "display_name": str(cat)}`
- Error responses: `{"ok": false, "error": "..."}` with HTTP 400

**`core/urls.py`**
- Added: `path('inventory/categories/create/', views.category_create_ajax, name='category-create')`

**`core/templates/core/product_form.html`**
- Wrapped the Category label in a flex row with a `<a href="#" id="btn-new-cat">+ New</a>` link (right-aligned, muted)
- Added an inline modal (`id="modal-new-cat"`) with Name, Description, and Parent Category fields
- Added `{% block extra_js %}` with vanilla JS: click opens modal; `fetch()` POST to `category-create`; on success, appends new `<option>` to both the main category dropdown and the parent dropdown, auto-selects the new category, closes the modal; error shown in `#nc-error` span

---

## 2 — Customer Identity Fields in Admin UI

### 2.1 Model Fields

`national_id`, `date_of_birth`, and `gender` were added to `Customer` (in migration `0005_remove_verification.py` detailed above). These fields were already collected by the kiosk API; this work surfaces them in the admin-facing templates and form handlers.

`national_id` field characteristics:
- `CharField(max_length=20, unique=True, null=True, blank=True)`
- Nullable unique — multiple customers without a national ID (stored as `NULL`, not `''`)
- Views use `national_id or None` pattern when saving to avoid unique constraint violations on empty strings

### 2.2 Templates Updated

**`core/templates/core/customer_list.html`**
- Added "ID Number" column header and `{{ customer.national_id|default:"—" }}` cell (`.td-mono`) between Name and Email columns

**`core/templates/core/customer_detail.html`**
- Added three rows to the Contact Details card after the Phone row:
  - ID Number: `{{ customer.national_id|default:"—" }}`
  - Date of Birth: `{{ customer.date_of_birth|date:"M j, Y"|default:"—" }}`
  - Gender: `Masculino` / `Femenino` / `—` conditional

**`core/templates/core/customer_form.html`**
- Phone placeholder updated from `+1 (555) 000-0000` to `04XX-XXXXXXX`
- Three new optional fields added to the Personal Information `form-grid-2` after Phone:
  - `national_id` — text input, `maxlength="20"`, placeholder "Ej: V12345678"
  - `date_of_birth` — `type="date"` input
  - `gender` — `<select>` with `— Not specified —`, `M Masculino`, `F Femenino`; selection state driven by `form_data.gender`

**`core/templates/core/order_detail.html`**
- Added to read-only customer panel (non-draft state): `{% if order.customer.national_id %}<div class="info-row"><span class="info-label">ID Number</span>{{ order.customer.national_id }}</div>{% endif %}`

### 2.3 Views Updated

**`core/views.py` — `customer_create`**
- Reads `national_id`, `date_of_birth`, `gender` from POST
- Validates: `national_id` uniqueness if non-empty; `gender` must be `''`, `'M'`, or `'F'`; `date_of_birth` parsed with `datetime.date.fromisoformat()` wrapped in try/except
- Added `import datetime` at top of file
- Passes fields to `Customer.objects.create()`; uses `national_id or None`

**`core/views.py` — `customer_edit`**
- Same field reading and validation (excludes current pk from uniqueness check)
- Pre-populates `form_data` with `national_id or ''`, `date_of_birth.isoformat() if customer.date_of_birth else ''`, `customer.gender`

---

## 3 — Venezuelan Seed Data

### Problem

The seed command populated the database with 6 US customers (Alice Johnson in Austin TX, etc.) that were mismatched with the Venezuelan deployment context of the kiosk and did not exercise the three new `Customer` fields.

### Solution

**`core/management/commands/seed.py`**

Added `import datetime` at the top.

Replaced the `CUSTOMERS` constant (6 tuples, US records, no identity fields) with 10 Venezuelan records. New tuple shape:
```python
# (first, last, national_id, email, phone, addr1, city, state, postal, country, dob, gender, notes)
```

| # | Name | National ID | DOB | Gender | City | State |
|---|------|------------|-----|--------|------|-------|
| 1 | María González | V12345678 | 1990-03-15 | F | Caracas | Distrito Capital |
| 2 | Carlos Hernández | V23456789 | 1985-07-22 | M | Valencia | Carabobo |
| 3 | Sofía Martínez | V34567890 | 1995-11-08 | F | Maracaibo | Zulia |
| 4 | Jesús Rodríguez | V45678901 | 1978-04-30 | M | Barquisimeto | Lara |
| 5 | Ana López | V56789012 | 2000-01-20 | F | Maracay | Aragua |
| 6 | Miguel Pérez | V67890123 | 1992-09-05 | M | Maturín | Monagas |
| 7 | Laura Ramírez | V78901234 | 1988-06-11 | F | Porlamar | Nueva Esparta |
| 8 | Andrés Torres | V89012345 | 1975-12-03 | M | Barinas | Barinas |
| 9 | Valentina Flores | E10234567 | 2003-08-27 | F | Los Teques | Miranda |
| 10 | Roberto Castillo | V11223344 | 1968-05-19 | M | San Cristóbal | Táchira |

All records use Venezuelan phone format (`04XX-XXXXXXX`), 4-digit Venezuelan postal codes, realistic street addresses, and `country='Venezuela'`. Valentina Flores uses prefix `E` (foreign national cedula) to exercise that variant.

**`_seed_customers()`** — updated to unpack the 13-field tuple and pass `national_id`, `date_of_birth`, `gender` to `Customer.objects.get_or_create()`.

**`_seed_orders()`** — updated to destructure all 10 customers:
```python
maria, carlos, sofia, jesus, ana, miguel, laura, andres, valentina, roberto = customers
```
Order notes translated to Spanish. Two new orders added to cover the additional customers:
- **Order 9** — Laura Ramírez, Delivered (T-shirts + notebook, card payment)
- **Order 10** — Andrés Torres, Paid (pens + mouse, cash payment)

Final seed summary: 10 customers, 10 orders (1 Draft, 1 Pending, 2 Confirmed, 2 Paid, 1 Shipped, 2 Delivered, 1 Cancelled), 6 payments, 23 inventory movements.

---

## 4 — Kiosk Product Search Bug Fix

### Root Cause

The kiosk product search bar showed "Error al buscar. Intenta nuevamente." instead of results. Tracing the network calls revealed the issue:

1. `kiosk/app/services/products.js` called `api.get('/products/?search=...')`.
2. `api.js` builds `BASE = CONFIG.BASE_URL + CONFIG.API_PATH` where `API_PATH` defaults to `'/api/v1'`.
3. The full URL resolved to `http://…/api/v1/products/?search=…` — the **standard admin DRF endpoint**.
4. That endpoint uses `Authorization: Token <key>` authentication.
5. The kiosk sends `Authorization: KioskKey <key>`, which the admin endpoint does not recognise.
6. DRF treats the request as anonymous → `IsAuthenticated` returns 403.
7. `api.js` maps all 401/403 responses to `ApiError('Estación desactivada…', 'station_deactivated')`.
8. `ScanScreen._doSearch()` catches the error and renders "Error al buscar."

The same bug affected `getProduct(id)` (the pre-checkout stock re-validation call), which also targeted `/api/v1/products/<id>/` instead of a kiosk-authenticated endpoint.

### Fix

**`api/kiosk/serializers.py` — `KioskProductSerializer`**
- Added `is_out_of_stock` and `is_low_stock` as `SerializerMethodField` entries (required by `ScanScreen._addProduct()` for out-of-stock guard and low-stock banner)
- Updated `Meta.fields` to include all five computed properties

**`api/kiosk/views.py`** — two new views added:

`KioskProductSearchView` — `GET /api/v1/kiosk/products/`
- Uses `KioskAPIMixin` (KioskKey auth + `IsKioskStation` permission)
- Accepts `?search=` query param; filters `Product.objects.filter(is_active=True)` with `Q(name__icontains=q) | Q(sku__icontains=q)`
- Returns `{'results': KioskProductSerializer(products, many=True).data}` — up to 6 results, ordered by name
- Throttled with `KioskScanThrottle`

`KioskProductDetailView` — `GET /api/v1/kiosk/products/<id>/`
- Fetches one active product by PK for pre-checkout stock re-validation
- Returns `KioskProductSerializer` representation or 404

**`api/kiosk/urls.py`**
- Added `path('products/', KioskProductSearchView.as_view(), name='product-search')`
- Added `path('products/<int:pk>/', KioskProductDetailView.as_view(), name='product-detail')`
- Existing `product/<str:sku>/` barcode lookup kept unchanged

**`kiosk/app/services/products.js`** — updated both exports:
- `searchProducts(query, signal)` → `api.get('/kiosk/products/?search=...')`  (was `/products/?...`)
- `getProduct(id)` → `api.get('/kiosk/products/${id}/')` (was `/products/${id}/`)

The service file comment was also updated to clarify that all calls go through `/api/v1/kiosk/products/` with KioskKey auth.

---

## 5 — Product Category Full CRUD

### Problem

`ProductCategory` had only an AJAX inline-create endpoint (`POST /inventory/categories/create/`) used by the product form modal. There was no way to list, browse, rename, reparent, or delete categories from the admin UI.

### Design

The new pages follow the identical pattern used by Customers, Inventory, and Users: page header + filter bar + table with actions + pagination + empty state. A shared create/edit template is used (same approach as `customer_form.html`). The AJAX endpoint and its URL name `'category-create'` are preserved unchanged so the product form modal continues to work.

### New `_is_descendant` helper (`core/views.py`)

Iterative BFS over a category's `subcategories` tree, used to detect circular parent assignments during edit. Uses a `visited` set to handle any corrupt data and avoids Python's recursion limit on deep trees.

```python
def _is_descendant(ancestor, candidate):
    visited = set()
    queue = list(ancestor.subcategories.values_list('pk', flat=True))
    while queue:
        pk = queue.pop(0)
        if pk in visited: continue
        visited.add(pk)
        if pk == candidate.pk: return True
        queue.extend(ProductCategory.objects.filter(
            parent_category_id=pk).values_list('pk', flat=True))
    return False
```

### New Views (`core/views.py`)

Imports added: `Count` from `django.db.models`; `ProtectedError` from `django.db.models.deletion`.

**`category_list`** (`@login_required`)
- Builds queryset with `select_related('parent_category')` and `annotate(product_count=Count('products'))`
- Supports `?q=` search across name and description
- Paginates at 25 per page

**`category_create`** (`@login_required`, `@role_required('Manager', 'Admin')`)
- Validates: name required; ≤ 150 chars; unique
- Resolves optional `parent_category` FK; errors if pk invalid
- On success: `messages.success` + redirect to `category-list`
- On error: re-renders form with `errors` dict and `form_data` for re-population

**`category_edit`** (`@login_required`, `@role_required('Manager', 'Admin')`)
- GET: pre-populates `form_data` from the existing object (`parent_category` stored as string pk)
- POST: same validation as create (uniqueness check excludes self); additionally guards:
  - Self-parent: `errors['parent_category'] = 'A category cannot be its own parent.'`
  - Circular reference via `_is_descendant`: `errors['parent_category'] = 'This would create a circular reference.'`

**`category_delete`** (`@require_POST`, `@login_required`, `@role_required('Manager', 'Admin')`)
- Wraps `category.delete()` in try/except for `ProtectedError` (raised when products are assigned)
- On `ProtectedError`: `messages.error` with actionable message, redirect to list (no crash)
- On success: `messages.success`, redirect to list
- Subcategories are unaffected (their `parent_category` becomes `NULL` via `SET_NULL` — they become top-level)

### New URL Patterns (`core/urls.py`)

```python
path('inventory/categories/',                  views.category_list,   name='category-list'),
path('inventory/categories/new/',              views.category_create, name='category-create-page'),
path('inventory/categories/<int:pk>/edit/',    views.category_edit,   name='category-edit'),
path('inventory/categories/<int:pk>/delete/',  views.category_delete, name='category-delete'),
```

The AJAX endpoint keeps its name `'category-create'` (referenced in `product_form.html`); the new page-based create uses `'category-create-page'` to avoid collision.

### Navbar (`core/templates/core/base.html`)

Added "Categories" link between Inventory and Payments:
```html
<a href="{% url 'category-list' %}" class="nav-link {% if 'category' in url_name %}active{% endif %}">Categories</a>
```
The `'category' in url_name` check activates the link on all four new URL names plus `'category-create'` (the AJAX endpoint, though it never renders a nav-accessible page).

### New Template: `category_list.html`

Table columns: **Name** (with optional description excerpt below), **Type** (badge: `badge-active` → Parent / `badge-pending` → Subcategory), **Parent** (link to parent's edit page, or "—"), **Products** (from the `product_count` annotation), **Created**, **Actions** (Edit + Delete).

Delete button: inline POST form with `onsubmit="return confirm(...)"` matching the pattern in `customer_list.html`. Confirm message notes that subcategories will become top-level. Edit and Delete only rendered for Manager/Admin (`{% if user.role and user.role.name in "Manager,Admin" %}`).

Empty state shows "No categories found" with "Add your first product category" link (create) or "Clear search" link (filtered state).

Pagination: identical block to `customer_list.html`, passing `q={{ query }}` in querystring when a search is active.

### New Template: `category_form.html`

Single template shared for create and edit. `category` present in context → edit mode.

Three fields:
1. **Name** (required, `maxlength="150"`, `required` attribute)
2. **Description** (optional textarea)
3. **Parent Category** (optional `<select>`; self excluded from options during edit via `{% if not category or cat.pk != category.pk %}`)

Parent dropdown uses `{{ cat }}` for option labels, which renders as "Parent › Child" for subcategories and plain "Name" for top-level — making nesting visible at a glance.

Form action, submit button label, and page subtitle all switch between create and edit mode via `{% if category %}` conditionals.

---

## Files Changed

| File | Type of change |
|------|---------------|
| `core/migrations/0005_remove_verification.py` | Created — removes VerificationRequest, removes KioskStation.verification_rate, adds Customer.date_of_birth and Customer.gender |
| `core/models.py` | Updated — removed VerificationRequest, removed verification_rate, added date_of_birth and gender to Customer |
| `core/admin.py` | Updated — removed VerificationRequest registration and verification_rate from KioskStationAdmin |
| `core/views.py` | Updated — added datetime import, Count/ProtectedError imports; added customer_create/customer_edit national_id/dob/gender handling; added category_create_ajax; added _is_descendant helper + category_list/category_create/category_edit/category_delete |
| `core/urls.py` | Updated — added category-create (AJAX), category-list, category-create-page, category-edit, category-delete URL patterns |
| `core/templates/core/base.html` | Updated — added "Categories" nav link |
| `core/templates/core/customer_list.html` | Updated — added ID Number column |
| `core/templates/core/customer_detail.html` | Updated — added national_id, date_of_birth, gender rows in Contact Details |
| `core/templates/core/customer_form.html` | Updated — phone placeholder, added national_id/date_of_birth/gender fields, added category inline-create modal |
| `core/templates/core/order_detail.html` | Updated — added national_id to read-only customer panel |
| `core/templates/core/product_form.html` | Updated — "+ New" category button, inline modal, AJAX JS block |
| `core/templates/core/category_list.html` | Created — full category list page |
| `core/templates/core/category_form.html` | Created — shared create/edit form |
| `core/management/commands/seed.py` | Updated — added datetime import; replaced 6 US CUSTOMERS with 10 Venezuelan records; updated _seed_customers to unpack new fields; updated _seed_orders to use new names + added 2 orders |
| `api/kiosk/views.py` | Updated — removed 4 verification views; checkout always delivers; added KioskProductSearchView and KioskProductDetailView |
| `api/kiosk/serializers.py` | Updated — KioskRegisterSerializer now requires email/dob/gender/state/city; removed VerificationListSerializer; added is_out_of_stock/is_low_stock to KioskProductSerializer; removed verification_required from KioskReceiptSerializer |
| `api/kiosk/urls.py` | Updated — removed 4 verification URL patterns; added products/ and products/<pk>/ |
| `kiosk/app/services/products.js` | Updated — searchProducts and getProduct now call /kiosk/products/ endpoints |
| `kiosk/app/services/customer.js` | Updated — createCustomer accepts and posts email, phone, dateOfBirth, gender, state, city |
| `kiosk/app/services/orders.js` | Updated — removed pollVerification; atomicCheckout no longer includes verification_required |
| `kiosk/app/store.js` | Updated — removed verification_required from PERSISTED_KEYS |
| `kiosk/app/components/RegistroScreen.js` | Rewritten — 8-field form with Venezuelan states/cities, sexo toggle buttons |
| `kiosk/app/components/ProcessingScreen.js` | Updated — removed all polling infrastructure; always navigates to success |
| `kiosk/app/components/SuccessScreen.js` | Updated — removed verificationRequired branch; always shows simple completion message |
| `kiosk/styles.css` | Updated — added .sexo-btns, .sexo-btn, .sexo-btn.selected styles |
