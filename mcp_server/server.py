"""
mcp_server/server.py
---------------------
RetailOps MCP Server — entry point.

This module wires together the FastMCP server, the RetailOps API client,
all tool modules, all resource handlers, and all prompt definitions.
It also handles transport selection and graceful client shutdown.

Transport modes (set via MCP_TRANSPORT environment variable):
  stdio            — default; used by Claude Desktop and most local clients.
                     The process communicates over stdin/stdout.
  sse              — HTTP Server-Sent Events; allows multiple clients to connect
                     simultaneously. Endpoint: http://<host>:<port>/sse
  streamable-http  — newer HTTP transport (MCP spec 2025-03-26); preferred for
                     remote / production deployments.
                     Endpoint: http://<host>:<port>/mcp

Network settings (SSE / streamable-http only):
  MCP_HOST  — bind address. Default 127.0.0.1 (loopback only).
               Set to 0.0.0.0 to accept remote connections.
  MCP_PORT  — port number. Default 8001.

Authentication / remote safety:
  MCP_AUTH_MODE        — local (default) or retailops-token.
  MCP_PUBLIC_BASE_URL  — required https:// URL when binding to a non-loopback host.
  MCP_ALLOWED_HOSTS    — required host allow-list when binding remotely.
  MCP_ALLOWED_ORIGINS  — optional origin allow-list for browser HTTP clients.

Remote SSE / streamable-http clients authenticate to MCP with:
  Authorization: Bearer <RetailOps API token>

The token is verified against RetailOps /auth/me/ before the MCP request is
accepted, then forwarded to the API as Authorization: Token <token>. The API
remains the authority for all role and permission checks.

Usage
-----
  # stdio (Claude Desktop, MCP inspector)
  python -m mcp_server.server

  # SSE — local loopback
  MCP_TRANSPORT=sse python -m mcp_server.server

  # SSE — remote, behind a TLS reverse proxy
  MCP_TRANSPORT=sse MCP_HOST=0.0.0.0 MCP_AUTH_MODE=retailops-token \
    MCP_PUBLIC_BASE_URL=https://mcp.example.com \
    MCP_ALLOWED_HOSTS=mcp.example.com \
    python -m mcp_server.server

  # Streamable-HTTP — preferred for production
  MCP_TRANSPORT=streamable-http python -m mcp_server.server
"""

import logging
import os

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from .client import RetailOpsClient
from .config import settings
from .prompts.workflows import register_prompts
from .resources.handlers import register_resources
from .tools.auth import register_auth_tools
from .tools.categories import register_category_tools
from .tools.customers import register_customer_tools
from .tools.dashboard import register_dashboard_tools
from .tools.inventory import register_inventory_tools
from .tools.orders import register_order_tools
from .tools.payments import register_payment_tools
from .tools.products import register_product_tools
from .tools.roles import register_role_tools
from .tools.settings import register_settings_tools
from .tools.users import register_user_tools
from .runtime_auth import RetailOpsTokenVerifier, is_loopback_host, split_csv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Transport configuration
# ---------------------------------------------------------------------------
_transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
_host = os.environ.get("MCP_HOST", "127.0.0.1").strip()
_port = int(os.environ.get("MCP_PORT", "8001"))
_auth_mode = os.environ.get("MCP_AUTH_MODE", "local").strip().lower() or "local"
_public_base_url = os.environ.get("MCP_PUBLIC_BASE_URL", "").strip().rstrip("/")
_allowed_hosts = split_csv(os.environ.get("MCP_ALLOWED_HOSTS"))
_allowed_origins = split_csv(os.environ.get("MCP_ALLOWED_ORIGINS"))
_required_scopes = split_csv(os.environ.get("MCP_REQUIRED_SCOPES")) or ["retailops:access"]

if _auth_mode not in {"local", "retailops-token"}:
    raise RuntimeError('MCP_AUTH_MODE must be "local" or "retailops-token".')

_http_transport = _transport in {"sse", "streamable-http"}
_remote_bind = _http_transport and not is_loopback_host(_host)

