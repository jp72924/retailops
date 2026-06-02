"""
mcp_server/runtime_auth.py
--------------------------
Runtime authentication helpers for the RetailOps MCP layer.

The MCP server can run in two materially different modes:

- local/stdio: a single local client usually owns the process, so a successful
  retailops_login call may activate a token for subsequent tool calls.
- HTTP transports: every remote client must bring its own RetailOps API token
  as an MCP Bearer token; the token is validated before the MCP request runs.

The RetailOps REST API remains the source of truth for identity, roles, and
permissions. This module only chooses which DRF token should be forwarded to
the API for the current MCP request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from .config import settings


TokenSource = Literal["request", "local", "env", "none"]


@dataclass(frozen=True)
class TokenContext:
    token: str
    source: TokenSource
    identity: dict | None = None


_local_active_token: str = ""
_local_identity: dict | None = None


def is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower().strip("[]")
    return normalized in {"127.0.0.1", "localhost", "::1"}


def split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def set_local_api_token(token: str, identity: dict | None = None) -> None:
    global _local_active_token, _local_identity
    _local_active_token = (token or "").strip()
    _local_identity = dict(identity or {}) if identity else None


def clear_local_api_token() -> None:
    global _local_active_token, _local_identity
    _local_active_token = ""
    _local_identity = None


def get_local_identity() -> dict | None:
    return dict(_local_identity) if _local_identity else None


def current_token_context() -> TokenContext:
    request_token = get_access_token()
    if request_token and request_token.token:
        return TokenContext(token=request_token.token, source="request")

    if _local_active_token:
        return TokenContext(token=_local_active_token, source="local", identity=get_local_identity())

    env_token = (settings.api_token or "").strip()
    if env_token:
        return TokenContext(token=env_token, source="env")

    return TokenContext(token="", source="none")


def resolve_current_api_token() -> str:
    return current_token_context().token


class RetailOpsTokenVerifier:
    """Validate MCP Bearer tokens by checking them against RetailOps auth/me."""

    def __init__(self, *, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.base_url).rstrip("/") + "/"
        self.timeout = settings.timeout if timeout is None else timeout

    async def verify_token(self, token: str) -> AccessToken | None:
        token = (token or "").strip()
        if not token:
            return None

        identity = await self._fetch_identity(token)
        if not identity or identity.get("is_active") is False:
            return None

        email = identity.get("email") or "unknown"
        user_id = identity.get("user_id") or "unknown"
        role = (identity.get("role_name") or "").strip()
        scopes = ["retailops:access"]
        if role:
            scopes.append(f"retailops:role:{role.lower()}")

        return AccessToken(
            token=token,
            client_id=f"{user_id}:{email}",
            scopes=scopes,
        )

    async def _fetch_identity(self, token: str) -> dict | None:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            ) as http:
                response = await http.get(
                    "auth/me/",
                    headers={"Authorization": f"Token {token}"},
                )
        except httpx.HTTPError:
            return None

        if response.status_code in {401, 403}:
            return None
        if not response.is_success:
            return None

        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
