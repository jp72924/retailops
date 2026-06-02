"""
mcp_server/tools/products.py
-----------------------------
Product catalog tools (6 tools).

Read operations require any authenticated role.
Write operations (create, update, delete) require Manager or Admin.

Important: current_stock, is_low_stock, and is_out_of_stock are computed
from InventoryMovement aggregates. Stock only changes via order lifecycle
actions or retailops_adjust_inventory, never by editing the product directly.
"""

from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


def register_product_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_list_products(
        search: Optional[str] = None,
        category: Optional[int] = None,
        is_active: Optional[bool] = None,
        stock: Optional[Literal["out", "low", "ok"]] = None,
        unit_of_measure: Optional[Literal["piece", "kg", "liter", "meter", "box", "pack"]] = None,
        ordering: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        List products with live stock levels and optional filters.

        Results include product image metadata: image, external_image_url,
        primary_image_url, and has_image.
        """
        try:
            return await client.get("/products/", {
                "search": search,
                "category": category,
                "is_active": is_active,
                "stock": stock,
                "unit_of_measure": unit_of_measure,
                "ordering": ordering,
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_product(id: int) -> dict:
        """
        Retrieve a single product by ID with live stock level, category details,
        and image metadata.
        """
        try:
            return await client.get(f"/products/{id}/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_create_product(
        sku: str,
        name: str,
        category_id: int,
        unit_price: str,
        unit_of_measure: Literal["piece", "kg", "liter", "meter", "box", "pack"],
        description: Optional[str] = None,
        low_stock_threshold: int = 10,
        is_active: Optional[bool] = None,
        external_image_url: Optional[str] = None,
        image_path: Optional[str] = None,
    ) -> dict:
        """
        Create a new product in the catalog. Requires Manager or Admin role.

        Active products require either image_path or external_image_url. If
        is_active is omitted, this tool creates the product as active when an
        image source is supplied and inactive otherwise.
        """
        has_image_input = bool(image_path) or bool((external_image_url or "").strip())
        effective_is_active = has_image_input if is_active is None else is_active
        if effective_is_active and not has_image_input:
            raise ValueError(
                "Active products require image_path or external_image_url. "
                "Omit is_active to create the product inactive, or pass is_active=False."
            )

        payload = {
            "sku": sku,
            "name": name,
            "category_id": category_id,
            "unit_price": unit_price,
            "unit_of_measure": unit_of_measure,
            "description": description,
            "low_stock_threshold": low_stock_threshold,
            "is_active": effective_is_active,
            "external_image_url": external_image_url,
        }

        try:
            if image_path:
                image_file, mime_type = client.prepare_file_upload(image_path)
                with image_file.open("rb") as fh:
                    return await client.post_multipart(
                        "/products/",
                        data=payload,
                        files={"image": (image_file.name, fh, mime_type)},
                    )
            return await client.post("/products/", payload)
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_update_product(
        id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        category_id: Optional[int] = None,
        unit_price: Optional[str] = None,
        unit_of_measure: Optional[Literal["piece", "kg", "liter", "meter", "box", "pack"]] = None,
        low_stock_threshold: Optional[int] = None,
        is_active: Optional[bool] = None,
        external_image_url: Optional[str] = None,
        image_path: Optional[str] = None,
        clear_image: bool = False,
    ) -> dict:
        """
        Update product details (partial update). Requires Manager or Admin role.

        SKU cannot be changed after creation. Activating a product requires an
        existing image source, external_image_url, or image_path.
        """
        if is_active is True and not image_path and external_image_url is None:
            current = await client.get(f"/products/{id}/")
            if not current.get("has_image"):
                raise ValueError(
                    "Cannot activate product without an image source. "
                    "Provide image_path or external_image_url first."
                )
        if is_active is True and clear_image and not image_path and not (external_image_url or "").strip():
            raise ValueError(
                "Cannot activate product while clearing its uploaded image "
                "unless external_image_url or image_path is provided."
            )

        payload = {
            "name": name,
            "description": description,
            "category_id": category_id,
            "unit_price": unit_price,
            "unit_of_measure": unit_of_measure,
            "low_stock_threshold": low_stock_threshold,
            "is_active": is_active,
            "external_image_url": external_image_url,
            "clear_image": clear_image if clear_image else None,
        }

        try:
            if image_path:
                image_file, mime_type = client.prepare_file_upload(image_path)
                with image_file.open("rb") as fh:
                    return await client.patch_multipart(
                        f"/products/{id}/",
                        data=payload,
                        files={"image": (image_file.name, fh, mime_type)},
                    )
            return await client.patch(f"/products/{id}/", payload)
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_delete_product(id: int) -> dict:
        """
        Delete a product from the catalog. Requires Manager or Admin role.

        Fails with 409 Conflict if any sales order item references this
        product. Consider deactivating it instead.
        """
        try:
            await client.delete(f"/products/{id}/")
            return {"message": f"Product {id} deleted successfully."}
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_product_movements(
        id: int,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        Retrieve the full inventory movement history for a specific product.
        """
        try:
            return await client.get(f"/products/{id}/movements/", {
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())
