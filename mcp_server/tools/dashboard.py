"""
mcp_server/tools/dashboard.py
------------------------------
Dashboard tool: business summary for the current month.
"""

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


def register_dashboard_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_get_dashboard() -> dict:
        """
        Retrieve a real-time business summary for the current month.

        Returns:
            orders_this_month      — total orders placed this calendar month.
            revenue_this_month     — sum of payments on Paid/Shipped/Delivered orders.
            pending_payments_count — number of Confirmed orders still awaiting payment.
            low_stock_count        — number of products at or below their low-stock threshold.
            recent_orders          — the 5 most recently created orders with key fields.
        """
        try:
            return await client.get("/dashboard/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())
