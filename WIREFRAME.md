# RetailOps — Functional View Wireframe

> Exhaustive inspection of the project's Django view templates, URL routes, view logic, and inter-view interactions.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              TEMPLATE HIERARCHY                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  STANDALONE (no base.html)          │  EXTEND base.html                      │
│  ───────────────────────────────    │  ─────────────────────────────────────  │
│  • login.html                       │  • dashboard.html                      │
│  • password_reset_form.html         │  • user_list.html  +  user_form.html   │
│  • password_reset_done.html         │  • customer_list.html                  │
│  • password_reset_confirm.html      │  • customer_form.html                  │
│  • password_reset_complete.html     │  • customer_detail.html                │
│                                     │  • order_list.html                     │
│                                     │  • order_detail.html                   │
│                                     │  • payment_list.html                   │
│                                     │  • payment_detail.html                 │
│                                     │  • inventory_list.html                 │
│                                     │  • product_form.html                   │
│                                     │  • category_list.html                  │
│                                     │  • category_form.html                  │
│                                     │  • settings.html                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Base Layout (`core/base.html`)

### 2.1 Global Shell
```
┌─────────────────────────────────────────────────────────────────────────────┐
│  [■RetailOps]  Dashboard  Orders  Customers  Inventory  Categories          │
│                Payments   [Users*]  Settings      [FX]  [AB] Admin ▼ [Logout]│
│                                      *Admin-only                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                         {% block content %}                                 │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  Toast container (fixed top-right)                                          │
│  Modal container ({% block modals %})                                       │
│  Global JS: toast, modal, side-panel                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Navbar Behavior
| Element | Visibility | Interaction |
|---------|-----------|-------------|
| Logo → Dashboard | Always | Link to `/` |
| Dashboard | Always | Active highlight on `url_name == 'dashboard'` |
| Orders | Always | Active on `'order' in url_name` |
| Customers | Always | Active on `'customer' in url_name` |
| Inventory | Always | Active on `'inventory' in url_name` or `'product' in url_name` |
| Categories | Always | Active on `'category' in url_name` |
| Payments | Always | Active on `'payment' in url_name` |
| Users | **Admin only** | Active on `'user' in url_name` |
| Settings | Always | Active on `url_name == 'settings'` |
| FX Badge | If `secondary_currency_enabled` | Static display of exchange rate |
| Language Selector | Always | POST to `/i18n/setlang/` — auto-submit on change |
| User Badge | Always | Avatar + full name + role tag |
| Logout | Always | POST form to `/logout/` |

### 2.3 Global UI Primitives (CSS classes)
```
• Cards:          .card > .card-header + .card-body
• Stat Cards:     .stat-grid > .stat-card.{primary|success|warning|danger}
• Tables:         .table-wrap > table (hover rows, striped)
• Buttons:        .btn-{primary|secondary|success|danger|warning|ghost}
• Badges:         .badge-{draft|pending|confirmed|paid|shipped|delivered|cancelled|refunded|admin|manager|staff|active|inactive}
• Forms:          .form-grid-{2|3} > .form-group > .form-control
• Filter Bar:     .filter-bar (search + selects + buttons)
• Pagination:     .pagination > .pg-btn
• Empty State:    .empty-state
• Alerts:         .alert-banner.{warning|danger|success|info}
• Modal:          .modal-overlay > .modal > .modal-header + .modal-body + .modal-footer
• Side Panel:     .side-panel (slides from right, 420px)
```

---

## 3. Authentication Flow (Unauthenticated Zone)

### 3.1 Login (`/login/` → `login_view`)
```
┌─────────────────────────────────────────┐
│           ■RetailOps                    │
│      Unified Retail & Order Mgmt        │
│                                         │
│  ┌─────────────────────────────────┐    │
│  │  Email Address                  │    │
│  │  [you@company.com           ]   │    │
│  │                                 │    │
│  │  Password                       │    │
│  │  [••••••••                  ]   │    │
│  │                                 │    │
│  │  [☐] Remember me              │    │
│  │                                 │    │
│  │  [      Sign In               ] │    │
│  └─────────────────────────────────┘    │
│         Forgot password?                │
│              [Language ▼]               │
└─────────────────────────────────────────┘
```
**Interactions:**
- POST email+password → success: redirect to `?next` or Dashboard
- "Forgot password?" → `/password-reset/`

### 3.2 Password Reset Chain
```
/login/
   │
   ▼ (click "Forgot password?")
/password-reset/ ─────────────────────┐
   │                                   │
   ▼ POST email                       │
/password-reset/done/                 │
   │                                   │
   ▼ (follow email link)               │
/password-reset/confirm/<uidb64>/<token>/
   │                                   │
   ▼ POST new password                │
/password-reset/complete/             │
   │                                   │
   ▼ (click "Sign In")                │
