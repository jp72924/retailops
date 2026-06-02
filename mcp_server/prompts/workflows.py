"""
mcp_server/prompts/workflows.py
---------------------------------
MCP Prompt definitions: guided workflow templates.

Prompts do not call the API. They return structured instruction text that an
AI agent can use to call RetailOps tools in the correct order.
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:

    @mcp.prompt()
    async def retailops_create_order_workflow(
        customer_hint: Optional[str] = None,
        product_hints: Optional[str] = None,
    ) -> list[dict]:
        """Guide an AI agent through creating and submitting a sales order."""
        steps = f"""
You are helping a user create a RetailOps sales order. Work through these steps in order:

STEP 1 - Find or create the customer
  Call retailops_list_customers with search="{customer_hint or '(ask the user for a name or email)'}".
  If a matching customer is found, confirm their details with the user.
  If no match is found, offer to create a new customer using retailops_create_customer.

STEP 2 - Select products
  Call retailops_list_products with stock="ok".
  Product hints to look for first: {product_hints or '(ask the user which products they need)'}.
  For each selected product, note product id, quantity, and unit price.

STEP 3 - Confirm the order details
  Show customer, line items, discount, tax, and notes. Ask the user to confirm or adjust.

STEP 4 - Create the order
  Call retailops_create_order with customer_id, items, discount_amount, tax_amount, and notes.

STEP 5 - Submit for manager review
  Ask whether to submit now. If yes, call retailops_submit_order.
  Confirm that the order is awaiting manager confirmation.
""".strip()
        return [{"role": "user", "content": steps}]

    @mcp.prompt()
    async def retailops_process_payment_workflow(order_id: int) -> list[dict]:
        """
        Guide an AI agent through recording payments, including OCR receipt
        verification for mobile payment and bank transfer.
        """
        steps = f"""
You are helping record a payment against RetailOps order ID {order_id}.
Work through these steps in order:

STEP 1 - Retrieve the order
  Call retailops_get_order with id={order_id}.
  Show order_number, status, total_amount, amount_paid, and amount_outstanding.
  If status is not "confirmed", stop:
    draft/pending -> the order must be confirmed before payment.
    paid -> already fully paid; use refund workflow if needed.
    cancelled/refunded -> no payment possible.

STEP 2 - Collect payment details
  Ask for amount, payment_method, reference_number, notes, and receipt_image_path.
  Allowed methods: cash, mobile_payment, bank_transfer, card, check, other.
  For mobile_payment or bank_transfer, collect reference_number, paid date,
  issuing/origin bank, and receipt_image_path when available.

STEP 3 - Verify receipt when OCR applies
  Call retailops_get_system_settings.
  If ocr_enabled=true and payment_method is in ocr_enabled_methods:
    1. Call retailops_check_receipt_ocr_health.
    2. Call retailops_verify_receipt with image_path, payment_method, sales_order_id={order_id},
       expected_reference, expected_paid_on, and expected_origin_bank when known.
    3. If valid=false or code=receipt_field_mismatch, show checks/details and stop.
    4. Keep checks.transaction_key and the returned vepay payload for the payment record.
  If OCR is disabled for a receipt method but receipt validation is required, ask for manager assistance
  or record with explicit manual override notes only if business policy allows it.

STEP 4 - Record the payment
  Call retailops_record_payment with:
    sales_order_id -> {order_id}
    amount -> decimal string, e.g. "150.00"
    payment_method -> selected method
    reference_number -> collected reference number
    notes -> collected notes or manual override reason
    receipt_image_path -> receipt image path for receipt methods
    transaction_key -> verified OCR transaction key when available
    ocr_receipt_data -> returned VEPay payload when available
    origin_bank/origin_phone/recipient_bank/recipient_account -> verified receipt fields when available

STEP 5 - Report the result
  Show payment_number, amount, payment_method, and the order's new status.
  If the order is now paid, say it is fully paid. If amount_outstanding remains, ask whether to record another payment.
""".strip()
        return [{"role": "user", "content": steps}]

    @mcp.prompt()
    async def retailops_cancel_or_refund_workflow(order_id: int) -> list[dict]:
        """Guide an AI agent through the correct reversal path for an order."""
        steps = f"""
You are helping reverse or cancel RetailOps order ID {order_id}.

STEP 1 - Retrieve the order
  Call retailops_get_order with id={order_id}.

STEP 2 - Choose by status
  draft or pending: edit with retailops_update_order or delete with retailops_delete_order after confirmation.
  confirmed: explain that cancellation restores stock, then call retailops_cancel_order if confirmed.
  paid: explain that refund restores stock and keeps payment records, then call retailops_refund_order if Admin.
  shipped or delivered: system reversal is not supported; use manual inventory adjustment for returned goods.
  cancelled or refunded: no further action needed.
""".strip()
        return [{"role": "user", "content": steps}]

    @mcp.prompt()
    async def retailops_stock_check_workflow() -> list[dict]:
        """Guide an AI agent through a full inventory health check."""
        steps = """
You are performing a RetailOps inventory health check.

STEP 1 - Dashboard summary
  Call retailops_get_dashboard and report low_stock_count and pending_payments_count.

STEP 2 - Zero-stock products
  Call retailops_list_products with stock="out" and list SKU, name, current_stock, and threshold.

STEP 3 - Low-stock products
  Call retailops_list_products with stock="low" and list SKU, name, current_stock, and threshold.

STEP 4 - Optional stock receipts
  If the user wants to record stock receipts, call retailops_adjust_inventory with positive quantity and notes.

STEP 5 - Summary
  Re-read adjusted products with retailops_get_product and report updated stock.
""".strip()
        return [{"role": "user", "content": steps}]

    @mcp.prompt()
    async def retailops_onboard_customer_workflow() -> list[dict]:
        """Guide an AI agent through onboarding a new customer."""
        steps = """
You are onboarding a new customer into RetailOps.

STEP 1 - Check for duplicates
  Ask for email, call retailops_list_customers with search=<email>, and reuse an existing match if appropriate.

STEP 2 - Collect customer details
  Required: first_name, last_name, email. Optional: phone, address, city, state, postal_code, country, notes.

STEP 3 - Create the customer
  Call retailops_create_customer and confirm the new ID.

STEP 4 - Offer first order
  If the user wants a first order, follow retailops_create_order_workflow with customer_hint=<email>.
""".strip()
        return [{"role": "user", "content": steps}]
