# CHANGELOG-02

## RetailOps — Session 2 Build Log

Continuation of the project build log. Session 1 (`CHANGELOG.md`) left the system with
a fully scaffolded backend — models, migrations, URL routing, templates, and stub views —
but with no working POST handlers, no data in the database, and no records displayed
anywhere in the UI.

This session completed four distinct phases of work.

---

## Phase 1 — CLAUDE.md Refresh

**File:** `CLAUDE.md`

The project guidance file was rewritten from scratch. The previous version was outdated:
it still described the project as "specification-only" with "no application code" and
contained none of the commands, architecture details, or stub status that a new Claude
Code session would need to be productive.

**Changes:**

| Section | Before | After |
|---------|--------|-------|
| Current state | "Specification-only. No application code exists yet." | Accurate description of what is scaffolded vs. still stubbed |
| Common commands | Absent | `runserver`, `migrate`, `makemigrations`, `shell`, admin URL and credentials |
| Architecture | Intended/future tense | Describes actual implemented code |
| Computed vs. stored fields | Not distinguished | Calls out that `Product.current_stock / is_low_stock / is_out_of_stock` are Python properties (not DB columns), and that `SalesOrderItem.line_total` is stored |
| Auth/authorization | Generic description | Exact import path and composition pattern for `@role_required` + `@require_POST` |
| Stub TODO table | Absent | Full table of every unimplemented POST handler |
| Seeded credentials | Absent | Quick-reference table |

---

## Phase 2 — User Guide

**File created:** `USER_GUIDE.md`

A full end-user guide written for non-technical staff, covering all normal workflows
in the order they would be encountered on the job.

**Contents:**

1. **Roles & Permissions** — Table showing exactly what Staff, Manager, and Admin can
   each do, so users understand which buttons they will and will not see.
2. **Login / Logout** — Step-by-step, including what to do if credentials fail.
3. **Dashboard** — Explains each stat card, the two tabs, and the quick-action sidebar.
4. **Customers** — Register, search, edit, delete (with the caveat about orders blocking deletion).
5. **Sales Order end-to-end** — Six numbered steps walking through every status from
   Draft → Delivered, with "Who can do this" noted at each stage.
6. **Payments** — How partial payments work, what happens when the balance is covered,
   why payments cannot be deleted.
7. **Cancellations** — When cancellation is possible, the stock-restoration side-effect
   explained in plain language.
8. **Refunds** — Admin-only; the distinction between the system record and the actual
   money transfer back to the customer.
9. **Inventory** — Reading stock colours, the movement history slide panel, adding and
   editing products, and the price-snapshot note (price changes don't affect existing orders).
10. **User Management** — Invite, edit, deactivate/reactivate, the "cannot deactivate
    your own account" rule.
11. **Quick Reference table** — All eight order statuses and their next action in one place.

---

## Phase 3 — POST Handler Implementation

**File:** `core/views.py` (grew from ~430 to ~1165 lines)

All twelve stub `pass  # TODO` POST bodies were implemented. Two private helper
functions were also added to reduce duplication across the order views.

### New helpers

**`_parse_line_items(post_data)`**

Extracts line-item tuples `(product_id, quantity, unit_price)` from the dynamic
form fields that the order template's JavaScript generates (`product_N`, `quantity_N`,
`unit_price_N`). Iterates 1 → `line_item_count`, skipping gaps left by removed rows
(the JS counter only increments, never resets after a row is deleted).

**`_save_order_items(order, raw_items)`**

Atomically replaces all `SalesOrderItem` rows on an order, then recalculates and saves
`subtotal` and `total_amount` on the order itself. Used by both `order_create` and
`order_detail` POST so totals are always kept consistent.

**`_order_form_context(order=None)`**

Builds the context dict needed by every render of `order_detail.html`, including
`customers`, `products` (active only), and `payment_method_choices`. Fixes a secondary
bug where the product dropdown in draft/pending orders and the payment modal both
rendered empty because neither was passed by the original GET handlers.

### Implemented views

#### `customer_create` POST / `customer_edit` POST

- Validates all required fields: first name, last name, email, address line 1, city,
  state, postal code, country.
- Email uniqueness check (on edit, the current customer's own email is excluded from
  the uniqueness query via `.exclude(pk=customer.pk)`).
- On failure: re-renders the form with `errors` and `form_data` dicts so field-level
  error messages appear and typed values are preserved.
- On success: creates/updates the `Customer` record and redirects to its detail page.

#### `order_create` POST

- Parses customer selection and all line items.
- Validates: customer must be selected and exist; at least one line item must be present.
- Creates `SalesOrder` at `status=DRAFT`, then calls `_save_order_items()` to create
  `SalesOrderItem` rows and compute `subtotal` / `total_amount`.
- The whole operation runs inside `transaction.atomic()` so a line-item error cannot
  leave a headerless order in the database.

