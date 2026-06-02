"""
mcp_server/tools/roles.py
--------------------------
Read-only role tools.

Roles are seeded reference data in RetailOps and are needed by agents when
creating or updating user accounts.
"""

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


def register_role_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_list_roles(
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        List all roles. Requires Admin role.
        """
        try:
            return await client.get("/roles/", {
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_role(id: int) -> dict:
        """
        Retrieve a single role by ID. Requires Admin role.
        """
        try:
            return await client.get(f"/roles/{id}/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())