/login/ ◄─────────────────────────────┘
```

| Step | Template | View | Method | Next |
|------|----------|------|--------|------|
| Request | `password_reset_form.html` | `PasswordResetView` | GET/POST | `/password-reset/done/` |
| Done | `password_reset_done.html` | `PasswordResetDoneView` | GET | — |
| Confirm | `password_reset_confirm.html` | `PasswordResetConfirmView` | GET/POST | `/password-reset/complete/` |
| Complete | `password_reset_complete.html` | `PasswordResetCompleteView` | GET | → Login |

---

## 4. Dashboard (`/` → `dashboard`)

### 4.1 Layout
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Dashboard                                                    Welcome, {name}│
├─────────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐            │
│ │Orders Month │ │Revenue Month│ │Pending Pmts │ │Low Stock    │            │
│ │    42       │ │  $12,450.00 │ │     7       │ │     3       │            │
│ │Sales orders │ │From paid    │ │Awaiting pay │ │Below thresh │            │
│ └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘            │
├────────────────────────────────────┬────────────────────────────────────────┤
│ ┌────────────────────────────────┐ │  ┌────────────────────────────────┐   │
│ │ [Recent Orders] [Inv Alerts ▼] │ │  │        Quick Actions           │   │
│ │                                │ │  │  + New Order                   │   │
│ │  Order#  Customer  Date  ...   │ │  │  👤 Register Customer          │   │
│ │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │ │  │  💳 View Payments              │   │
│ │  ORD-001  John D.  Jun 1  ...  │ │  │  📦 Inventory                  │   │
│ │  ...                           │ │  └────────────────────────────────┘   │
│ │                                │ │                                       │
│ │  View all →                    │ │                                       │
│ └────────────────────────────────┘ │                                       │
└────────────────────────────────────┴────────────────────────────────────────┘
```

### 4.2 Tab Switcher (JS: `dashTab()`)
- **Recent Orders** (default): Table of last 5 orders → links to `order-detail`
- **Inventory Alerts**: Low-stock/out-of-stock product cards → links to `inventory-list`

### 4.3 Outbound Links
| Source | Target URL | Purpose |
|--------|-----------|---------|
| "View all →" | `order-list` | Full order history |
| "No orders yet" | `order-create` | Create first order |
| Quick Action "+ New Order" | `order-create` | New sales order |
| Quick Action "👤 Register Customer" | `customer-create` | New customer |
| Quick Action "💳 View Payments" | `payment-list` | All payments |
| Quick Action "📦 Inventory" | `inventory-list` | Stock levels |

---

## 5. Customers Module

### 5.1 Customer List (`/customers/` → `customer_list`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Customers                                       [+ Register Customer]       │
├─────────────────────────────────────────────────────────────────────────────┤
│ [🔍 Search name, email, ID or phone…] [Search] [Clear] [Filtered by "…"]   │
├─────────────────────────────────────────────────────────────────────────────┤
│ Name    │ ID#      │ Email     │ Phone │ City │ Country │ Registered │ ⋮   │
│━━━━━━━━━│━━━━━━━━━━│━━━━━━━━━━━│━━━━━━━│━━━━━━│━━━━━━━━━│━━━━━━━━━━━━│━━━━━│
│ John D. │ V12345678│john@em… │ —     │Caracas│ Venezuela│ Jun 1, 2026│View│
│         │          │           │       │      │         │            │Edit│
│         │          │           │       │      │         │            │Del │
├─────────────────────────────────────────────────────────────────────────────┤
│ Showing 1–25 of 142                    ‹ 1 2 3 4 5 ›                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Actions per row:**
- **View** → `customer-detail`
- **Edit** → `customer-edit`
- **Delete** → POST `customer-delete` (confirm dialog)

### 5.2 Customer Create/Edit (`/customers/new/` → `customer_create`, `/customers/<pk>/edit/` → `customer_edit`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Register Customer                                    ← Back to Customers    │
├─────────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ PERSONAL INFORMATION                                                    │ │
│ │  First Name*    [                    ]  Last Name*  [                    ]│ │
│ │  Email*         [                    ]  Phone       [                    ]│ │
│ │  ID Number      [                    ]  Date of Birth [📅              ] │ │
│ │  Gender         [— Not specified — ▼]                                   │ │
│ │ ADDRESS                                                                 │ │
│ │  Address Line 1*[                                               ]       │ │
│ │  Address Line 2 [                                               ]       │ │
│ │  City*          [      ]  State* [      ]  Postal Code* [        ]      │ │
│ │  Country*       [United States                                ]         │ │
│ │ NOTES                                                                   │ │
│ │  Internal Notes [                                               ]       │ │
│ │                                                                         │ │
│ │  [ Register Customer ]  [ Cancel ]                                      │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```
- **Validation:** Required fields, email uniqueness, national_id uniqueness, gender ∈ {M,F}, valid DOB
- **Success:** Redirect to `customer-detail`

### 5.3 Customer Detail (`/customers/<pk>/` → `customer_detail`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ John Doe                                [Edit]  [+ New Order]               │
│ Customer since Jun 1, 2026                                                  │
├───────────────────────────────┬─────────────────────────────────────────────┤
│ │ Contact Details            │ │ Address                                  │ │
│ │ Email   john@example.com   │ │ Line 1  123 Main St                      │ │
│ │ Phone   —                  │ │ City    Caracas, Dtto. Capital 1010      │ │
│ │ ID#     V12345678          │ │ Country Venezuela                        │ │
│ │ DOB     Jun 15, 1985       │ │                                          │ │
│ │ Gender  Male               │ │                                          │ │
│ └────────────────────────────┘ └──────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│ Order History                                          [+ New Order]        │
│ Order#    │ Date       │ Status   │ Items │ Total    │ Actions              │
│━━━━━━━━━━━│━━━━━━━━━━━━│━━━━━━━━━━│━━━━━━━│━━━━━━━━━━│━━━━━━━━━━━━━━━━━━━━━━│
│ ORD-001   │ Jun 5, 2026│ Confirmed│ 3     │ $450.00  │ [View]               │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Outbound links:**
- Edit → `customer-edit`
- New Order → `order-create?customer=<pk>`
- Order# → `order-detail`
- View → `order-detail`

---

## 6. Sales Orders Module

### 6.1 Order List (`/orders/` → `order_list`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Sales Orders                                           [+ New Order]        │
├─────────────────────────────────────────────────────────────────────────────┤
│ [🔍 Search…] [All Statuses ▼] [From 📅] [To 📅] [Filter] [Clear] [Filters] │
├─────────────────────────────────────────────────────────────────────────────┤
│ Order#   │ Customer │ Date       │ Status   │ Items │ Total     │ Actions   │
│━━━━━━━━━━│━━━━━━━━━━│━━━━━━━━━━━━│━━━━━━━━━━│━━━━━━━│━━━━━━━━━━━│━━━━━━━━━━━│
│ ORD-001  │ John D.  │ Jun 5, 2026│ Confirmed│ 3     │ $450.00   │View│Edit│ │
│ ORD-002  │ Jane S.  │ Jun 4, 2026│ Draft    │ 2     │ $120.00   │View│Edit│Del│
└─────────────────────────────────────────────────────────────────────────────┘
```

