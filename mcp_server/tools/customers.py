"""
mcp_server/tools/customers.py
------------------------------
Customer CRUD tools (5 tools).

All tools require any authenticated role.
retailops_delete_customer fails with 409 if the customer has any orders
(on_delete=PROTECT guard enforced by the API).
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


def register_customer_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_list_customers(
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        List customers with optional full-text search and pagination.

        Args:
            search:    Searches across first_name, last_name, and email fields.
            page:      Page number (1-based). Defaults to 1.
            page_size: Results per page. Defaults to 25, maximum 100.

        Returns a paginated envelope: { count, next, previous, results: [...] }
        """
        try:
            return await client.get("/customers/", {
                "search": search,
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_customer(id: int) -> dict:
        """
        Retrieve a single customer by their database ID.

        Returns the full customer record including all address fields and notes.

        Args:
            id: The customer's integer primary key.
        """
        try:
            return await client.get(f"/customers/{id}/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_create_customer(
        first_name: str,
        last_name: str,
        email: str,
        phone: Optional[str] = None,
        address_line1: Optional[str] = None,
        address_line2: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        postal_code: Optional[str] = None,
        country: Optional[str] = "United States",
        notes: Optional[str] = None,
    ) -> dict:
        """
        Create a new customer record.

        Email must be unique across all customers.
        Country defaults to "United States" if not provided.

        Args:
            first_name:    Customer's first name (required).
            last_name:     Customer's last name (required).
            email:         Contact email — must be unique (required).
            phone:         Phone number.
            address_line1: Primary street address.
            address_line2: Suite, apartment, or building number.
            city:          City name.
            state:         State or province.
            postal_code:   ZIP or postal code.
            country:       Country name. Defaults to "United States".
            notes:         Internal notes visible only to staff.

        Returns the created customer object including the assigned integer ID.
        """
        try:
            return await client.post("/customers/", {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "address_line1": address_line1,
                "address_line2": address_line2,
                "city": city,
                "state": state,
                "postal_code": postal_code,
                "country": country,
                "notes": notes,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_update_customer(
        id: int,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        address_line1: Optional[str] = None,
        address_line2: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        postal_code: Optional[str] = None,
        country: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Update one or more fields on an existing customer (partial update).

        Only the fields you provide are changed; omitted fields are left as-is.

        Args:
            id: The customer's integer primary key (required).
            All other args are optional — only provided fields are updated.

        Returns the updated customer object.
        """
        try:
            return await client.patch(f"/customers/{id}/", {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "address_line1": address_line1,
                "address_line2": address_line2,
                "city": city,
                "state": state,
                "postal_code": postal_code,
                "country": country,
                "notes": notes,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_delete_customer(id: int) -> dict:
        """
        Permanently delete a customer record (hard delete).

        Fails with a 409 Conflict error if the customer has any associated
        sales orders — use retailops_list_orders with customer=<id> first
        to check. There is no soft-delete; this action is irreversible.

        Args:
            id: The customer's integer primary key.
        """
        try:
            await client.delete(f"/customers/{id}/")
            return {"message": f"Customer {id} deleted successfully."}
        except RetailOpsError as e:
            raise ValueError(e.user_message())
