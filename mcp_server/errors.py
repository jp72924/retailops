"""
mcp_server/errors.py
--------------------
Error handling for the RetailOps MCP Server.

The RetailOps API always returns errors in this envelope:
    { "error": "...", "code": "...", "details": { ... } }

This module provides:
  - RetailOpsError -typed exception that carries status, code, and details.
  - raise_for_status -converts any non-2xx httpx.Response to RetailOpsError.
  - RetailOpsError.user_message() -returns a concise, human-readable string
    suitable for returning directly from an MCP tool.
"""

import httpx


class RetailOpsError(Exception):
    """
    Raised whenever the RetailOps API returns a non-2xx response.

    Attributes
    ----------
    status  : HTTP status code (e.g. 400, 403, 404, 409, 500)
    error   : Human-readable error message from the API envelope
    code    : Machine-readable error code from the API envelope
    details : Field-level validation errors or extra context (may be empty)
    """

    def __init__(
        self,
        status: int,
        error: str,
        code: str,
        details: dict | None = None,
        payload: dict | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.code = code
        self.details = details or {}
        self.payload = payload or {}
        super().__init__(f"[{status}] {code}: {error}")

    def user_message(self) -> str:
        """
        Return a concise, human-readable error message for MCP tool responses.

        Maps common HTTP/API error codes to actionable messages.
        Falls back to the raw API error string for unknown codes.
        """
        if self.status == 401 or self.code == "authentication_failed":
            return (
                "Authentication failed. "
                "Check the active MCP token: HTTP clients must send Authorization: Bearer <RetailOps API token>, "
                "while local stdio sessions use retailops_login or RETAILOPS_API_TOKEN."
            )

        if self.status == 403 or self.code == "permission_denied":
            return (
                "Permission denied. "
                "The MCP agent account does not have the required role for this action. "
                "Review the Capability Matrix in MCP_DESIGN.md to see which role is needed."
            )

        if self.status == 404 or self.code == "not_found":
            return f"Not found: the requested resource does not exist. Check the ID you provided. ({self.error})"

        if self.status == 409 or self.code == "conflict":
            return (
                f"Conflict: {self.error} "
                "This record cannot be deleted because other records depend on it."
            )

        if self.status == 400:
            # Validation error -try to surface field-level details
            if self.details:
                field_errors = "; ".join(
                    f"{field}: {', '.join(errs) if isinstance(errs, list) else errs}"
                    for field, errs in self.details.items()
                )
                return f"Validation failed -{field_errors}"
            return f"Validation failed -{self.error}"

        if self.status == 500 or self.code == "server_error":
            return (
                "RetailOps server error (HTTP 500). "
                "Check the Django development server logs for a traceback."
            )

        # Generic fallback -include enough info for debugging
        return f"{self.error} (HTTP {self.status}, code={self.code})"


def raise_for_status(response: httpx.Response) -> None:
    """
    Inspect an httpx.Response and raise RetailOpsError for non-2xx status codes.

    Attempts to parse the standard API error envelope
    ``{"error": "...", "code": "...", "details": {...}}``.
    Falls back to raw response text if the body is not valid JSON.
    """
    if response.is_success:
        return

    try:
        body = response.json()
        raise RetailOpsError(
            status=response.status_code,
            error=body.get("error", "Unknown error"),
            code=body.get("code", "unknown"),
            details=body.get("details"),
            payload=body,
        )
    except (ValueError, KeyError):
        # Response body is not JSON (e.g. HTML error page from a proxy)
        raise RetailOpsError(
            status=response.status_code,
            error=response.text or f"HTTP {response.status_code}",
            code="http_error",
        )