**Actions by status:**
| Status | View | Edit | Delete |
|--------|------|------|--------|
| Draft | ✓ | ✓ | ✓ (POST `order-delete`) |
| Pending | ✓ | ✓ | ✗ |
| Confirmed+ | ✓ | ✗ | ✗ |

### 6.2 Order Detail / Create (`/orders/new/` → `order_create`, `/orders/<pk>/` → `order_detail`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ORD-001                              [Save Draft] [Submit for Review]       │
│ [Confirmed] Created by Admin · Jun 5, 2026  · Confirmed by Manager Jun 5   │
├────────────────────────────────────┬────────────────────────────────────────┤
│ │ Customer                        │ │ Payments                            │ │
│ │ Name   John Doe                 │ │ PAY-001    $200.00                 │ │
│ │ Email  john@example.com         │ │ Cash · Jun 5 · Admin               │ │
│ │ Phone  —                        │ │                                    │ │
│ │ ID#    V12345678                │ │ Outstanding: $250.00 (red)         │ │
│ │ Address 123 Main St, …          │ │ [+ Record]                         │ │
│ └─────────────────────────────────┘ └────────────────────────────────────┘ │
├────────────────────────────────────┬────────────────────────────────────────┤
│ │ Line Items                    [+ Add Item]                                │
│ │ Product │ SKU │ Qty │ Unit Price │ Line Total │ ⋮                        │
│ │ [Prod ▼]│ SKU │ [1] │ [$150.00]  │ $150.00    │ ✕                        │
│ │ Subtotal                              $450.00                             │
│ │ Tax                                   $0.00                               │
│ │ Discount [$0.00]                      $0.00                               │
│ │ Grand Total                           $450.00                             │
│ └───────────────────────────────────────────────────────────────────────────┘
│ │ Order Notes                                                               │
│ │ [Internal notes…]                                                         │
│ └───────────────────────────────────────────────────────────────────────────┘
│ │ Order Info                                                                │
│ │ Order#  ORD-001  Status [Confirmed]  Created Jun 5, 2026                  │
│ └───────────────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────────┘
```

**Role-gated action buttons (top-right):**
| Current Status | Role | Button | POST Target |
|---------------|------|--------|-------------|
| Draft / Pending | Any | Save Draft | `order-detail` (POST update) |
| Draft | Staff/Manager/Admin | Submit for Review | `order-submit` |
| Pending | Manager/Admin | Confirm Order | `order-confirm` |
| Confirmed | Any | Record Payment | Opens payment modal |
| Paid | Staff/Manager/Admin | Mark Shipped | `order-ship` |
| Shipped | Staff/Manager/Admin | Mark Delivered | `order-deliver` |
| Confirmed | Manager/Admin | Cancel Order | `order-cancel` |
| Paid | Admin | Issue Refund | `order-refund` |

**JS Interactions:**
- `addLineItem()` — Appends new row to line-items table from `PRODUCTS` array
- `removeLineItem()` — Deletes row (minimum 1 item enforced)
- `updateSku()` — On product change, updates SKU, thumb, and default price
- `recalcRow()` / `recalcTotals()` — Live subtotal/grand-total calculation
- `toggleRefField()` — Shows/hides reference number field based on payment method
- Currency formatting with secondary currency support

### 6.3 Order Status State Machine
```
                    ┌─────────────┐
         ┌─────────►│   Draft     │◄────────┐
         │          └──────┬──────┘         │
         │ delete           │ submit         │ create
         │                  ▼                │
         │          ┌─────────────┐          │
         │    ┌─────┤   Pending   │          │
         │    │     └──────┬──────┘          │
         │    │cancel      │ confirm          │
         │    │            ▼                 │
         │    │     ┌─────────────┐          │
         │    └───►│  Confirmed  │◄───────────┘
         │          └──────┬──────┘
         │                 │ record payment
         │                 ▼
         │          ┌─────────────┐
         │          │    Paid     │
         │          └──────┬──────┘
         │                 │ ship
         │    ┌────────────┼────────────┐
         │    │            ▼            │ refund
         │    │     ┌─────────────┐     │
         │    │     │   Shipped   │     │
         │    │     └──────┬──────┘     │
         │    │            │ deliver     │
         │    │            ▼             │
         │    │     ┌─────────────┐      │
         │    │     │  Delivered  │      │
         │    │     └─────────────┘      │
         │    │                          │
         │    └──────────────────────────┘
         │
         └──────────────────────────────────► (deleted)
