"""
mcp_server/tools/categories.py
--------------------------------
Product category CRUD tools (5 tools).

Read operations (list, get) require any authenticated role.
Write operations (create, update, delete) require Manager or Admin.
Delete fails with 409 if any products are assigned to the category
(on_delete=PROTECT guard enforced by the API).
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


def register_category_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_list_categories(
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        List all product categories including their hierarchy relationships.

        Each category includes: id, name, description, parent_category (ID),
        display_name (e.g. "Electronics › Phones"), and a subcategories list.

        Args:
            page:      Page number (1-based). Defaults to 1.
            page_size: Results per page. Defaults to 25, maximum 100.
        """
        try:
            return await client.get("/categories/", {
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_category(id: int) -> dict:
        """
        Retrieve a single product category by ID.

        Returns the full category object including its subcategories list
        and display_name (which shows the full ancestry path).

        Args:
            id: The category's integer primary key.
        """
        try:
            return await client.get(f"/categories/{id}/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_create_category(
        name: str,
        description: Optional[str] = None,
        parent_category: Optional[int] = None,
    ) -> dict:
        """
        Create a new product category. Requires Manager or Admin role.

        Categories can be nested: provide parent_category to place this
        category under an existing one. The name must be unique globally.

        Args:
            name:            Category name — must be unique (required).
            description:     Optional text description.
            parent_category: ID of the parent category for nested hierarchies.

        Returns the created category object including its assigned ID.
        """
        try:
            return await client.post("/categories/", {
                "name": name,
                "description": description,
                "parent_category": parent_category,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_update_category(
        id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        parent_category: Optional[int] = None,
    ) -> dict:
        """
        Update a category's name, description, or parent (partial update).
        Requires Manager or Admin role.

        Only the fields you provide are changed; omitted fields are left as-is.
        A category cannot be set as its own parent (API enforces this).

        Args:
            id:              The category's integer primary key (required).
            name:            New category name (must remain unique).
            description:     New description text.
            parent_category: New parent category ID, or null to make it a root category.

        Returns the updated category object.
        """
        try:
            return await client.patch(f"/categories/{id}/", {
                "name": name,
                "description": description,
                "parent_category": parent_category,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_delete_category(id: int) -> dict:
        """
        Delete a product category. Requires Manager or Admin role.

        Fails with a 409 Conflict error if any products are currently
        assigned to this category (on_delete=PROTECT). Reassign those
        products to another category first using retailops_update_product.

        Args:
            id: The category's integer primary key.
        """
        try:
            await client.delete(f"/categories/{id}/")
            return {"message": f"Category {id} deleted successfully."}
        except RetailOpsError as e:
            raise ValueError(e.user_message())
