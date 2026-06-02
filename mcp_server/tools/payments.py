"""
mcp_server/tools/payments.py
------------------------------
Payment ledger and OCR receipt tools (4 tools).

retailops_record_payment lives in orders.py because its primary effect is
on order status. Payment records are immutable financial records.
"""

from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


ALLOWED_RECEIPT_MIME_TYPES = {"image/jpeg", "image/png", "image/heic", "image/heif"}


def register_payment_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_list_payments(
        sales_order: Optional[int] = None,
        payment_method: Optional[Literal["cash", "mobile_payment", "bank_transfer", "card", "check", "other"]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        Query the payment ledger with optional filters.

        payment_method may be cash, mobile_payment, bank_transfer, card,
        check, or other.
        """
        try:
            return await client.get("/payments/", {
                "sales_order": sales_order,
                "payment_method": payment_method,
                "date_from": date_from,
                "date_to": date_to,
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_payment(id: int) -> dict:
        """
        Retrieve a single immutable payment record by ID.
        """
        try:
            return await client.get(f"/payments/{id}/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_check_receipt_ocr_health() -> dict:
        """
        Check server-side connectivity to the configured OCR provider.

        Calls GET /payments/receipts/healthz/. Requires Manager or Admin role.
        """
        try:
            return await client.get("/payments/receipts/healthz/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_verify_receipt(
        image_path: str,
        payment_method: Literal["mobile_payment", "bank_transfer"],
        sales_order_id: Optional[int] = None,
        expected_amount_usd: Optional[str] = None,
        expected_reference: Optional[str] = None,
        expected_paid_on: Optional[str] = None,
        expected_origin_bank: Optional[str] = None,
    ) -> dict:
        """
        Parse and verify a receipt image through VEPay without creating a payment.

        Provide either sales_order_id or expected_amount_usd. For kiosk-style
        verification before an order exists, also pass expected_reference,
        expected_paid_on (YYYY-MM-DD), and expected_origin_bank when available.
        The API remains the authority for OCR, duplicate detection, and field
        matching; this tool returns the provider/check payload intact on success.
        """
        if sales_order_id is None and expected_amount_usd is None:
            raise ValueError(
                "Provide sales_order_id or expected_amount_usd when verifying a receipt."
            )

        data = {
            "payment_method": payment_method,
            "sales_order": sales_order_id,
            "expected_amount_usd": expected_amount_usd,
            "expected_reference": expected_reference,
            "expected_paid_on": expected_paid_on,
            "expected_origin_bank": expected_origin_bank,
        }

        receipt_file, mime_type = client.prepare_file_upload(
            image_path,
            allowed_mime_types=ALLOWED_RECEIPT_MIME_TYPES,
        )

        try:
            with receipt_file.open("rb") as fh:
                return await client.post_multipart(
                    "/payments/receipts/verify/",
                    data=data,
                    files={"image": (receipt_file.name, fh, mime_type)},
                )
        except RetailOpsError as e:
            if e.code == "receipt_field_mismatch":
                return e.payload
            raise ValueError(e.user_message())