```

---

## 7. Payments Module

### 7.1 Payment List (`/payments/` → `payment_list`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Payments                                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ [🔍 Search…] [All Methods ▼] [All Statuses ▼] [From] [To] [Filter] [Clear] │
├─────────────────────────────────────────────────────────────────────────────┤
│ Pay# │ Order# │ Customer │ Amount │ Method │ Status │ Ref │ Rec │ Date │ ⋮ │
├─────────────────────────────────────────────────────────────────────────────┤
│ PAY-001│ORD-001│John D.  │$200.00 │ Cash   │Paid    │—   │Admin│Jun 5 │View│
└─────────────────────────────────────────────────────────────────────────────┘
```

**Outbound links:**
- Payment# → `payment-detail`
- Order# → `order-detail`
- Receipt (if image exists) → `payment.receipt_image.url`

### 7.2 Payment Detail (`/payments/<pk>/` → `payment_detail`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PAY-001                                    ← Back to Order                  │
│ Recorded on Jun 5, 2026 by Admin                                            │
├─────────────────────────────┬───────────────────────────────────────────────┤
│ │ Payment Details          │ │ Linked Order                               │ │
│ │ Pay#    PAY-001          │ │ Order#  ORD-001     [View Order →]         │ │
│ │ Amount  $200.00          │ │ Status  Confirmed                          │ │
│ │ Method  Cash             │ │ Customer John Doe                          │ │
│ │ Date    Jun 5, 2026      │ │ Total   $450.00                            │ │
│ │ By      Admin            │ │ Paid    $200.00                            │ │
│ └──────────────────────────┘ │ Outstanding  $250.00 (red)                 │ │
│                              └──────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────────┘
```

### 7.3 Payment Recording Modal (inside `order_detail.html`)
```
┌─────────────────────────────────────────┐
│ Record Payment                    [✕]   │
├─────────────────────────────────────────┤
│ Amount*         [$                ]     │
│ Outstanding: $250.00                    │
│                                         │
│ Payment Method*                         │
│ ○ Cash  ○ Bank Transfer  ○ Card  ○ Check│
│                                         │
│ Reference Number [                ]     │
│ (Required for bank/card/check)          │
│                                         │
│ Notes                                   │
│ [                               ]       │
├─────────────────────────────────────────┤
│              [Cancel] [Record Payment]  │
└─────────────────────────────────────────┘
```
- POST to `payment-create` with `sales_order=<pk>`
- Auto-transitions order to **Paid** if total payments ≥ order total

---

## 8. Inventory Module

### 8.1 Inventory List (`/inventory/` → `inventory_list`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Inventory                              [Adjust Stock] [+ Add Product]       │
├─────────────────────────────────────────────────────────────────────────────┤
│ ⚠ 3 products are below the low-stock threshold — restock soon.        [✕]  │
├─────────────────────────────────────────────────────────────────────────────┤
│ [🔍 Search SKU/name…] [All Categories ▼] [All Stock Status ▼] [Filter]     │
├─────────────────────────────────────────────────────────────────────────────┤
│ SKU │ Product │ Category │ Unit │ Price │ Stock │ Actions                │
├─────┼─────────┼──────────┼──────┼───────┼───────┼────────────────────────┤
│SKU-1│ Widget  │Electronics│ pcs │$99.00 │ 5(low)│Edit│Adjust│Movements  │
│SKU-2│ Gizmo   │Tools    │ box  │$45.00 │ 0(out)│Edit│Adjust│Movements  │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Row actions:**
- **Edit** → `product-edit` (Manager/Admin only)
- **Adjust** → Opens Adjust Stock Modal pre-filled with product
- **Movements** → Opens Side Panel with AJAX-loaded movement history

### 8.2 Movement History Side Panel
```
┌─────────────────────────────────────┐
│ SKU-1 — Widget                [✕]   │
├─────────────────────────────────────┤
│ Current stock: 5 (warning color)    │
│                                     │
│ Sale · SalesOrder #42               │
│ Jun 5, 2026 · Admin                 │
│ Stock deducted on confirmation…     │
│ ─────────────────────────────────   │
│ Purchase · ManualAdjustment #0      │
│ Jun 1, 2026 · Manager               │
│ +50                                 │
└─────────────────────────────────────┘
```
- Fetched via `fetch('/inventory/products/<pk>/movements/')` → JSON

### 8.3 Adjust Stock Modal
```
┌─────────────────────────────────────────┐
│ Adjust Stock                      [✕]   │
├─────────────────────────────────────────┤
│ Product    SKU-1 — Widget               │
│                                         │
│ Movement Type*                          │
│ [Purchase — add stock received ▼]       │
│                                         │
│ Quantity*       [      ]                │
│ (positive = add, negative = deduct)     │
│                                         │
│ Notes           [               ]       │
├─────────────────────────────────────────┤
│          [Cancel] [Record Adjustment]   │
└─────────────────────────────────────────┘
```
- POST to `inventory-adjust`
- Creates `InventoryMovement` with `reference_type=MANUAL_ADJUSTMENT`

### 8.4 Product Form (`/inventory/products/new/` → `product_create`, `/inventory/products/<pk>/edit/` → `product_edit`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Add Product                                    ← Back to Inventory          │
├─────────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ PRODUCT IDENTITY                                                        │ │
│ │  SKU*           [                    ]  Name*  [                    ]   │ │
│ │  Category*      [— Select — ▼] [+ New]  Unit*  [— Select — ▼]          │ │
│ │  Description    [                                               ]       │ │
│ │ PRODUCT IMAGE                                                           │ │
│ │  ┌────┐  Upload Image [        ]  External URL [              ]         │ │
│ │  │IMG │  [☐ Remove uploaded image]                                       │ │
│ │  └────┘  Active products require image.                                 │ │
│ │ PRICING & STOCK                                                         │ │
│ │  Unit Price ($)* [      ]  Low Stock Threshold* [      ]                │ │
│ │  [☐] Product is active                                                  │ │
│ │                                                                         │ │
│ │  [ Add Product ]  [ Cancel ]                                            │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Inline Category Creation (AJAX):**
- Click "+ New" → opens custom modal (not base.html modal system)
- POST to `/inventory/categories/create/` → JSON response
- On success: appends option to category dropdowns and auto-selects

---

## 9. Categories Module

### 9.1 Category List (`/inventory/categories/` → `category_list`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Product Categories (12)                            [+ New Category]         │
├─────────────────────────────────────────────────────────────────────────────┤
│ [🔍 Search…] [Search] [Clear] [Filtered by "…"]                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ Name │ Type │ Parent │ Products │ Created │ Actions                         │
├──────┼──────┼────────┼──────────┼─────────┼─────────────────────────────────┤
│Electr│Parent│—       │ 42       │Jan 2026 │Edit│Delete                      │
│Phones│Sub   │Electr  │ 8        │Feb 2026 │Edit│Delete                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Category Form (`/inventory/categories/new/` → `category_create`, `/inventory/categories/<pk>/edit/` → `category_edit`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ New Category                                          ← Back                │
├─────────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ CATEGORY DETAILS                                                        │ │
│ │  Name*          [                    ]                                  │ │
│ │  Description    [                                               ]       │ │
│ │  Parent Category [— None (top-level) — ▼]                               │ │
│ │                                                                         │ │
│ │  [ Create Category ]  [ Cancel ]                                        │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```
- Validates: name required, ≤150 chars, unique, no circular parent references
- ProtectedError handling: cannot delete category assigned to products

