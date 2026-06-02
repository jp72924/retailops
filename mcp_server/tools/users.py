"""
mcp_server/tools/users.py
--------------------------
User management tools (7 tools). All require Admin role.

The agent account configured in RETAILOPS_API_TOKEN must have role=Admin
for any of these tools to succeed.

Key constraints enforced server-side:
  - You cannot deactivate your own account (guard: self-deactivation blocked).
  - Password must be >= 8 characters.
  - Email must be unique across all users.
  - Users are soft-deleted (is_active=False), never hard-deleted.
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


def register_user_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_list_users(
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        List all user accounts with their roles and active status. Requires Admin role.

        Returns: id, email, first_name, last_name, role (nested object with name),
        role_name, is_active, is_staff, created_at, updated_at.

        Args:
            page:      Page number (1-based). Defaults to 1.
            page_size: Results per page. Defaults to 25, maximum 100.
        """
        try:
            return await client.get("/users/", {
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_user(id: int) -> dict:
        """
        Retrieve a single user account by ID. Requires Admin role.

        Password is never returned. Returns the same fields as retailops_list_users
        for a single user.

        Args:
            id: The user's integer primary key.
        """
        try:
            return await client.get(f"/users/{id}/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_create_user(
        email: str,
        password: str,
        role: int,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        is_active: bool = True,
    ) -> dict:
        """
        Create a new user account and assign a role. Requires Admin role.

        The password is hashed before storage and never returned.
        The email address is used as the login username and must be unique.

        Role IDs (seeded reference data):
          1 = Admin   — full access including user management and refunds
          2 = Manager — order confirmation/cancellation, inventory, catalog management
          3 = Staff   — order submission/shipping/delivery, read access

        Args:
            email:      Login email — must be unique across all users (required).
            password:   Initial password — minimum 8 characters (required).
            role:       Role ID to assign (required). Use retailops_list_roles
                        or check retailops_list_users to confirm current role IDs.
            first_name: User's first name.
            last_name:  User's last name.
            is_active:  Whether the account is active on creation. Defaults to True.

        Returns the created user object (without password).
        """
        try:
            return await client.post("/users/", {
                "email": email,
                "password": password,
                "role": role,
                "first_name": first_name,
                "last_name": last_name,
                "is_active": is_active,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_update_user(
        id: int,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        role: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> dict:
        """
        Update a user's profile fields (partial update). Requires Admin role.

        To change a password, use retailops_change_password instead.
        Only the fields you provide are changed.

        Args:
            id:         The user's integer primary key (required).
            email:      New email address (must remain unique).
            first_name: New first name.
            last_name:  New last name.
            role:       New role ID (1=Admin, 2=Manager, 3=Staff).
            is_active:  True to activate, False to deactivate (see also
                        retailops_deactivate_user for the explicit action).

        Returns the updated user object.
        """
        try:
            return await client.patch(f"/users/{id}/", {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "role": role,
                "is_active": is_active,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_change_password(
        id: int,
        new_password: str,
        confirm_password: str,
    ) -> dict:
        """
        Set a new password for a user account. Requires Admin role.

        The old password is NOT required (Admin override). The password is hashed;
        existing API tokens remain valid after the change (they are not revoked).

        Args:
            id:               The user's integer primary key (required).
            new_password:     New password — minimum 8 characters (required).
            confirm_password: Must match new_password exactly (required).

        Returns: { "message": "Password updated successfully." }
        """
        if new_password != confirm_password:
            raise ValueError("new_password and confirm_password do not match.")

        try:
            result = await client.post(f"/users/{id}/change-password/", {
                "new_password": new_password,
                "confirm_password": confirm_password,
            })
            return result or {"message": "Password updated successfully."}
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_deactivate_user(id: int) -> dict:
        """
        Deactivate a user account (soft delete). Requires Admin role.

        Sets is_active=False. The user can no longer log in. Their data,
        order history, and existing tokens remain (tokens become inoperable).
        Use retailops_reactivate_user to restore access.

        Cannot be used on your own account (server-side guard).

        Args:
            id: The user's integer primary key.

        Returns: { "message": "User deactivated." }
        """
        try:
            result = await client.post(f"/users/{id}/deactivate/")
            return result or {"message": f"User {id} deactivated successfully."}
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_reactivate_user(id: int) -> dict:
        """
        Restore access for a previously deactivated user account. Requires Admin role.

        Sets is_active=True. The user can log in again. They will need to
        obtain a new token (retailops_login) as previous tokens were not
        automatically re-enabled.

        Args:
            id: The user's integer primary key.

        Returns: { "message": "User reactivated." }
        """
        try:
            result = await client.post(f"/users/{id}/reactivate/")
            return result or {"message": f"User {id} reactivated successfully."}
        except RetailOpsError as e:
            raise ValueError(e.user_message())