#### `order_detail` POST

- Rejects edits if the order is no longer in Draft or Pending.
- Allows customer re-selection while in Draft only.
- Updates `discount_amount` and `notes`, then replaces all line items via
  `_save_order_items()` inside a transaction.

#### `order_confirm` (Pending → Confirmed)

- Sets `status`, `confirmed_by`, and `confirmed_at`.
- Creates one negative `InventoryMovement` (type `sale`, reference `SalesOrder`) per
  line item to deduct reserved stock.
- Entire operation wrapped in `transaction.atomic()`.

#### `order_cancel` (Confirmed → Cancelled)

- Sets `status = CANCELLED`.
- Creates one positive `InventoryMovement` (type `return`) per line item to restore
  the stock that was deducted on confirmation.
- Entire operation wrapped in `transaction.atomic()`.

#### `order_refund` (Paid → Refunded)

- Sets `status = REFUNDED`.
- Creates one positive `InventoryMovement` (type `return`) per line item to add stock
  back, identical in structure to the cancel restoration.
- Entire operation wrapped in `transaction.atomic()`.

#### `product_create` POST / `product_edit` POST

- Validates: SKU required and unique (on edit, excluding the current product); name
  required; category must be selected; unit of measure must be a valid choice; unit
  price must be greater than zero; low-stock threshold must be a non-negative integer.
- The `is_active` checkbox is handled explicitly (`request.POST.get('is_active') == '1'`)
  because unchecked checkboxes are absent from POST data entirely.
- On failure: re-renders the form with `errors` and `form_data`, passing `product`
  back so the template's fallback expressions (`|default:product.field`) still work.

#### `payment_create` POST

- Validates: amount > 0; payment method must be a valid choice value.
- Creates a `Payment` record linked to the order.
- After creation, re-aggregates all payments on the order using `Sum`. If the total
  paid now equals or exceeds `order.total_amount`, transitions the order to `PAID` and
  sets `paid_at = timezone.now()`.
- Produces a distinct success message depending on whether the order is now fully paid
  or a balance remains outstanding.

#### `user_invite` POST

- Validates: all fields required; email uniqueness checked against `User` table.
- Calls `User.objects.create_user()` (not `create()`) so the password is hashed
  through Django's password hashing pipeline rather than stored in plain text.

#### `user_edit` POST

Two forms on the same URL are dispatched by a hidden `action` field:

- **Default (no action):** Updates `first_name`, `last_name`, `email`, `role`, and
  `is_active`. Prevents an admin from deactivating their own account via this form
  (mirrors the existing `/deactivate/` endpoint guard).
- **`action=change_password`:** Validates that `new_password` is at least 8 characters
  and matches `confirm_password`, then calls `user.set_password()`.

### New imports added to `views.py`

```python
from decimal import Decimal, InvalidOperation   # price/amount parsing
from django.core.paginator import Paginator     # list view pagination
from django.db import transaction               # atomic order/inventory ops
from django.db.models import Q, Sum             # search filters, payment aggregation
```

---

## Phase 4 — Database Seed Command

**Files created:**
```
core/management/__init__.py
core/management/commands/__init__.py
core/management/commands/seed.py   (~389 lines)
```

A Django management command that populates the database with a realistic sample
dataset. Run with:

```bash
python manage.py seed           # seed (skips if data already exists)
python manage.py seed --force   # clear all existing data first, then re-seed
```

**Data seeded:**

| Entity | Count | Notes |
|--------|------:|-------|
| `ProductCategory` | 5 | Electronics, Accessories (child), Apparel, Footwear (child), Office Supplies |
| `Product` | 8 | Wireless Mouse, USB-C Hub, Mechanical Keyboard, Laptop Stand, Running Shoes, Cotton T-Shirt, Ballpoint Pens, A4 Notebook |
| `Customer` | 6 | Alice Johnson, Bob Martinez, Carol White, David Kim, Emma Davis, Frank Wilson |
| `SalesOrder` | 8 | One of every status: Draft, Pending, Confirmed (×2, one with partial payment), Paid, Shipped, Delivered, Cancelled |
| `Payment` | 4 | Bank Transfer, Card, Cash (×2); one order has a partial deposit |
| `InventoryMovement` | 19 | 8 opening-stock purchases + 10 sale deductions (from confirmed/paid/shipped/delivered orders) + 1 return restoration (from the cancelled order) |

All stock levels are internally consistent: the `current_stock` property on every
product correctly reflects the net of its purchase and sale movements.

**Guard logic:** The command checks `SalesOrder.objects.exists()` and
`Customer.objects.exists()` before doing any work and prints a warning if data is
already present, preventing accidental double-seeding. `--force` bypasses this guard
by deleting all moveable data first (in FK-safe order: movements → payments →
items → orders → customers → products → categories).

---

## Phase 5 — List View Data Fix

**File:** `core/views.py`

