"""
mcp_server/tools/auth.py
------------------------
Authentication tools for local and remote MCP sessions.

retailops_login   — obtains a RetailOps API token; activates it only in stdio.
retailops_whoami  — shows the identity behind the currently effective token.
retailops_logout  — safely revokes the current effective token.
"""

import os

from mcp.server.fastmcp import FastMCP

from ..client import RetailOpsClient
from ..errors import RetailOpsError
from ..runtime_auth import (
    clear_local_api_token,
    current_token_context,
    set_local_api_token,
)


def register_auth_tools(mcp: FastMCP, client: RetailOpsClient) -> None:

    @mcp.tool()
    async def retailops_login(email: str, password: str, activate: bool = True) -> dict:
        """
        Obtain a RetailOps API token for any user account.

        Calls the public token endpoint — no existing token is required.
        In stdio mode, activate=True also makes that token the effective token
        for subsequent tool calls in this MCP process. In HTTP transports, the
        returned token must be sent by the MCP client as Authorization: Bearer
        <token>; login does not mutate process-wide identity for remote clients.

        Args:
            email:    The user's email address.
            password: The user's password.
            activate: Whether to activate the returned token for stdio sessions.
        """
        try:
            result = await client.post_anon(
                "/auth/token/",
                {"email": email, "password": password},
            )
            transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
            activated = bool(activate and transport == "stdio")
            if activated:
                set_local_api_token(result["token"], result)

            return {
                **result,
                "activated": activated,
                "token_source": "local_session" if activated else "returned_only",
                "message": (
                    "Token activated for this local MCP process."
                    if activated
                    else "Token returned. For HTTP MCP transports, reconnect or send it as Authorization: Bearer <token>."
                ),
            }
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_whoami() -> dict:
        """
        Return the RetailOps user identity for the currently effective MCP token.

        Token source priority is: HTTP request Bearer token, locally activated
        stdio token, then RETAILOPS_API_TOKEN from the environment.
        """
        context = current_token_context()
        if context.source == "none":
            raise ValueError(
                "No RetailOps token is active. Use retailops_login in stdio mode "
                "or configure RETAILOPS_API_TOKEN / HTTP Bearer auth."
            )
        try:
            result = await client.get("/auth/me/")
            return {**result, "token_source": context.source}
        except RetailOpsError as e:
            raise ValueError(e.user_message())

    @mcp.tool()
    async def retailops_logout(revoke_env_token: bool = False) -> dict:
        """
        Revoke the currently effective RetailOps API token.

        By default this refuses to revoke RETAILOPS_API_TOKEN from the environment
        because that can break the whole MCP process until the environment is
        updated. Pass revoke_env_token=True to do that intentionally.
        """
        context = current_token_context()
        if context.source == "none":
            raise ValueError("No RetailOps token is active, so there is nothing to revoke.")
        if context.source == "env" and not revoke_env_token:
            raise ValueError(
                "Refusing to revoke RETAILOPS_API_TOKEN from the environment. "
                "Pass revoke_env_token=True if you really want to invalidate it."
            )

        try:
            await client.post("/auth/token/revoke/")
            if context.source == "local":
                clear_local_api_token()
            return {
                "message": "Token revoked successfully.",
                "token_source": context.source,
                "env_token_still_configured": context.source == "env",
            }
        except RetailOpsError as e:
            raise ValueError(e.user_message())