if _remote_bind and _auth_mode != "retailops-token":
    raise RuntimeError(
        "Remote MCP transports require MCP_AUTH_MODE=retailops-token. "
        "Do not expose SSE or streamable-http with the shared environment token."
    )

if _remote_bind:
    if not _public_base_url.lower().startswith("https://"):
        raise RuntimeError(
            "Remote MCP transports require MCP_PUBLIC_BASE_URL with an https:// URL."
        )
    if not _allowed_hosts:
        raise RuntimeError(
            "Remote MCP transports require MCP_ALLOWED_HOSTS to prevent DNS rebinding."
        )

if not _public_base_url and _auth_mode == "retailops-token":
    public_host = "127.0.0.1" if _host in {"0.0.0.0", "::"} else _host
    if ":" in public_host and not public_host.startswith("["):
        public_host = f"[{public_host}]"
    _public_base_url = f"http://{public_host}:{_port}"

_auth_settings = None
_token_verifier = None
if _auth_mode == "retailops-token":
    _auth_settings = AuthSettings(
        issuer_url=_public_base_url,
        service_documentation_url=_public_base_url,
        required_scopes=_required_scopes,
        resource_server_url=_public_base_url,
    )
    _token_verifier = RetailOpsTokenVerifier()

_transport_security = None
if _remote_bind or _allowed_hosts or _allowed_origins:
    if not _allowed_origins and _public_base_url:
        _allowed_origins = [_public_base_url]
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=_allowed_origins,
    )

# FastMCP accepts host/port at construction; they are used by SSE and
# streamable-http transports. They are ignored in stdio mode.
mcp = FastMCP(
    name="RetailOps",
    instructions=(
        "You are connected to RetailOps — a unified retail and e-commerce order "
        "management system. You can manage customers, products, categories, sales orders, "
        "payments, and inventory. All business rules (role permissions, stock deductions, "
        "order lifecycle transitions) are enforced server-side. "
        "Use the retailops_* tools to read and write data, and the retailops_*_workflow "
        "prompts to guide multi-step operations."
    ),
    host=_host,
    port=_port,
    auth=_auth_settings,
    token_verifier=_token_verifier,
    transport_security=_transport_security,
)

# ---------------------------------------------------------------------------
# Shared API client (one instance, reused by all tool closures)
# ---------------------------------------------------------------------------
client = RetailOpsClient()

# ---------------------------------------------------------------------------
# Register tools (54 tools across 11 domains)
# ---------------------------------------------------------------------------
register_auth_tools(mcp, client)
register_role_tools(mcp, client)
register_dashboard_tools(mcp, client)
register_customer_tools(mcp, client)
register_category_tools(mcp, client)
register_product_tools(mcp, client)
register_inventory_tools(mcp, client)
register_order_tools(mcp, client)
register_payment_tools(mcp, client)
register_settings_tools(mcp, client)
register_user_tools(mcp, client)

# ---------------------------------------------------------------------------
# Register resources (14 URI handlers)
# ---------------------------------------------------------------------------
register_resources(mcp, client)

# ---------------------------------------------------------------------------
# Register prompts (5 guided workflow templates)
# ---------------------------------------------------------------------------
register_prompts(mcp)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting RetailOps MCP Server")
    logger.info("  Transport : %s", _transport)
    logger.info("  API URL   : %s", settings.base_url)
    logger.info("  Auth mode : %s", _auth_mode)
    if _auth_mode == "local":
        logger.info("  API token : runtime login / RETAILOPS_API_TOKEN fallback")
    else:
        logger.info("  API token : per-client MCP Bearer token")

    if _transport in ("sse", "streamable-http"):
        logger.info("  Listening : http://%s:%s", _host, _port)
        endpoint = "/sse" if _transport == "sse" else "/mcp"
        logger.info("  Endpoint  : http://%s:%s%s", _host, _port, endpoint)

    try:
        mcp.run(transport=_transport)
    finally:
        # Release the httpx connection pool on any exit
        anyio.run(client.close)
