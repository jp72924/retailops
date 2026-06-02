# Retail & E-Commerce Order Management System — Design Artifact Generator

You are a senior systems architect and UI/UX designer. Your task is to produce three complete design artifacts for a retail/e-commerce order management system built with Django (backend) and a vanilla HTML + CSS + JS frontend. The system is intended for internal business operations.

---

## SYSTEM OVERVIEW

**System Name:** RetailOps — Unified Retail & E-Commerce Order Management System

**Functional Scope:**
- Customer registration and management
- Sales order placement and lifecycle tracking
- Manual payment recording and reconciliation
- Inventory management with low-stock alerts
- Role-based access control (Admin, Manager, Staff)

**Business Context:**
- Retail store + e-commerce order management + internal business operations in one unified system
- Payments are manually recorded internally after the fact (no real payment gateway integration)
- Scale: hundreds of records (no enterprise-scale performance optimizations required)

---

## ARTIFACT 1 — ENTITY RELATIONSHIP DIAGRAM (Mermaid)

Produce a complete ERD using Mermaid `erDiagram` syntax. The diagram must cover **all entities** and **all relationships** described below.

### Entities and Attributes

**1. Role**
- id (PK, integer, auto)
- name (string: Admin | Manager | Staff)
- description (text, optional)
- created_at (datetime)
- updated_at (datetime)

**2. User**
- id (PK, integer, auto)
- email (string, unique, used for login)
- password_hash (string, Django handles this)
- first_name (string)
- last_name (string)
- role_id (FK → Role)
- is_active (boolean, default true)
- created_at (datetime)
- updated_at (datetime)

**3. Customer**
- id (PK, integer, auto)
- user_id (FK → User, optional — for customers who also have system login)
- first_name (string)
- last_name (string)
- email (string, unique)
- phone (string, optional)
- address_line1 (string)
- address_line2 (string, optional)
- city (string)
- state (string)
- postal_code (string)
- country (string, default from settings)
- notes (text, optional)
- created_at (datetime)
- updated_at (datetime)

**4. Product**
- id (PK, integer, auto)
- sku (string, unique)
- name (string)
- description (text, optional)
- category_id (FK → ProductCategory)
- unit_of_measure (string: piece | kg | liter | meter | box | pack)
- unit_price (decimal, 2dp)
- low_stock_threshold (integer, default 10)
- is_active (boolean, default true)
- created_at (datetime)
- updated_at (datetime)

**5. ProductCategory**
- id (PK, integer, auto)
- name (string, unique)
- description (text, optional)
- parent_category_id (FK → ProductCategory, self-referential, optional) — supports subcategories
- created_at (datetime)
- updated_at (datetime)

**6. InventoryMovement**
- id (PK, integer, auto)
- product_id (FK → Product)
- movement_type (string: sale | purchase | adjustment | return)
- quantity (integer — positive for additions, negative for deductions)
- reference_type (string: SalesOrder | PurchaseOrder | ManualAdjustment | Return)
- reference_id (integer — ID of the related order or adjustment record)
- notes (text, optional)
- created_by_id (FK → User)
- created_at (datetime)

**7. SalesOrder**
- id (PK, integer, auto)
- order_number (string, unique, auto-generated, format: SO-YYYYMMDD-XXXX)
- customer_id (FK → Customer)
- status (string: draft | pending | confirmed | paid | shipped | delivered | cancelled | refunded)
- subtotal (decimal, 2dp)
- tax_amount (decimal, 2dp)
- discount_amount (decimal, 2dp, default 0)
- total_amount (decimal, 2dp)
- notes (text, optional)
- created_by_id (FK → User)
- confirmed_by_id (FK → User, optional)
- created_at (datetime)
- updated_at (datetime)
- confirmed_at (datetime, optional)
- paid_at (datetime, optional)

**8. SalesOrderItem**
- id (PK, integer, auto)
- sales_order_id (FK → SalesOrder)
- product_id (FK → Product)
- quantity (integer)
- unit_price (decimal, 2dp — snapshot at time of order)
- tax_rate (decimal, 4dp, default 0)
- line_total (decimal, 2dp)
- created_at (datetime)

**9. Payment**
- id (PK, integer, auto)
- payment_number (string, unique, auto-generated, format: PAY-YYYYMMDD-XXXX)
- sales_order_id (FK → SalesOrder)
- amount (decimal, 2dp)
- payment_method (string: cash | bank_transfer | card | check | other)
- reference_number (string, optional — e.g., check number, transaction ID)
- recorded_by_id (FK → User)
- notes (text, optional)
- created_at (datetime)

