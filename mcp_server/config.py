"""
mcp_server/config.py
--------------------
Configuration for the RetailOps MCP Server.

All values come from environment variables so no secrets live in code.
For local development, place a .env file in the project root (next to manage.py).
The .env file is loaded automatically by python-dotenv when this module is imported.

Environment variables
---------------------
RETAILOPS_BASE_URL   — Base URL of the RetailOps REST API, no trailing slash.
                        Default: http://127.0.0.1:8000/api/v1
RETAILOPS_API_TOKEN  — Local fallback DRF token for stdio sessions.
                        Local fallback for stdio sessions. HTTP MCP clients should
                        send their own RetailOps token as Authorization: Bearer <token>
                        when MCP_AUTH_MODE=retailops-token.
RETAILOPS_TIMEOUT    — HTTP request timeout in seconds. Default: 30
RETAILOPS_DEBUG      — Set to "true" to log raw request/response bodies. Default: false
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file:
# mcp_server/config.py → mcp_server/ → project root).
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)


@dataclass(frozen=True)
class Settings:
    """Immutable settings object populated once at import time."""

    base_url: str    # e.g. "http://127.0.0.1:8000/api/v1"
    api_token: str   # DRF token — empty string means unauthenticated (most tools will fail)
    timeout: float   # HTTP timeout in seconds
    debug: bool      # Log raw HTTP traffic when True


settings = Settings(
    base_url=os.environ.get("RETAILOPS_BASE_URL", "http://127.0.0.1:8000/api/v1").rstrip("/"),
    api_token=os.environ.get("RETAILOPS_API_TOKEN", ""),
    timeout=float(os.environ.get("RETAILOPS_TIMEOUT", "30")),
    debug=os.environ.get("RETAILOPS_DEBUG", "false").lower() == "true",
)
