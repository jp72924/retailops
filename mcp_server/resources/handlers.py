"""
mcp_server/resources/handlers.py
----------------------------------
MCP Resource URI handlers — read-only browsing of RetailOps data.

Resources map retailops:// URIs to GET endpoints on the API.
They always return pretty-printed JSON (text/plain MIME type).
Use MCP Tools when you need filtering, pagination, or write access.

URI catalog (14 resources):
  retailops://settings               -> GET /settings/
  retailops://roles                  -> GET /roles/
  retailops://dashboard              → GET /dashboard/
  retailops://customers              → GET /customers/
  retailops://customers/{id}         → GET /customers/{id}/
  retailops://products               → GET /products/
  retailops://products/{id}          → GET /products/{id}/
  retailops://products/{id}/movements → GET /products/{id}/movements/
  retailops://categories             → GET /categories/
  retailops://orders                 → GET /orders/
  retailops://orders/{id}            → GET /orders/{id}/
  retailops://payments               → GET /payments/
  retailops://inventory              → GET /inventory/
  retailops://users                  → GET /users/  (Admin token only)
"""

import json

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


def _pretty(data: dict | list) -> str:
    """Serialize API response to indented JSON string."""
    return json.dumps(data, indent=2, ensure_ascii=False)


def _error_text(e: RetailOpsError) -> str:
    return json.dumps({"error": e.user_message(), "code": e.code, "status": e.status}, indent=2)


def register_resources(mcp: FastMCP, client: RetailOpsClient) -> None:

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    @mcp.resource("retailops://dashboard")
    async def resource_dashboard() -> str:
        """
        RetailOps business dashboard — current month summary.

        Returns orders_this_month, revenue_this_month, pending_payments_count,
        low_stock_count, and the 5 most recent orders.
        Use retailops_get_dashboard tool for programmatic access.
        """
        try:
            return _pretty(await client.get("/dashboard/"))
        except RetailOpsError as e:
            return _error_text(e)

    # ------------------------------------------------------------------
    # Settings and roles
    # ------------------------------------------------------------------

    @mcp.resource("retailops://settings")
    async def resource_settings() -> str:
        """
        RetailOps singleton system settings.
        """
        try:
            return _pretty(await client.get("/settings/"))
        except RetailOpsError as e:
            return _error_text(e)

    @mcp.resource("retailops://roles")
    async def resource_roles() -> str:
        """
        RetailOps roles reference data. Admin token required.
        """
        try:
            return _pretty(await client.get("/roles/"))
        except RetailOpsError as e:
            return _error_text(e)

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------

    @mcp.resource("retailops://customers")
    async def resource_customers() -> str:
        """
        RetailOps customer list — first page (25 results).

        Use the retailops_list_customers tool for search and pagination.
        """
        try:
            return _pretty(await client.get("/customers/"))
        except RetailOpsError as e:
            return _error_text(e)

    @mcp.resource("retailops://customers/{id}")
    async def resource_customer(id: str) -> str:
        """
        RetailOps customer record by ID.

        Returns the full customer object including all address and contact fields.
        """
        try:
            return _pretty(await client.get(f"/customers/{id}/"))
        except RetailOpsError as e:
            return _error_text(e)

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    @mcp.resource("retailops://products")
    async def resource_products() -> str:
        """
        RetailOps product catalog — first page (25 results) with live stock levels.

        Use the retailops_list_products tool for filtering by stock status,
        category, or search terms.
        """
        try:
            return _pretty(await client.get("/products/"))
        except RetailOpsError as e:
            return _error_text(e)

    @mcp.resource("retailops://products/{id}")
    async def resource_product(id: str) -> str:
        """
        RetailOps product by ID — includes live current_stock, is_low_stock,
        is_out_of_stock computed from inventory movement history.
        """
        try:
            return _pretty(await client.get(f"/products/{id}/"))
        except RetailOpsError as e:
            return _error_text(e)

    @mcp.resource("retailops://products/{id}/movements")
    async def resource_product_movements(id: str) -> str:
        """
        Inventory movement history for a specific product.

        Returns the first page (25) of movements — every stock change
        (deduction, addition, adjustment) recorded for this product.
        Use the retailops_get_product_movements tool for pagination.
        """
        try:
            return _pretty(await client.get(f"/products/{id}/movements/"))
        except RetailOpsError as e:
            return _error_text(e)

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    @mcp.resource("retailops://categories")
    async def resource_categories() -> str:
        """
        RetailOps product category tree — first page (25 results).

        Each entry shows the category name, description, parent relationship,
        display_name (full ancestry path), and direct subcategories.
        """
        try:
            return _pretty(await client.get("/categories/"))
        except RetailOpsError as e:
            return _error_text(e)

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    @mcp.resource("retailops://orders")
    async def resource_orders() -> str:
        """
        RetailOps sales orders — most recent 25.

        Each entry includes order_number, status, customer summary,
        total_amount, amount_paid, and amount_outstanding.
        Use the retailops_list_orders tool for status and date filtering.
        """
        try:
            return _pretty(await client.get("/orders/"))
        except RetailOpsError as e:
            return _error_text(e)

    @mcp.resource("retailops://orders/{id}")
    async def resource_order(id: str) -> str:
        """
        RetailOps sales order by ID — full detail view.

        Includes: header fields, all line items with nested product details,
        financial summary (subtotal, tax, discount, total, amount_paid,
        amount_outstanding), and metadata (created_by, confirmed_by, timestamps).
        """
        try:
            return _pretty(await client.get(f"/orders/{id}/"))
        except RetailOpsError as e:
            return _error_text(e)

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------

    @mcp.resource("retailops://payments")
    async def resource_payments() -> str:
        """
        RetailOps payment ledger — most recent 25 records.

        Each entry shows payment_number, sales_order, amount, payment_method,
        reference_number, and recorded_by.
        Use the retailops_list_payments tool for filtering by order or method.
        """
        try:
            return _pretty(await client.get("/payments/"))
        except RetailOpsError as e:
            return _error_text(e)

    # ------------------------------------------------------------------
    # Inventory movements
    # ------------------------------------------------------------------

    @mcp.resource("retailops://inventory")
    async def resource_inventory() -> str:
        """
        RetailOps inventory movement log — most recent 25 records.

        Every stock change (sale deduction, return, manual adjustment) across
        all products. Use the retailops_list_inventory_movements tool for
        product-level or date-range filtering.
        """
        try:
            return _pretty(await client.get("/inventory/"))
        except RetailOpsError as e:
            return _error_text(e)

    # ------------------------------------------------------------------
    # Users (Admin token required)
    # ------------------------------------------------------------------

    @mcp.resource("retailops://users")
    async def resource_users() -> str:
        """
        RetailOps user accounts — first 25 results. Admin token required.

        Returns id, email, first_name, last_name, role_name, is_active
        for each user. Passwords are never included.
        Returns an authentication or permission error if the agent token
        does not belong to an Admin account.
        """
        try:
            return _pretty(await client.get("/users/"))
        except RetailOpsError as e:
            return _error_text(e)
