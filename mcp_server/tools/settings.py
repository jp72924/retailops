"""
mcp_server/tools/settings.py
-----------------------------
System settings tools (2 tools).

SystemSettings is a singleton row exposed by /api/v1/settings/. The MCP
layer delegates all persistence and business validation to the API while
performing small local checks for obvious malformed input.
"""

from decimal import Decimal, InvalidOperation
from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError


OCR_METHODS = {"mobile_payment", "bank_transfer"}


def _positive_decimal(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a numeric string.") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    return str(parsed)


def _positive_int(value: Optional[int], field_name: str) -> Optional[int]:
    if value is not None and value <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    return value


def register_settings_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_get_system_settings() -> dict:
        """
        Retrieve the current system-wide settings. Any authenticated role.
        """
        try:
            return await client.get("/settings/")
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_update_system_settings(
        currency_code: Optional[str] = None,
        currency_symbol: Optional[str] = None,
        decimal_places: Optional[int] = None,
        secondary_currency_enabled: Optional[bool] = None,
        secondary_currency_code: Optional[str] = None,
        secondary_currency_symbol: Optional[str] = None,
        secondary_decimal_places: Optional[int] = None,
        secondary_exchange_rate: Optional[str] = None,
        ocr_enabled: Optional[bool] = None,
        ocr_provider: Optional[str] = None,
        ocr_base_url: Optional[str] = None,
        ocr_api_key: Optional[str] = None,
        ocr_timeout_seconds: Optional[int] = None,
        ocr_max_file_mb: Optional[int] = None,
        ocr_strict_amount: Optional[bool] = None,
        ocr_require_complete: Optional[bool] = None,
        ocr_enabled_methods: Optional[list] = None,
        receipt_image_required_for_receipt_methods: Optional[bool] = None,
        delete_receipt_image_after_days: Optional[int] = None,
    ) -> dict:
        """
        Partial-update all current SystemSettings fields. Requires Manager+.

        Pass ocr_api_key="" to clear the stored OCR key. Omit ocr_api_key
        (None) to leave it unchanged.
        """
        if secondary_exchange_rate is not None:
            secondary_exchange_rate = _positive_decimal(
                secondary_exchange_rate,
                "secondary_exchange_rate",
            )

        _positive_int(ocr_timeout_seconds, "ocr_timeout_seconds")
        _positive_int(ocr_max_file_mb, "ocr_max_file_mb")
        _positive_int(delete_receipt_image_after_days, "delete_receipt_image_after_days")

        if ocr_enabled_methods is not None:
            if not isinstance(ocr_enabled_methods, list):
                raise ValueError("ocr_enabled_methods must be a list.")
            unsupported = sorted(set(ocr_enabled_methods) - OCR_METHODS)
            if unsupported:
                allowed = ", ".join(sorted(OCR_METHODS))
                raise ValueError(
                    f"Unsupported OCR method(s): {', '.join(unsupported)}. "
                    f"Allowed methods: {allowed}."
                )

        payload = {
            "currency_code": currency_code,
            "currency_symbol": currency_symbol,
            "decimal_places": decimal_places,
            "secondary_currency_enabled": secondary_currency_enabled,
            "secondary_currency_code": secondary_currency_code,
            "secondary_currency_symbol": secondary_currency_symbol,
            "secondary_decimal_places": secondary_decimal_places,
            "secondary_exchange_rate": secondary_exchange_rate,
            "ocr_enabled": ocr_enabled,
            "ocr_provider": ocr_provider,
            "ocr_base_url": ocr_base_url,
            "ocr_api_key": ocr_api_key,
            "ocr_timeout_seconds": ocr_timeout_seconds,
            "ocr_max_file_mb": ocr_max_file_mb,
            "ocr_strict_amount": ocr_strict_amount,
            "ocr_require_complete": ocr_require_complete,
            "ocr_enabled_methods": ocr_enabled_methods,
            "receipt_image_required_for_receipt_methods": receipt_image_required_for_receipt_methods,
            "delete_receipt_image_after_days": delete_receipt_image_after_days,
        }
        payload = {key: value for key, value in payload.items() if value is not None}

        if not payload:
            raise ValueError("At least one settings field must be provided.")

        try:
            return await client.patch("/settings/", payload)
        except RetailOpsError as e:
            raise ValueError(e.user_message())