### Relationships

- Role **1:N** User
- User **1:N** Customer (optional — for customers with login)
- Customer **1:N** SalesOrder
- ProductCategory **1:N** Product
- ProductCategory **1:N** ProductCategory (self-referential, parent)
- SalesOrder **1:N** SalesOrderItem
- Product **1:N** SalesOrderItem
- SalesOrder **1:N** Payment
- SalesOrder **1:N** InventoryMovement
- User **1:N** InventoryMovement
- User **1:N** Payment

### Output Format

Return the Mermaid diagram inside a fenced code block using the `erDiagram` syntax. Include a brief text legend explaining cardinality symbols used.

---

## ARTIFACT 2 — WORKFLOW DIAGRAM (Mermaid)

Produce a workflow / state machine diagram using Mermaid `flowchart LR` (or `stateDiagram-v2`) syntax. Cover the following business workflows:

### Workflow A — Order-to-Payment Lifecycle

```
Draft → Pending → Confirmed → Paid → Shipped → Delivered
                              ↘ (on cancel) → Cancelled
                   (on refund) → Refunded
```

Show decision nodes and actions that trigger each transition:
- Draft → Pending: triggered by Staff saving the order
- Pending → Confirmed: triggered by Manager approving stock availability
- Confirmed → Paid: triggered by Staff recording a payment
- Paid → Shipped: triggered by Staff marking order shipped
- Shipped → Delivered: triggered by Staff confirming delivery
- Confirmed → Cancelled: triggered by Manager or Admin
- Paid → Refunded: triggered by Admin

### Workflow B — Inventory Update Logic

Show how inventory is affected by each order lifecycle event:
- On order confirmation: deduct reserved stock
- On payment recorded: deduct committed stock
- On cancellation (before payment): restore reserved stock
- On refund: add stock back (return)

### Workflow C — User Onboarding / Customer Registration

```
Guest → Fill Registration Form → Account Created (Pending) → Admin Approves → Active Customer
```

### Workflow D — Low-Stock Alert

```
Inventory Check → Stock ≤ Threshold? → [Yes] → Generate Alert Notification → [No] → No Action
```

### Output Format

Return each workflow as a separate Mermaid fenced code block with a short label.

---

## ARTIFACT 3 — VISUAL WIREFRAME MOCKUPS (HTML/CSS)

Produce **one complete HTML file** containing wireframe mockups for **all core views** of the application. Each view must be visually distinct, clearly labeled, and rendered in a side-by-side or stacked grid layout.

### Views to Design

**1. Dashboard (Home)**
- Top navigation bar with logo, nav links (Dashboard, Orders, Customers, Inventory, Payments, Settings), user avatar + role badge, logout button
- Summary cards row: Total Orders (this month), Total Revenue, Pending Payments, Low Stock Items
- Recent Orders table (last 5): columns = Order #, Customer, Date, Status, Total; status shown as colored badge
- Inventory Alerts panel: list of products below low-stock threshold
- Quick Actions sidebar: "New Order", "Register Customer", "Record Payment"

**2. Customer Registration / Edit Form**
- Section: Personal Information (First Name, Last Name, Email, Phone)
- Section: Address (Address Line 1, Address Line 2, City, State, Postal Code, Country)
- Section: Notes (textarea)
- Form buttons: Save Customer, Cancel
- Validation error states (show one example field in error state with red border and error message below)
- Success toast notification (show in triggered state)

**3. Sales Order List View**
- Filter bar: Search by Order # or Customer name; Filter by Status (dropdown); Filter by Date Range
- Data table: Order #, Customer Name, Date, Status (badge), Items Count, Total Amount, Actions (View, Edit, Delete)
- Pagination controls
- "New Order" button (top right)
- Empty state illustration placeholder (for when no orders match filters)

**4. Sales Order Detail / Create View**
- Header: Order # (SO-20250601-0001), Status badge, Created By, Date
- Customer info panel (read-only summary)
- Line items table: Product (dropdown/autocomplete), SKU (auto-populated), Qty, Unit Price, Line Total; "Add Line Item" button; "Remove" per row
- Totals section: Subtotal, Tax, Discount (input), Grand Total
- Action buttons: Save Draft / Confirm Order / Record Payment / Cancel Order (role-dependent visibility)
- Payment history sub-panel (if order has payments): Payment #, Amount, Method, Date, Recorded By