---

## 10. Users / Staff Management (Admin Only)

### 10.1 User List (`/users/` → `user_list`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Staff Management                                    [+ Invite New User]     │
├─────────────────────────────────────────────────────────────────────────────┤
│ Name │ Email │ Role │ Status │ Last Login │ Actions                           │
├──────┼───────┼──────┼────────┼────────────┼───────────────────────────────────┤
│ AB Admin│a@em│Admin │ Active │ Today      │Edit                               │
│ MN Mgr │m@em │Manager│Active │ Jun 1      │Edit│Deactivate                    │
│ ST Staff│s@em│Staff │Inactive│ Never      │Edit│Reactivate                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Invite User Modal:**
```
┌─────────────────────────────────────────┐
│ Invite New Staff Member           [✕]   │
├─────────────────────────────────────────┤
│ First Name*  [      ]  Last Name* [      ]│
│ Email*       [                  ]         │
│ Role*        [— Select — ▼]               │
│ Temporary Password* [            ]        │
├─────────────────────────────────────────┤
│          [Cancel] [Create User]           │
└─────────────────────────────────────────┘
```
- POST to `user-invite` → redirect to `user-list`

### 10.2 User Edit (`/users/<pk>/edit/` → `user_edit`)
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Edit User                                          ← Back to Staff          │
├─────────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────┐  ┌────────────────────────────────────────────┐ │
│ │ Account Details         │  │ Change Password                            │ │
│ │ First Name* [      ]    │  │ New Password* [                    ]       │ │
│ │ Last Name*  [      ]    │  │ Confirm*      [                    ]       │ │
│ │ Email*      [      ]    │  │                                            │ │
│ │ Role*       [— ▼]       │  │ [ Set Password ]                           │ │
│ │ ⚠ Changing role updates │  └────────────────────────────────────────────┘ │
│ │   permissions.          │                                                │ │
│ │ [☐] Account is active   │                                                │ │
│ │ (Cannot deactivate self)│                                                │ │
│ │                         │                                                │ │
│ │ [ Save Changes ] [Cancel]│                                               │ │
│ └─────────────────────────┘                                                │
└─────────────────────────────────────────────────────────────────────────────┘
```
- Two POST actions: default (profile update) vs `action=change_password`
- Self-deactivation blocked

---

## 11. Settings (`/settings/` → `user_settings`)

### 11.1 Layout
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Settings                                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ Regional Preferences                                                    │ │
│ │  Time Zone  [UTC ▼]          Language [English ▼]                       │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ Currency Settings (Admin only)                                          │ │
│ │  Code [USD]  Symbol [$]  Decimals [2 ▼]                                 │ │
│ │  Preview: $1,234.00                                                     │ │
│ │  ─────────────────────────────────────────────────────────────────────  │ │
│ │  [☐] Show secondary currency                                            │ │
│ │  2nd Code [VES] 2nd Sym [Bs.] 2nd Dec [2] Rate [36.50]                  │ │
│ │  [☐] Auto-update from external source                                   │ │
│ │  Source URL [https://…]  JSON Field [promedio]                          │ │
│ │  [Update now]  Last updated: Jun 5, 2026                                │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ Receipt OCR Settings (Admin only)                                       │ │
│ │  [☐] Enable receipt OCR                                                 │ │
│ │  Provider [VEPay ▼]  Base URL [https://…]                               │ │
│ │  API Key [***] [Replace key]                                            │ │
│ │  Timeout [30]  Max Size [8]  Retention [90]                             │ │
│ │  Payment Methods: [☐ Mobile] [☐ Bank]                                   │ │
│ │  [☐] Require amount match  [☐] Require complete validation              │ │
│ │  [☐] Require receipt image for kiosk                                    │ │
│ │  [Test connection] [Not tested]                                         │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  [ Save Settings ]                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Interactions:**
- "Update now" button uses `formaction="{% url 'secondary-rate-refresh' %}"`
- "Test connection" fetches `{% url 'api:payment-receipt-healthz' %}` via JS
- "Replace key" toggles hidden input visibility
- Language change sets cookie + updates `User.language`

---

## 12. Complete URL-to-Template-to-View Matrix

| URL Path | Name | View | Template | Methods | Role |
|----------|------|------|----------|---------|------|
| `/login/` | `login` | `login_view` | `login.html` | GET/POST | Any |
| `/logout/` | `logout` | `logout_view` | — | POST | Auth |
| `/i18n/setlang/` | `set-language` | `set_language` | — | POST | Any |
| `/password-reset/` | `password_reset` | `PasswordResetView` | `password_reset_form.html` | GET/POST | Any |
| `/password-reset/done/` | `password_reset_done` | `PasswordResetDoneView` | `password_reset_done.html` | GET | Any |
| `/password-reset/confirm/…/` | `password_reset_confirm` | `PasswordResetConfirmView` | `password_reset_confirm.html` | GET/POST | Any |
| `/password-reset/complete/` | `password_reset_complete` | `PasswordResetCompleteView` | `password_reset_complete.html` | GET | Any |
| `/` | `dashboard` | `dashboard` | `dashboard.html` | GET | Auth |
| `/customers/` | `customer-list` | `customer_list` | `customer_list.html` | GET | Auth |
| `/customers/new/` | `customer-create` | `customer_create` | `customer_form.html` | GET/POST | Auth |
| `/customers/<pk>/` | `customer-detail` | `customer_detail` | `customer_detail.html` | GET | Auth |
| `/customers/<pk>/edit/` | `customer-edit` | `customer_edit` | `customer_form.html` | GET/POST | Auth |
| `/customers/<pk>/delete/` | `customer-delete` | `customer_delete` | — | POST | Auth |
| `/orders/` | `order-list` | `order_list` | `order_list.html` | GET | Auth |
| `/orders/new/` | `order-create` | `order_create` | `order_detail.html` | GET/POST | Staff+ |
| `/orders/<pk>/` | `order-detail` | `order_detail` | `order_detail.html` | GET/POST | Staff+ |
| `/orders/<pk>/delete/` | `order-delete` | `order_delete` | — | POST | Staff+ |
| `/orders/<pk>/submit/` | `order-submit` | `order_submit` | — | POST | Staff+ |
| `/orders/<pk>/confirm/` | `order-confirm` | `order_confirm` | — | POST | Manager+ |
| `/orders/<pk>/ship/` | `order-ship` | `order_ship` | — | POST | Staff+ |
| `/orders/<pk>/deliver/` | `order-deliver` | `order_deliver` | — | POST | Staff+ |
| `/orders/<pk>/cancel/` | `order-cancel` | `order_cancel` | — | POST | Manager+ |
| `/orders/<pk>/refund/` | `order-refund` | `order_refund` | — | POST | Admin |
| `/payments/` | `payment-list` | `payment_list` | `payment_list.html` | GET | Auth |
| `/payments/new/` | `payment-create` | `payment_create` | — | POST | Auth |
| `/payments/<pk>/` | `payment-detail` | `payment_detail` | `payment_detail.html` | GET | Auth |
| `/inventory/` | `inventory-list` | `inventory_list` | `inventory_list.html` | GET | Auth |
| `/inventory/products/new/` | `product-create` | `product_create` | `product_form.html` | GET/POST | Manager+ |
| `/inventory/products/<pk>/edit/` | `product-edit` | `product_edit` | `product_form.html` | GET/POST | Manager+ |
| `/inventory/products/<pk>/movements/` | `product-movements` | `product_movements` | JSON | GET | Auth |
| `/inventory/adjust/` | `inventory-adjust` | `inventory_adjust` | — | POST | Manager+ |
| `/inventory/categories/create/` | `category-create` | `category_create_ajax` | JSON | POST | Manager+ |
| `/inventory/categories/` | `category-list` | `category_list` | `category_list.html` | GET | Auth |
| `/inventory/categories/new/` | `category-create-page` | `category_create` | `category_form.html` | GET/POST | Manager+ |
| `/inventory/categories/<pk>/edit/` | `category-edit` | `category_edit` | `category_form.html` | GET/POST | Manager+ |
| `/inventory/categories/<pk>/delete/` | `category-delete` | `category_delete` | — | POST | Manager+ |
| `/users/` | `user-list` | `user_list` | `user_list.html` | GET | Admin |
| `/users/invite/` | `user-invite` | `user_invite` | — | POST | Admin |
| `/users/<pk>/edit/` | `user-edit` | `user_edit` | `user_form.html` | GET/POST | Admin |
| `/users/<pk>/deactivate/` | `user-deactivate` | `user_deactivate` | — | POST | Admin |
| `/users/<pk>/reactivate/` | `user-reactivate` | `user_reactivate` | — | POST | Admin |
| `/settings/` | `settings` | `user_settings` | `settings.html` | GET/POST | Auth |
| `/settings/secondary-rate/refresh/` | `secondary-rate-refresh` | `secondary_rate_refresh` | — | POST | Admin |

---

## 13. View Interaction Graph

```
                              ┌─────────────┐
                              │   LOGIN     │
                              │  /login/    │
                              └──────┬──────┘
                                     │ authenticate
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            AUTHENTICATED ZONE                                │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐          │
│  │DASHBOARD│  │ ORDERS  │  │CUSTOMERS│  │INVENTORY│  │PAYMENTS │          │
│  │   /     │  │/orders/ │  │/customers│  │/inventory│  │/payments│         │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘          │
│       │            │            │            │            │                 │
│       └────────────┴────────────┴────────────┴────────────┘                 │
│                              │                                              │
│       ┌──────────────────────┼──────────────────────┐                       │
│       ▼                      ▼                      ▼                       │
│  ┌─────────┐           ┌──────────┐          ┌──────────┐                   │
│  │SETTINGS │           │  USERS   │          │CATEGORIES│                   │
│  │/settings│           │ /users/  │          │/inventory│                   │
│  └─────────┘           └──────────┘          │/categories│                  │
│                                              └──────────┘                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 13.1 Cross-View Navigation Links

