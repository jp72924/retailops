"""
mcp_server/tools/inventory.py
------------------------------
Inventory movement tools (3 tools).

Read operations (list, get) require any authenticated role.
retailops_adjust_inventory requires Manager or Admin.

InventoryMovement records are immutable once created — they form an
append-only audit log. Stock levels are always derived from the
sum of all movements for a product; they are never stored directly.
"""

from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


def register_inventory_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_list_inventory_movements(
        product: Optional[int] = None,
        movement_type: Optional[Literal["sale", "purchase", "adjustment", "return"]] = None,
        reference_type: Optional[Literal["SalesOrder", "PurchaseOrder", "ManualAdjustment", "Return"]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        Query the inventory movement log across all products.

        Every stock change in the system — from order confirmations, cancellations,
        refunds, and manual adjustments — is recorded here as an immutable row.
        Use this for full stock audit trails.

        Args:
            product:       Filter by product ID.
            movement_type: Filter by type: "sale" (deduction), "purchase" (addition),
                           "adjustment" (manual), "return" (reversal).
            reference_type: Filter by source document type.
            date_from:     Earliest date to include (YYYY-MM-DD).
            date_to:       Latest date to include (YYYY-MM-DD).
            page:          Page number (1-based). Defaults to 1.
            page_size:     Results per page. Defaults to 25, maximum 100.
        """
        try:
            return await client.get("/inventory/", {
                "product": product,
                "movement_type": movement_type,
                "reference_type": reference_type,
                "date_from": date_from,
                "date_to": date_to,
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_inventory_movement(id: int) -> dict:
        """
        Retrieve a single inventory movement record by ID.

        Returns the full movement record including: product (id, sku, name),
        movement_type, movement_type_display, quantity (signed integer),
        reference_type, reference_id, notes, created_by (full name), created_at.

        Args:
            id: The movement's integer primary key.
        """
        try:
            return await client.get(f"/inventory/{id}/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_adjust_inventory(
        product_id: int,
        quantity: int,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Record a manual stock adjustment. Requires Manager or Admin role.

        Creates an immutable InventoryMovement with movement_type="adjustment"
        and reference_type="ManualAdjustment". The adjustment immediately
        affects the product's current_stock (computed from movement sum).

        Use cases:
        - Receiving a stock purchase: positive quantity, e.g. quantity=50.
        - Writing off damaged goods: negative quantity, e.g. quantity=-5.
        - Correcting a stock count discrepancy.

        Args:
            product_id: The product's integer primary key (required).
            quantity:   Signed integer — positive adds stock, negative removes it.
                        Must be non-zero (required).
            notes:      Reason for the adjustment (recommended for audit trail).

        Returns the created InventoryMovement record.
        """
        if quantity == 0:
            raise ValueError("quantity must be non-zero. Use a positive value to add stock or negative to remove it.")

        try:
            return await client.post("/inventory/adjust/", {
                "product_id": product_id,
                "quantity": quantity,
                "notes": notes,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_bulk_adjust_inventory(adjustments: list) -> dict:
        """
        Record stock adjustments for multiple products in one request.
        Requires Manager or Admin role.

        Each adjustment is processed independently. Failures are collected and
        reported without aborting the remaining adjustments.

        Each entry in `adjustments` must be a dict with:
            product_id  (int, required)   — product primary key
            quantity    (int, required)   — non-zero; positive adds stock, negative removes
            notes       (str, optional)   — reason for adjustment (recommended)

        Example:
            adjustments = [
                {"product_id": 3, "quantity": 100, "notes": "Weekly restock"},
                {"product_id": 7, "quantity": -8,  "notes": "Damaged in transit"},
            ]

        Returns:
            {
                "succeeded": [ <InventoryMovement>, ... ],
                "failed":    [ {"product_id": <int>, "error": "<reason>"}, ... ]
            }
        """
        if not adjustments:
            raise ValueError("adjustments must be a non-empty list.")
        try:
            return await client.post("/inventory/bulk-adjust/", {
                "adjustments": adjustments,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())