**5. Payment Recording Modal / Form**
- Field: Select Order (searchable dropdown showing Order # + Customer + Outstanding Balance)
- Field: Amount (pre-filled with outstanding balance, editable)
- Field: Payment Method (radio buttons: Cash, Bank Transfer, Card, Check, Other)
- Field: Reference Number (conditional — shown for bank_transfer, card, check)
- Field: Notes (textarea)
- Buttons: Record Payment (primary), Cancel

**6. Inventory Management View**
- Filter bar: Search by SKU or Name; Filter by Category; Filter by Stock Status (All | Low Stock | Out of Stock | In Stock)
- Product table: SKU, Product Name, Category, Unit, Unit Price, Stock Level (colored: green ≥ threshold, orange < threshold, red = 0), Actions (Edit, View Movements)
- "Add Product" button
- Low-stock indicator banner (shown when any product is below threshold)
- Inventory Movement History side panel (slide-in or toggle): Date, Type, Qty, Reference, Notes

**7. User / Staff Management View (Admin only)**
- User table: Name, Email, Role (badge: Admin=blue, Manager=green, Staff=gray), Status (Active/Inactive), Last Login, Actions (Edit, Deactivate)
- "Invite New User" button → opens modal form: First Name, Last Name, Email, Role (select), Send Invite toggle
- Role change confirmation dialog (example)

**8. Login Page**
- Centered card layout
- Logo + App Name ("RetailOps")
- Email field, Password field, "Remember Me" checkbox
- "Sign In" button
- Error state: "Invalid email or password" (shown for demo)
- Footer: "Forgot Password?" link

### Design Specifications for All Views

- **Color palette:** Use CSS variables for the palette. Suggested: primary `#2563EB` (blue), secondary `#1E293B` (slate), success `#16A34A`, warning `#D97706`, danger `#DC2626`, background `#F8FAFC`, surface `#FFFFFF`, text `#0F172A`, muted `#64748B`.
- **Typography:** System font stack (`-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`). Headings: bold, 1.25rem–1.75rem. Body: 0.875rem–1rem.
- **Spacing:** Consistent 8px grid. Use `gap`, `padding`, and `margin` in multiples of 8.
- **Badges:** Small pill-shaped status badges. Draft=gray, Pending=yellow, Confirmed=blue, Paid=green, Shipped=indigo, Delivered=emerald, Cancelled=red, Refunded=orange.
- **Cards:** White surface with `border: 1px solid #E2E8F0`, `border-radius: 8px`, `box-shadow: 0 1px 3px rgba(0,0,0,0.1)`.
- **Navigation:** Horizontal top bar with `background: #1E293B`, white text. Active link has bottom border accent in primary blue.
- **Tables:** `border-collapse: separate`, `border-spacing: 0`, `border-radius: 8px` on outer container, alternating row background `#F8FAFC`.
- **Forms:** Labels above inputs, `border: 1px solid #CBD5E1`, `border-radius: 6px`, focus state with blue outline. Error state: red border + red message below.
- **Buttons:** Primary: filled blue, white text. Secondary: white with border. Danger: red. All with `border-radius: 6px`, `padding: 8px 16px`.
- **Responsive:** All layouts must be responsive down to 768px (tablet). Use CSS Grid or Flexbox. No fixed pixel widths on main containers.

### Interactive Elements (Vanilla JS)

Include minimal JavaScript to demonstrate:
- Tab switching on Dashboard (Summary / Recent Orders / Alerts panels)
- Filter behavior on Sales Order List (show filter active state)
- Modal open/close on Payment Recording and Invite User
- Toast notification trigger on Payment Recording success
- Low-stock banner show/hide toggle on Inventory view
- Side panel slide-in for Inventory Movement History
- Form validation error state toggle on Login page
- Line item row add/remove on Sales Order form

---

## FINAL DELIVERABLE STRUCTURE

Return your response organized as follows:

1. **Section: Entity Relationship Diagram** — Mermaid `erDiagram` code block + brief legend
2. **Section: Workflow Diagrams** — 4 separate Mermaid `flowchart` / `stateDiagram-v2` code blocks, one per workflow
3. **Section: HTML Wireframe Application** — A single, complete, runnable HTML file with embedded CSS and JS, containing all 8 views as distinct sections in a tabbed or scrollable layout. Label each section clearly.

Each section must be self-contained and ready to render in a modern browser or Mermaid preview tool.