```
From DASHBOARD:
  → order-list      ("View all →")
  → order-create    (Quick Action "+ New Order")
  → customer-create (Quick Action "👤 Register Customer")
  → payment-list    (Quick Action "💳 View Payments")
  → inventory-list  (Quick Action "📦 Inventory")

From ORDER-LIST:
  → order-detail    (Order# link, View button)
  → order-create    ("+ New Order")
  → customer-detail (Customer name link)

From ORDER-DETAIL:
  → customer-detail (Customer name in info panel)
  → payment-create  ("Record Payment" modal → POST)
  → payment-detail  (Payment# in payment history)
  → order-list      (after delete)

From CUSTOMER-LIST:
  → customer-detail (Name link, View button)
  → customer-create ("+ Register Customer")
  → customer-edit   (Edit button)

From CUSTOMER-DETAIL:
  → customer-edit   ("Edit")
  → order-create?customer=<pk> ("+ New Order")
  → order-detail    (Order# in history)

From PAYMENT-LIST:
  → payment-detail  (Payment# link)
  → order-detail    (Order# link)

From PAYMENT-DETAIL:
  → order-detail    ("← Back to Order", "View Order →")
  → customer-detail (Customer name link)

From INVENTORY-LIST:
  → product-create  ("+ Add Product")
  → product-edit    ("Edit")
  → product-movements (AJAX, side panel)

From PRODUCT-FORM:
  → inventory-list  ("← Back to Inventory", Cancel)
  → category-create (AJAX modal)

From CATEGORY-LIST:
  → category-create-page ("+ New Category")
  → category-edit   (Name link, Edit button)

From CATEGORY-FORM:
  → category-list   ("← Back", Cancel)

From USER-LIST:
  → user-edit       ("Edit")

From USER-FORM:
  → user-list       ("← Back to Staff", Cancel)

From SETTINGS:
  → secondary-rate-refresh ("Update now" button)
```