**Root cause:** Six GET handlers all returned `render(request, 'template.html', {})` —
an empty context dict. The templates expected named variables (`page_obj`, `query`,
`stats`, `products`, `orders`, etc.) to drive their tables, filters, pagination, and
stat cards. Since none of these were ever provided, every list page rendered as an
empty state even with data in the database.

### `dashboard`

Computes four stat card values fresh on each request:

- `stats.orders_this_month` — `SalesOrder` count with `created_at >= first day of month`
- `stats.revenue_this_month` — `Sum('total_amount')` of Paid / Shipped / Delivered orders
  with `paid_at >= first day of month`
- `stats.pending_payments_count` — count of orders at `status=CONFIRMED` (i.e., awaiting payment)
- `stats.low_stock_count` — computed in Python from a prefetched product list (cannot
  use a DB filter because `current_stock` is a Python property, not a stored column)

Also passes `recent_orders` (last 5 by `created_at`) and `low_stock_products` (list
of products where `is_low_stock or is_out_of_stock`).

### `customer_list`

- Searches `first_name`, `last_name`, `email` with a case-insensitive `Q` OR filter.
- Paginates at 25 per page using Django's `Paginator`.
- Passes `page_obj` and `query` back to template for filter persistence.

### `customer_detail`

- Was already passing `customer`; now also passes `orders` (the customer's related
  order set, ordered newest-first) so the order history table renders.

### `order_list`

- Filters by free-text search (`order_number`, `customer__first_name`,
  `customer__last_name`), status dropdown, and from/to date range.
- Paginates at 20 per page.
- Passes `status_choices = SalesOrder.STATUS_CHOICES` so the status dropdown in the
  filter bar populates from the model rather than being hardcoded in the template.

### `payment_list`

- Filters by free-text search (`payment_number`, `sales_order__order_number`),
  payment method dropdown, and from/to date range.
- Paginates at 20 per page.
- Uses `select_related('sales_order', 'sales_order__customer', 'recorded_by')` to
  avoid N+1 queries when rendering the customer name and recorder columns.

### `inventory_list`

- Filters by SKU/name search and category dropdown at the queryset level.
- Stock-status filter (`ok` / `low` / `out`) applied in Python after evaluating the
  `current_stock` property on each product — this is required because the property
  aggregates `InventoryMovement` records and cannot be translated into a `WHERE` clause.
- `low_stock_count` (used by the warning banner) is always computed from the full,
  unfiltered product set so the banner reflects reality even when filters are active.

---

## File Inventory (end of Session 2)

```
.
├── CLAUDE.md                               ← Updated project guidance
├── CHANGELOG.md                            ← Session 1 build log
├── CHANGELOG-02.md                         ← This file
├── USER_GUIDE.md                           ← End-user guide (new)
├── RETAIL_OPS_DESIGN_PROMPT.md             ← Original specification (unchanged)
├── erd.md                                  ← Mermaid ERD (unchanged)
├── wireframes.html                         ← Interactive wireframes (unchanged)
├── workflows.md                            ← Mermaid workflow diagrams (unchanged)
├── manage.py
├── requirements.txt
├── db.sqlite3                              ← Seeded with full sample dataset
├── retailops/
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
└── core/
    ├── apps.py
    ├── admin.py
    ├── decorators.py
    ├── models.py
    ├── urls.py
    ├── views.py                            ← All 29 views fully implemented (~1165 lines)
    ├── migrations/
    │   └── 0001_initial.py
    ├── management/
    │   └── commands/
    │       └── seed.py                     ← Database seed command (new, ~389 lines)
    └── templates/core/
        ├── base.html
        ├── login.html
        ├── dashboard.html
        ├── customer_list.html
        ├── customer_form.html
        ├── customer_detail.html
        ├── order_list.html
        ├── order_detail.html
        ├── payment_list.html
        ├── payment_detail.html
        ├── inventory_list.html
        ├── product_form.html
        ├── user_list.html
        └── user_form.html
```

---

## What Remains

The application is now fully functional end-to-end for its core workflows. Known
gaps that would need to be addressed before a production deployment:

| Area | Gap |
|------|-----|
| Authentication | No "forgot password" or self-service password reset flow |
| Authorisation | `order_detail` POST and `order_submit` have no role restriction — any logged-in user can edit and submit orders; tighten if needed |
| Validation | No server-side guard preventing confirmation of an order with zero line items (possible if all items were deleted while in Pending) |
| Inventory | No UI for recording stock purchases / manual adjustments — movements only come from order confirmations; initial stock must be entered via the Django admin or the seed command |
| Dashboard | Stats cards (revenue, pending payments) do not update in real time; a page refresh is required |
| Security | `SECRET_KEY` in `settings.py` is the insecure placeholder; `DEBUG = True`; `ALLOWED_HOSTS = []` — all must be changed before any deployment |
