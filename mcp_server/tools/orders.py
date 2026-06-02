"""
mcp_server/tools/orders.py
----------------------------
Sales order tools (12 tools) including all lifecycle transitions
and the payment recording tool.

Order status machine:
  Draft → submit → Pending → confirm → Confirmed → [payment] → Paid
                                      → cancel   → Cancelled
                                                   Paid → ship → Shipped
                                                               → deliver → Delivered
                                                   Paid → refund → Refunded

Inventory side-effects:
  confirm  → negative InventoryMovement per line item (deduct stock)
  cancel   → positive InventoryMovement per line item (restore stock)
  refund   → positive InventoryMovement per line item (restore stock)

Permission requirements:
  create / update / delete / submit / ship / deliver → Staff, Manager, or Admin
  confirm / cancel                                   → Manager or Admin
  refund                                             → Admin only
  record_payment                                     → Any authenticated user

All transitions are enforced server-side; the MCP layer does not re-validate status.
"""

from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


ALLOWED_RECEIPT_MIME_TYPES = {"image/jpeg", "image/png", "image/heic", "image/heif"}


def register_order_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @mcp.tool()
    async def retailops_list_orders(
        customer: Optional[int] = None,
        status: Optional[Literal[
            "draft", "pending", "confirmed", "paid",
            "shipped", "delivered", "cancelled", "refunded"
        ]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        List sales orders with optional filters and pagination.

        Each result includes the order header, computed amount_paid and
        amount_outstanding, customer summary, and key timestamps.

        Args:
            customer:  Filter by customer ID.
            status:    Filter by order status. One of: draft, pending, confirmed,
                       paid, shipped, delivered, cancelled, refunded.
            date_from: Earliest creation date to include (YYYY-MM-DD).
            date_to:   Latest creation date to include (YYYY-MM-DD).
            page:      Page number (1-based). Defaults to 1.
            page_size: Results per page. Defaults to 25, maximum 100.
        """
        try:
            return await client.get("/orders/", {
                "customer": customer,
                "status": status,
                "date_from": date_from,
                "date_to": date_to,
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_order(id: int) -> dict:
        """
        Retrieve a full sales order including line items, totals, and payment history.

        Returns:
          - Order header: order_number, status, customer, created_at, notes.
          - Financial: subtotal, tax_amount, discount_amount, total_amount,
                       amount_paid, amount_outstanding.
          - Line items: each with product (nested), quantity, unit_price,
                        tax_rate, line_total.
          - Metadata: created_by, confirmed_by, confirmed_at, paid_at.

        Args:
            id: The order's integer primary key.
        """
        try:
            return await client.get(f"/orders/{id}/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_create_order(
        customer_id: int,
        items: list,
        discount_amount: Optional[str] = None,
        tax_amount: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Create a new sales order in Draft status. Requires Staff, Manager, or Admin role.

        Draft orders do NOT affect inventory — stock is only deducted when the
        order is confirmed via retailops_confirm_order.

        The order_number is auto-generated in the format SO-YYYYMMDD-XXXX.

        Args:
            customer_id:     ID of the customer placing the order (required).
            items:           List of line items (required, minimum 1). Each item is a dict:
                               { "product_id": int,         ← required
                                 "quantity":   int,         ← required (>= 1)
                                 "unit_price": "9.99",      ← optional, defaults to product price
                                 "tax_rate":   "0.0000" }   ← optional, defaults to 0
            discount_amount: Order-level discount as decimal string, e.g. "10.00".
                             Defaults to "0.00".
            tax_amount:      Additional order-level tax as decimal string. Defaults to "0.00".
            notes:           Internal notes for this order.

        Returns the created order object with computed subtotal and total_amount.
        """
        if not items:
            raise ValueError("items must contain at least one line item.")

        try:
            return await client.post("/orders/", {
                "customer_id": customer_id,
                "items": items,
                "discount_amount": discount_amount,
                "tax_amount": tax_amount,
                "notes": notes,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_update_order(
        id: int,
        items: Optional[list] = None,
        discount_amount: Optional[str] = None,
        tax_amount: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Edit a sales order. Requires Staff, Manager, or Admin role.

        Only allowed while the order is in Draft or Pending status.
        Providing items replaces ALL existing line items atomically —
        this is not an item-level patch.

        Args:
            id:              The order's integer primary key (required).
            items:           New complete set of line items (replaces all existing items).
                             Same format as retailops_create_order.items.
            discount_amount: New order-level discount as decimal string.
            tax_amount:      New order-level tax as decimal string.
            notes:           Updated internal notes.

        Returns the updated order object with recalculated totals.
        """
        try:
            return await client.patch(f"/orders/{id}/", {
                "items": items,
                "discount_amount": discount_amount,
                "tax_amount": tax_amount,
                "notes": notes,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_delete_order(id: int) -> dict:
        """
        Permanently delete a sales order. Requires Staff, Manager, or Admin role.

        Only allowed for orders in Draft status. All line items are deleted
        in cascade. This action is irreversible.

        To delete a Pending/Confirmed order, first cancel it
        (retailops_cancel_order), which is also irreversible but restores stock.

        Args:
            id: The order's integer primary key.
        """
        try:
            await client.delete(f"/orders/{id}/")
            return {"message": f"Order {id} deleted successfully."}
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    @mcp.tool()
    async def retailops_submit_order(id: int) -> dict:
        """
        Advance an order from Draft to Pending. Requires Staff, Manager, or Admin role.

        Submitting signals the order is ready for manager review.
        No inventory changes occur at this stage.

        Constraint: order must currently be in Draft status.

        Args:
            id: The order's integer primary key.

        Returns the updated order object (status: "pending").
        """
        try:
            return await client.post(f"/orders/{id}/submit/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_confirm_order(id: int) -> dict:
        """
        Confirm a Pending order, advancing it to Confirmed. Requires Manager or Admin role.

        THIS IS THE CRITICAL INVENTORY STEP. Confirmation atomically:
        1. Creates one negative InventoryMovement per line item (deducts stock).
        2. Sets confirmed_by to the agent account and confirmed_at to now.
        3. Changes order status to "confirmed".

        If any inventory operation fails, the entire transaction rolls back
        (no partial stock deductions). Order can now receive payments.

        Constraints:
          - Order must currently be in Pending status.
          - Order must have at least one line item.

        Args:
            id: The order's integer primary key.

        Returns the updated order object (status: "confirmed").
        """
        try:
            return await client.post(f"/orders/{id}/confirm/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_ship_order(id: int) -> dict:
        """
        Mark a Paid order as Shipped. Requires Staff, Manager, or Admin role.

        No inventory changes — stock was already deducted at confirmation.

        Constraint: order must currently be in Paid status.

        Args:
            id: The order's integer primary key.

        Returns the updated order object (status: "shipped").
        """
        try:
            return await client.post(f"/orders/{id}/ship/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_deliver_order(id: int) -> dict:
        """
        Mark a Shipped order as Delivered. Requires Staff, Manager, or Admin role.

        This is the final step in the standard order lifecycle.
        No inventory changes occur.

        Constraint: order must currently be in Shipped status.

        Args:
            id: The order's integer primary key.

        Returns the updated order object (status: "delivered").
        """
        try:
            return await client.post(f"/orders/{id}/deliver/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_cancel_order(id: int) -> dict:
        """
        Cancel a Confirmed order, restoring inventory. Requires Manager or Admin role.

        Cancellation atomically:
        1. Creates one positive InventoryMovement per line item (restores stock).
        2. Changes order status to "cancelled".

        Cannot be used after payment — use retailops_refund_order for Paid orders.

        Constraint: order must currently be in Confirmed status.

        Args:
            id: The order's integer primary key.

        Returns the updated order object (status: "cancelled").
        """
        try:
            return await client.post(f"/orders/{id}/cancel/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_refund_order(id: int) -> dict:
        """
        Refund a Paid order, restoring inventory. Requires Admin role.

        Refund atomically:
        1. Creates one positive InventoryMovement per line item (restores stock).
        2. Changes order status to "refunded".

        This is the most consequential reversal action and is Admin-only.
        Financial records (Payment rows) are NOT deleted — they remain as
        immutable records.

        Constraint: order must currently be in Paid status.

        Args:
            id: The order's integer primary key.

        Returns the updated order object (status: "refunded").
        """
        try:
            return await client.post(f"/orders/{id}/refund/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    # ------------------------------------------------------------------
    # Payment recording (lives here because its primary effect is on order status)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def retailops_record_payment(
        sales_order_id: int,
        amount: str,
        payment_method: Literal["cash", "mobile_payment", "bank_transfer", "card", "check", "other"],
        reference_number: Optional[str] = None,
        notes: Optional[str] = None,
        status: Optional[Literal["pending_review", "confirmed"]] = None,
        transaction_key: Optional[str] = None,
        origin_phone: Optional[str] = None,
        origin_bank: Optional[str] = None,
        recipient_bank: Optional[str] = None,
        recipient_account: Optional[str] = None,
        ocr_receipt_data: Optional[dict] = None,
        receipt_image_path: Optional[str] = None,
    ) -> dict:
        """
        Record a payment against a Confirmed order. Any authenticated role.

        Payments are immutable financial records — they cannot be edited or deleted.
        The payment_number is auto-generated in the format PAY-YYYYMMDD-XXXX.

        Auto-transition to Paid:
          If the running total of payments meets or exceeds the order's total_amount,
          the order status automatically transitions to "paid" and paid_at is stamped.
          This uses a database-level lock to prevent concurrent double-transitions.

        Constraint: the sales order must currently be in Confirmed status.

        Args:
            sales_order_id:   ID of the Confirmed order to pay against (required).
            amount:           Payment amount as decimal string, e.g. "150.00" (required, > 0).
            payment_method:   How payment was received (required). One of:
                              cash, mobile_payment, bank_transfer, card,
                              check, other.
            reference_number: External reference, e.g. cheque number or bank transaction ID.
            notes:            Optional internal notes about this payment.
            status:           Optional payment status: confirmed or pending_review.
            transaction_key:   Verified OCR transaction key for receipt methods.
            origin_phone:      Origin phone from receipt OCR/manual entry.
            origin_bank:       Issuing/origin bank from receipt OCR/manual entry.
            recipient_bank:    Recipient bank from receipt OCR/manual entry.
            recipient_account: Recipient account from receipt OCR/manual entry.
            ocr_receipt_data:  Raw OCR payload to store with the payment.
            receipt_image_path: Local receipt image path on the MCP server host.

        Returns the created Payment record including payment_number, amount,
        payment_method, and the order's new status.
        """
        payload = {
            "sales_order": sales_order_id,
            "amount": amount,
            "payment_method": payment_method,
            "reference_number": reference_number,
            "notes": notes,
            "status": status,
            "transaction_key": transaction_key,
            "origin_phone": origin_phone,
            "origin_bank": origin_bank,
            "recipient_bank": recipient_bank,
            "recipient_account": recipient_account,
            "ocr_receipt_data": ocr_receipt_data,
        }

        try:
            if receipt_image_path:
                receipt_file, mime_type = client.prepare_file_upload(
                    receipt_image_path,
                    allowed_mime_types=ALLOWED_RECEIPT_MIME_TYPES,
                )
                with receipt_file.open("rb") as fh:
                    return await client.post_multipart(
                        "/payments/",
                        data=payload,
                        files={"receipt_image": (receipt_file.name, fh, mime_type)},
                    )
            return await client.post("/payments/", payload)
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    # ------------------------------------------------------------------
    # Bulk transitions
    # ------------------------------------------------------------------

    @mcp.tool()
    async def retailops_bulk_confirm_orders(order_ids: list) -> dict:
        """
        Confirm multiple Pending orders in one request. Requires Manager or Admin role.

        Each order is processed independently. Partial success is normal — orders
        that cannot be confirmed (wrong status, no items, not found) are reported
        in the "failed" list without aborting the remaining orders.

        Side-effects per confirmed order:
          - Status changes from "pending" to "confirmed".
          - One negative InventoryMovement is created per line item (stock deducted).

        Args:
            order_ids: List of integer order IDs to confirm (required, non-empty).

        Returns:
            {
                "succeeded": [ <order object>, ... ],
                "failed":    [ {"id": <int>, "error": "<reason>"}, ... ]
            }
        """
        if not order_ids:
            raise ValueError("order_ids must be a non-empty list of integers.")
        try:
            return await client.post("/orders/bulk-transition/", {
                "order_ids": order_ids,
                "action": "confirm",
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_bulk_ship_orders(order_ids: list) -> dict:
        """
        Mark multiple Paid orders as Shipped in one request. Requires Manager or Admin role.

        Each order is processed independently. Orders not in "paid" status are
        reported in the "failed" list without aborting the remaining orders.

        Args:
            order_ids: List of integer order IDs to ship (required, non-empty).

        Returns:
            {
                "succeeded": [ <order object>, ... ],
                "failed":    [ {"id": <int>, "error": "<reason>"}, ... ]
            }
        """
        if not order_ids:
            raise ValueError("order_ids must be a non-empty list of integers.")
        try:
            return await client.post("/orders/bulk-transition/", {
                "order_ids": order_ids,
                "action": "ship",
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_bulk_deliver_orders(order_ids: list) -> dict:
        """
        Mark multiple Shipped orders as Delivered in one request. Requires Manager or Admin role.

        Each order is processed independently. Orders not in "shipped" status are
        reported in the "failed" list without aborting the remaining orders.

        Args:
            order_ids: List of integer order IDs to mark as delivered (required, non-empty).

        Returns:
            {
                "succeeded": [ <order object>, ... ],
                "failed":    [ {"id": <int>, "error": "<reason>"}, ... ]
            }
        """
        if not order_ids:
            raise ValueError("order_ids must be a non-empty list of integers.")
        try:
            return await client.post("/orders/bulk-transition/", {
                "order_ids": order_ids,
                "action": "deliver",
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())