---

## 14. Data Flow Diagrams

### 14.1 Order Lifecycle (Full Flow)
```
┌─────────────┐     GET     ┌─────────────┐     POST    ┌─────────────┐
│  Dashboard  │────────────►│ order-create│◄───────────│ order-detail│
│ ("New Order")│            │  (blank)    │  save draft │  (exists)   │
└─────────────┘            └──────┬──────┘             └──────┬──────┘
                                  │                           │
                                  │ POST create               │ POST update
                                  ▼                           ▼
                           ┌─────────────┐             ┌─────────────┐
                           │ order-detail│◄────────────│  order-db   │
                           │  (Draft)    │   redirect  │  (updated)  │
                           └──────┬──────┘             └─────────────┘
                                  │
                                  │ POST submit
                                  ▼
                           ┌─────────────┐
                           │ order-detail│
                           │  (Pending)  │◄────── Manager/Admin ─────┐
                           └──────┬──────┘                           │
                                  │ POST confirm                      │
                                  ▼                                   │
                           ┌─────────────┐     POST record payment   │
                           │ order-detail│◄──────────────────────────┤
                           │ (Confirmed) │                             │
                           └──────┬──────┘                             │
                                  │                                    │
                    ┌─────────────┼─────────────┐                      │
                    │             │             │                      │
                    ▼             ▼             ▼                      │
              ┌─────────┐  ┌─────────┐  ┌──────────┐                  │
              │ cancel  │  │  pay    │  │  (wait)  │                  │
              │ Manager+│  │  any    │  │          │                  │
              └────┬────┘  └────┬────┘  └────┬─────┘                  │
                   │            │            │                        │
                   ▼            ▼            │                        │
              ┌─────────┐  ┌─────────┐      │                        │
              │Cancelled│  │  Paid   │──────┘                        │
              │(restore)│  │ Staff+  │                               │
              └─────────┘  └────┬────┘                               │
                                │                                    │
                    ┌───────────┼───────────┐                        │
                    ▼           ▼           ▼                        │
              ┌─────────┐  ┌─────────┐  ┌──────────┐                 │
              │  ship   │  │ refund  │  │  (wait)  │                 │
              │ Staff+  │  │  Admin  │  │          │                 │
              └────┬────┘  └────┬────┘  └──────────┘                 │
                   │            │                                      │
                   ▼            ▼                                      │
              ┌─────────┐  ┌─────────┐                               │
              │ Shipped │  │ Refunded│                               │
              │ Staff+  │  │(restore)│◄───────────────────────────────┘
              └────┬────┘  └─────────┘
                   │
                   ▼ deliver (Staff+)
              ┌──────────┐
              │Delivered │
              │  (final) │
              └──────────┘
```

