"""
mcp_server/tools/recipient_profiles.py
----------------------------------------
Recipient profile CRUD tools (5 tools).

A RecipientProfile is an admin-configured "known-good" recipient identity
(phone, bank, document/ID number) for a Mobile Payment or Bank Transfer
account. When SystemSettings.recipient_validation_enabled is on, kiosk
checkout and manual receipt verification reject any receipt whose
OCR-detected recipient doesn't match one of these profiles for that payment
method.

Unlike categories/products, every action here — including list and get —
requires Manager or Admin, since these records carry bank-account and
document identifiers used for fraud control.
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


PAYMENT_METHODS = {"mobile_payment", "bank_transfer"}


def _check_payment_method(payment_method: Optional[str]) -> None:
    if payment_method is not None and payment_method not in PAYMENT_METHODS:
        allowed = ", ".join(sorted(PAYMENT_METHODS))
        raise ValueError(
            f"Unsupported payment_method: {payment_method!r}. Allowed values: {allowed}."
        )


def register_recipient_profile_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_list_recipient_profiles(
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """
        List recipient profiles (the fraud-prevention allowlist for OCR-verified
        kiosk payments). Requires Manager or Admin role.

        Args:
            search:    Searches across label, phone, bank, and document_id fields.
            page:      Page number (1-based). Defaults to 1.
            page_size: Results per page. Defaults to 25, maximum 100.
        """
        try:
            return await client.get("/payment-recipient-profiles/", {
                "search": search,
                "page": page,
                "page_size": min(page_size, 100),
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_get_recipient_profile(id: int) -> dict:
        """
        Retrieve a single recipient profile by ID. Requires Manager or Admin role.

        Args:
            id: The profile's integer primary key.
        """
        try:
            return await client.get(f"/payment-recipient-profiles/{id}/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_create_recipient_profile(
        payment_method: str,
        phone: str,
        bank: str,
        document_id: str,
        label: Optional[str] = None,
    ) -> dict:
        """
        Create a recipient profile. Requires Manager or Admin role.

        The combination of payment_method, phone, bank, and document_id must
        be unique — creating a duplicate combination returns a validation error.
        New profiles are always active by default.

        Args:
            payment_method: "mobile_payment" or "bank_transfer" (required).
            phone:          Recipient phone number as it appears on receipts (required).
            bank:           Recipient bank name (required).
            document_id:    Recipient identification/RIF/document number (required).
            label:          Optional free-text name to help identify the profile
                            (e.g. "Main store — BDV").

        Returns the created profile object including the assigned integer ID.
        """
        _check_payment_method(payment_method)
        try:
            return await client.post("/payment-recipient-profiles/", {
                "payment_method": payment_method,
                "phone": phone,
                "bank": bank,
                "document_id": document_id,
                "label": label,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_update_recipient_profile(
        id: int,
        payment_method: Optional[str] = None,
        phone: Optional[str] = None,
        bank: Optional[str] = None,
        document_id: Optional[str] = None,
        label: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> dict:
        """
        Update one or more fields on a recipient profile (partial update).
        Requires Manager or Admin role.

        Only the fields you provide are changed; omitted fields are left as-is.
        The resulting payment_method+phone+bank+document_id combination must
        remain unique. Set is_active=False to deactivate a profile without
        deleting it — inactive profiles are kept for reference but are never
        matched against receipts.

        Args:
            id: The profile's integer primary key (required).
            All other args are optional — only provided fields are updated.

        Returns the updated profile object.
        """
        _check_payment_method(payment_method)
        try:
            return await client.patch(f"/payment-recipient-profiles/{id}/", {
                "payment_method": payment_method,
                "phone": phone,
                "bank": bank,
                "document_id": document_id,
                "label": label,
                "is_active": is_active,
            })
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_delete_recipient_profile(id: int) -> dict:
        """
        Permanently delete a recipient profile (hard delete). Requires Manager
        or Admin role. There is no soft-delete; this action is irreversible.
        To temporarily stop matching a profile without deleting it, use
        retailops_update_recipient_profile with is_active=False instead.

        Args:
            id: The profile's integer primary key.
        """
        try:
            await client.delete(f"/payment-recipient-profiles/{id}/")
            return {"message": f"Recipient profile {id} deleted successfully."}
        except RetailOpsError as e:
            raise ValueError(e.user_message())
