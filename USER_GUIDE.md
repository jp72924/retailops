# RetailOps — User Guide

This guide walks you through the day-to-day use of RetailOps: how to log in, register customers, place and process orders, record payments, and manage inventory. No technical knowledge is required.

---

## Table of Contents

1. [Roles & What Each Can Do](#1-roles--what-each-can-do)
2. [Logging In & Out](#2-logging-in--out)
3. [The Dashboard](#3-the-dashboard)
4. [Managing Customers](#4-managing-customers)
5. [Processing a Sales Order — End to End](#5-processing-a-sales-order--end-to-end)
6. [Recording a Payment](#6-recording-a-payment)
7. [Cancelling an Order](#7-cancelling-an-order)
8. [Issuing a Refund](#8-issuing-a-refund)
9. [Managing Inventory](#9-managing-inventory)
10. [Managing Staff Accounts (Admin Only)](#10-managing-staff-accounts-admin-only)
11. [Settings & Preferences](#11-settings--preferences)

---

## 1. Roles & What Each Can Do

Every user in RetailOps is assigned one of three roles. Your role controls which buttons and pages you can access.

| Action | Staff | Manager | Admin |
|--------|:-----:|:-------:|:-----:|
| View dashboard, customers, orders, payments, inventory | Yes | Yes | Yes |
| Register and edit customers | Yes | Yes | Yes |
| Change your own time-zone and language preferences | Yes | Yes | Yes |
| Create a new sales order | Yes | Yes | Yes |
| Submit an order for review (Draft → Pending) | Yes | Yes | Yes |
| Record payments against confirmed orders | Yes | Yes | Yes |
| Approve/confirm an order (Pending → Confirmed) | — | Yes | Yes |
| Mark an order as Shipped / Delivered | Yes | Yes | Yes |
| Cancel a confirmed order | — | Yes | Yes |
| Issue a refund | — | — | Yes |
| Add or edit products | — | Yes | Yes |
| Add or edit product categories | — | Yes | Yes |
| Record manual stock adjustments | — | Yes | Yes |
| Change system currency settings | — | Yes | Yes |
| Manage staff accounts | — | — | Yes |

If you try to perform an action your role does not allow, the system will show a "Permission Denied" page.

---

## 2. Logging In & Out

### Logging in

1. Open RetailOps in your browser. You will land on the **Login** page automatically.
2. Enter your **email address** and **password**.
3. Click **Log In**.
4. You will be taken to the **Dashboard**.

If you see "Invalid email or password", double-check your credentials. Contact your Admin if you cannot log in.

### Logging out

Click the **Logout** button on the right-hand side of the navigation bar. You will be returned to the Login page.

### Forgot your password?

If you cannot remember your password:

1. On the Login page, click the **Forgot password?** link below the form.
2. Enter the **email address** for your account and click **Send reset link**.
3. The system will email you a one-time password-reset link. (In a development setup, the link is printed to the server console rather than sent — your Admin can retrieve it for you.)
4. Open the link from the email. You will be asked to enter a **new password** twice.
5. Click **Set new password**. You will then be sent back to the Login page; sign in with your new password.

The page never reveals whether an email address belongs to a real account, so you will see the same confirmation message even if you mistype the address. If no email arrives, double-check the address with your Admin.

---

## 3. The Dashboard

The Dashboard is your home screen. It gives you a quick snapshot of the business:

- **Summary cards** at the top show orders this month, total revenue, payments outstanding, and how many products are running low on stock.
- **Recent Orders** tab lists the five most recent orders with their current status.
- **Inventory Alerts** tab lists products that have fallen below their low-stock threshold.
- **Quick Actions** sidebar has shortcut buttons for the most common tasks: New Customer, New Order, Record Payment, Add Product.

Use the navigation bar at the top to move between sections: **Dashboard**, **Orders**, **Customers**, **Inventory**, **Categories**, **Payments**, **Settings**, and (Admins only) **Users**.

---

## 4. Managing Customers

Every order must be linked to a customer, so customers need to be registered before you can place an order for them.

### Registering a new customer

1. Click **Customers** in the navigation bar.
2. Click the **New Customer** button (top-right of the page).
3. Fill in the form:

   **Identification**
   - **First Name** and **Last Name** — required.
   - **Email** — required; must be unique across all customers.
   - **Phone** — optional.
   - **ID Number** — optional national / tax identification number (e.g. cédula, DNI, SSN). If provided it must be unique across all customers.
   - **Date of Birth** — optional; pick a date from the calendar.
   - **Gender** — optional; choose **Masculino**, **Femenino**, or leave as **— Not specified —**.

   **Address**
   - **Address Line 1** — Street address. Required.
   - **Address Line 2** — Apartment, suite, building name, etc. Optional.
   - **City**, **State / Province**, **Postal Code**, and **Country** — all required.

   **Other**
   - **Notes** — any free-text notes about the customer (e.g. preferred contact method, special instructions).

4. Click **Save Customer**.
5. You will be taken to the customer's profile page, which shows their contact details and full order history.

### Editing a customer

1. From the customer list or their profile page, click **Edit**.
2. Make your changes.
3. Click **Save Customer**.

### Searching for a customer

On the Customers page, type a name or email into the search bar and press Enter. The list will filter to matching records.

### Deleting a customer

A customer can only be deleted if they have no orders on record. Click **Delete** on their profile and confirm the action. If the button is not visible or the action fails, the customer has existing orders and cannot be removed.

---

## 5. Processing a Sales Order — End to End

A sales order moves through these stages in order:

```
Draft → Pending → Confirmed → Paid → Shipped → Delivered
```

Each stage is described below.

---

### Step 1 — Create the order (Draft)

*Who can do this: Staff, Manager, Admin*

1. Click **Orders** in the navigation bar, then click **New Order**.
2. Select the **Customer** from the dropdown. If the customer is not listed, register them first (see Section 4).
3. Add line items:
   - Click **Add Item**.
   - Select a **Product** from the dropdown or type a SKU. The unit price will fill in automatically based on the product's current price.
   - Enter the **Quantity**.
   - Repeat for each product in the order.
   - To remove a line, click the trash icon on that row.
4. Optionally fill in:
   - **Discount** — a fixed amount to subtract from the subtotal.
   - **Tax** — a fixed tax amount to add.
   - **Notes** — any internal notes about this order.
5. Review the totals at the bottom of the line-items table.
6. Click **Save Order**.

The order is saved as a **Draft**. A unique order number is assigned automatically (format: `SO-YYYYMMDD-XXXX`). The order can still be edited freely while it is in Draft.

---

### Step 2 — Submit for review (Draft → Pending)

*Who can do this: Staff, Manager, Admin*

Once the order looks correct, submit it for a manager to review:

1. Open the order (click its order number in the Orders list).
2. Click the **Submit for Review** button.
3. The status changes to **Pending**. The order is now locked against further edits.

---

### Step 3 — Confirm the order (Pending → Confirmed)

*Who can do this: Manager, Admin*

A manager or admin reviews the order and approves it:

1. Open the Pending order.
2. Verify the customer, products, quantities, and totals are correct.
3. Click **Confirm Order**.
4. The status changes to **Confirmed**. Stock is automatically deducted from inventory at this point.

If there is a problem with the order, it can be cancelled at this stage (see Section 7).

---

### Step 4 — Record payment (Confirmed → Paid)

*Who can do this: Staff, Manager, Admin*

Once the order is confirmed, payment can be recorded:

1. Open the Confirmed order.
2. Click **Record Payment**. A payment form appears (either in a modal or a side panel).
3. Fill in:
   - **Amount** — the amount received. You can record partial payments; the order moves to Paid only once the full amount is covered.
   - **Payment Method** — Cash, Bank Transfer, Card, Check, or Other.
   - **Reference Number** — optional; a cheque number, bank transfer ID, etc.
   - **Notes** — optional internal note.
4. Click **Save Payment**.

A unique payment number is assigned automatically (format: `PAY-YYYYMMDD-XXXX`). If the total payments recorded equal or exceed the order total, the order status automatically moves to **Paid**.

You can record multiple partial payments against the same order. Each one appears in the payment history on the order's detail page.

---

### Step 5 — Mark as Shipped (Paid → Shipped)

*Who can do this: Staff, Manager, Admin*

When the goods leave your warehouse or store:

1. Open the Paid order.
2. Click **Mark as Shipped**.
3. The status changes to **Shipped**.

---

### Step 6 — Mark as Delivered (Shipped → Delivered)

*Who can do this: Staff, Manager, Admin*

When delivery is confirmed:

1. Open the Shipped order.
2. Click **Mark as Delivered**.
3. The status changes to **Delivered**. This is the final, completed state for a normal order.

---

## 6. Recording a Payment

Payments are always recorded from the order detail page (see Step 4 in Section 5 above). You can also browse all payments by clicking **Payments** in the navigation bar.

The Payments list shows every payment with its payment number, linked order, amount, method, and date. Click any payment number to view the full payment receipt, including the linked order summary.

**Key things to know about payments:**

- Payments are **manual** — there is no automatic card processing. You record what was received.
- An order can have **multiple payments** (partial payments are supported). The order becomes Paid when cumulative payments cover the total.
- Payments cannot be deleted through the interface. To reverse a payment, the order must be refunded (Admin only — see Section 8).

---

## 7. Cancelling an Order

*Who can do this: Manager, Admin — only while the order is Confirmed*

An order can be cancelled after it has been confirmed but before payment is recorded:

1. Open the Confirmed order.
2. Click **Cancel Order**.
3. The status changes to **Cancelled**. Stock that was deducted when the order was confirmed is automatically returned to inventory.

> **Note:** Only **Draft** orders can be permanently deleted from the Orders list — at that point no stock has been touched. Once submitted (**Pending**), an order can no longer be deleted or cancelled directly; a Manager must confirm it first, after which it can be cancelled.

---

## 8. Issuing a Refund

*Who can do this: Admin only — only while the order is Paid*

If a paid order needs to be reversed:

1. Open the Paid order.
2. Click **Issue Refund**.
3. The status changes to **Refunded**. Stock is automatically added back to inventory.

Refunds are a record-keeping action — they mark the order as refunded in the system. The actual return of money to the customer is handled outside the system (cash back, bank transfer, etc.).

---

## 9. Managing Inventory

Click **Inventory** in the navigation bar to see all products and their current stock levels.

### Reading the inventory list

- Each row shows a product's SKU, name, category, unit of measure, unit price, and current stock.
- A **yellow warning** row or banner indicates the product's stock is below its low-stock threshold.
- A **red** indicator means the product is out of stock.
- Use the **search bar** to filter by SKU or product name.
- Use the **Category** dropdown to filter by product category.
- Use the **Stock Status** filter to show only low-stock, out-of-stock, or healthy products.

### Viewing stock movement history

Click the **history icon** (or "View Movements" button) on any product row. A side panel opens showing the last 50 stock movements for that product — purchases, sales, adjustments, and returns — with dates, quantities, and who made each entry.

### Adding a new product

*Who can do this: Manager, Admin*

1. On the Inventory page, click **Add Product**.
2. Fill in:
   - **SKU** — a unique identifier for the product (e.g. `SHOE-RED-42`).
   - **Name** — the display name.
   - **Category** — select from the existing categories.
   - **Unit of Measure** — Piece, Kilogram, Liter, Meter, Box, or Pack.
   - **Unit Price** — the default selling price. This can be overridden per order line.
   - **Low Stock Threshold** — the quantity below which the system will flag this product as low stock. Default is 10.
   - **Description** — optional.
3. Click **Save Product**.

The product starts with zero stock. Stock increases when you receive inventory (recorded as a manual adjustment or purchase movement) and decreases automatically when orders are confirmed.

### Editing a product

*Who can do this: Manager, Admin*

1. Find the product in the Inventory list.
2. Click the **Edit** button on its row.
3. Update the fields as needed.
4. Click **Save Product**.

> **Note:** Changing a product's unit price here does not affect already-placed orders. Order line items lock in the price at the time the order is created.

### Recording a manual stock adjustment

*Who can do this: Manager, Admin*

Use this whenever stock changes for a reason other than a confirmed order — for example when goods arrive from a supplier, when a counting error needs correcting, or when a customer returns merchandise.

You can open the adjustment form in two ways:

- **From the page header** — click **Adjust Stock** at the top-right. A product picker appears so you can choose any product.
- **From a product row** — click the row's **Adjust** action; the product is pre-selected for you.

Then fill in:

- **Movement Type** —
  - **Purchase** — stock received from a supplier.
  - **Adjustment** — correct a counting error.
  - **Return** — stock returned by a customer.
- **Quantity** — a signed number. Use a **positive** value to add stock (e.g. `50`) and a **negative** value to deduct stock (e.g. `-10`). Zero is rejected.
- **Notes** — optional reason, PO number, or any context worth keeping with the record.

Click **Record Adjustment**. The movement appears immediately in the product's stock-movement history (see *Viewing stock movement history* above).

> **Note:** Stock movements are **immutable**. Once recorded an adjustment cannot be edited or deleted — record a compensating movement instead.

### Managing categories

*Who can do this: Manager, Admin*

Categories group related products (e.g. *Electronics*, *Office Supplies*, *Cables*). Every product must belong to exactly one category.

1. Click **Categories** in the navigation bar (or open the Categories page from the Inventory area).
2. To add a category, click **New Category** and fill in:
   - **Name** — required and must be unique.
   - **Description** — optional.
   - **Parent Category** — optional. Leave blank for a top-level category, or pick an existing one to create a subcategory (e.g. *Electronics › Cables*).
3. To edit or delete a category, use the buttons on its row.

> A category cannot be deleted while products are still linked to it. Move or delete the products first, or pick a different category for them.

---

## 10. Managing Staff Accounts (Admin Only)

Click **Users** in the navigation bar (visible to Admins only) to manage who has access to RetailOps.

### Inviting a new user

1. On the Users page, click **Invite User**. A form appears.
2. Fill in:
   - **First Name** and **Last Name**.
   - **Email address** — this will be their login username.
   - **Role** — Staff, Manager, or Admin.
   - **Password** — set an initial password for them. Ask them to change it after first login.
3. Click **Send Invite** (or Save, depending on the version).

The user can now log in with the email and password you provided.

### Editing a user

1. Find the user in the list.
2. Click **Edit** on their row.
3. You can update their name, role, and password.
4. Click **Save**.

### Deactivating a user

If a staff member leaves, deactivate their account rather than deleting it (so their historical actions are preserved):

1. Find the user in the list.
2. Click **Deactivate**.
3. The user's status changes to Inactive. They will no longer be able to log in.

You cannot deactivate your own account.

### Reactivating a user

1. Find the inactive user in the list (they will be shown with an Inactive badge).
2. Click **Reactivate**.
3. They can log in again immediately.

---

## 11. Settings & Preferences

Click **Settings** in the navigation bar. The page is split into two areas: personal preferences that apply only to you, and system-wide settings that only Admins and Managers can change.

### Your personal preferences

*Who can do this: anyone who is logged in*

- **Time Zone** — Pick the IANA time-zone you want dates and times displayed in (e.g. `America/Caracas`, `Europe/Madrid`, `Asia/Tokyo`). All timestamps across the application — order creation times, payment dates, stock movement history — are converted to this zone immediately after you save.
- **Language** — Choose the display language for the back-office interface.

Click **Save Preferences** to apply your changes. The new time-zone and language take effect on the next page you visit.

### System currency settings (Manager / Admin)

*Who can do this: Manager, Admin*

These settings control how monetary amounts are displayed everywhere in RetailOps — the order list, order detail page, dashboard cards, payment records, and so on. They do **not** convert any of the prices already stored in the database; only the **display** changes.

#### Primary currency

This is the currency you operate in. All product prices, order totals, and payments are stored as decimal amounts in this currency.

- **Currency Code** — A three-letter ISO 4217 code such as `USD`, `EUR`, `GBP`, `VES`.
- **Currency Symbol** — The character (or short string) shown next to amounts (e.g. `$`, `€`, `£`, `Bs.`). Up to 4 characters.
- **Decimal Places** — How many digits to show after the decimal point. Use `0` for currencies like JPY, `2` for USD/EUR, `3` for KWD, etc.

A live preview underneath the form shows what amounts will look like with your settings.

#### Secondary currency (optional)

When enabled, every monetary amount is shown together with an approximate conversion in smaller muted text (e.g. `$3.49  ≈ Bs. 127,39`). This is useful if your business operates in a country where prices are quoted in one currency but customers naturally think in another.

1. Tick **Show a secondary currency alongside the primary** to enable the feature.
2. Fill in:
   - **Secondary Code** — ISO 4217 code (e.g. `VES`, `ARS`).
   - **Secondary Symbol** — Display symbol (e.g. `Bs.`, `$`). Required while the feature is enabled.
   - **Secondary Decimals** — Decimal places for the converted amount (0–4).
   - **Exchange Rate** — How many units of the secondary currency equal **one** unit of the primary currency. Must be greater than zero.
3. Click **Save Settings**.

> The exchange rate is **static** — it stays the value you set until an Admin changes it. Update it whenever you need the displayed conversions to reflect the current market. The kiosk PWA, if your business uses one, has its own live-rate pipeline and is unaffected by this value.

To turn the feature off, simply untick the checkbox and save. The other secondary-currency fields are kept on file but are ignored everywhere until the feature is re-enabled.

---

## Quick Reference — Order Status Summary

| Status | What it means | Next action |
|--------|--------------|-------------|
| **Draft** | Created, not yet reviewed | Submit for Review (or Delete) |
| **Pending** | Waiting for manager approval | Confirm (Manager/Admin) |
| **Confirmed** | Approved; stock reserved | Record Payment (or Cancel — Manager/Admin) |
| **Paid** | Payment received in full | Mark as Shipped |
| **Shipped** | Goods dispatched | Mark as Delivered |
| **Delivered** | Order complete | — |
| **Cancelled** | Cancelled before payment; stock restored | — |
| **Refunded** | Paid order reversed; stock restored | — |