### 14.2 Inventory Adjustment Flow
```
┌─────────────────┐      click "Adjust"      ┌─────────────────┐
│ inventory-list  │─────────────────────────►│  adjust-modal   │
│   (per row)     │                          │  (pre-filled)   │
└─────────────────┘                          └────────┬────────┘
                                                      │
                            ┌─────────────────────────┘
                            │ click header "Adjust Stock"
                            ▼
                     ┌─────────────────┐
                     │  adjust-modal   │
                     │ (show dropdown) │
                     └────────┬────────┘
                              │ POST
                              ▼
                     ┌─────────────────┐
                     │inventory-adjust │
                     │  (view logic)   │
                     └────────┬────────┘
                              │ creates InventoryMovement
                              ▼
                     ┌─────────────────┐
                     │ inventory-list  │
                     │ (with message)  │
                     └─────────────────┘
```

### 14.3 AJAX Category Creation Flow
```
┌─────────────────┐      click "+ New"       ┌─────────────────┐
│  product-form   │─────────────────────────►│ modal-new-cat   │
│ (category field)│                          │  (custom modal) │
└─────────────────┘                          └────────┬────────┘
                                                      │
                                            ┌─────────┘
                                            │ click "Create"
                                            ▼
                                     ┌─────────────────┐
                                     │category-create  │
                                     │  (AJAX POST)    │
                                     │ returns JSON    │
                                     └────────┬────────┘
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    │ success                      error                │
                    ▼                                                       ▼
            ┌─────────────────┐                                   ┌─────────────────┐
            │ Append option to│                                   │ Show error in   │
            │ category select │                                   │ modal, keep open│
            │ Auto-select new │                                   └─────────────────┘
            │ Close modal     │
            └─────────────────┘
```

---

## 15. Role-Based Access Control Summary

| Feature | Staff | Manager | Admin |
|---------|:-----:|:-------:|:-----:|
| View Dashboard | ✓ | ✓ | ✓ |
| View Orders | ✓ | ✓ | ✓ |
| Create Orders | ✓ | ✓ | ✓ |
| Edit Draft/Pending | ✓ | ✓ | ✓ |
| Submit Draft → Pending | ✓ | ✓ | ✓ |
| Confirm Pending → Confirmed | ✗ | ✓ | ✓ |
| Ship / Deliver | ✓ | ✓ | ✓ |
| Cancel Confirmed | ✗ | ✓ | ✓ |
| Refund Paid | ✗ | ✗ | ✓ |
| View Customers | ✓ | ✓ | ✓ |
| Create/Edit Customers | ✓ | ✓ | ✓ |
| Delete Customers | ✓ | ✓ | ✓ |
| View Payments | ✓ | ✓ | ✓ |
| Record Payments | ✓ | ✓ | ✓ |
| View Inventory | ✓ | ✓ | ✓ |
| Add/Edit Products | ✗ | ✓ | ✓ |
| Adjust Stock | ✗ | ✓ | ✓ |
| View Categories | ✓ | ✓ | ✓ |
| Manage Categories | ✗ | ✓ | ✓ |
| View Users | ✗ | ✗ | ✓ |
| Invite/Edit/Deactivate Users | ✗ | ✗ | ✓ |
| View Settings | ✓ | ✓ | ✓ |
| Edit Currency/OCR | ✗ | ✗ | ✓ |
| Refresh Exchange Rate | ✗ | ✗ | ✓ |

---

## 16. Template Reuse Patterns

| Shared Component | Used By | Purpose |
|-----------------|---------|---------|
| `base.html` | All authenticated pages | Navbar, CSS, toast, modal, panel systems |
| `customer_form.html` | `customer_create`, `customer_edit` | Dual-purpose create/edit with `{% if customer %}` |
| `order_detail.html` | `order_create`, `order_detail` | Dual-purpose blank/existing with `{% if order %}` |
| `product_form.html` | `product_create`, `product_edit` | Dual-purpose with image upload handling |
| `category_form.html` | `category_create`, `category_edit` | Dual-purpose with parent category select |
| `user_form.html` | `user_edit` | Profile + password change side-by-side |
| `_order_form_context()` | `order_create`, `order_detail` | Shared context builder |
| Pagination widget | All list templates | Consistent page-range with query preservation |
| Filter bar | All list templates | Search + facet filters + active indicator |
| Empty state | All list templates | Icon + message + CTA |

---

*Document generated from exhaustive inspection of `core/templates/`, `core/views.py`, `core/urls.py`, and `retailops/urls.py`.*
