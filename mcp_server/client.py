"""
mcp_server/client.py
--------------------
Async HTTP client wrapper for the RetailOps REST API.

All MCP tool modules import a single shared RetailOpsClient instance
(created in server.py). This keeps auth headers, base URL, timeout,
None-param filtering, and debug logging in one place.

Key design choices
------------------
- Two underlying httpx.AsyncClient instances:
    _http  — authenticated endpoints; Authorization is resolved per request.
    _anon  — no auth header; used only by the login (token-obtain) endpoint.
- Authenticated requests resolve the RetailOps API token in this order:
  MCP HTTP Bearer token, locally activated stdio login token, then
  RETAILOPS_API_TOKEN as the local fallback.
- Paths are normalised (leading slash stripped) so they resolve correctly
  against the base_url, which always ends with a slash.
- None-valued query params are stripped before the request is sent to
  avoid Django DRF misinterpreting the literal string "None".
- Debug logging prints method, path, and status code when RETAILOPS_DEBUG=true.
"""

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .errors import raise_for_status
from .runtime_auth import resolve_current_api_token

logger = logging.getLogger(__name__)


class RetailOpsClient:
    """
    Thin async wrapper around the RetailOps REST API (``/api/v1/``).

    Methods
    -------
    get(path, params)      → dict
    post(path, body)       → dict | None
    post_anon(path, body)  → dict          (no Authorization header)
    patch(path, body)      → dict
    delete(path)           → None

    Raise RetailOpsError for any non-2xx response.
    """

    def __init__(self) -> None:
        # Ensure base_url always ends with "/" so httpx resolves relative paths
        # correctly (e.g. "customers/" merges to base_url + "customers/").
        _base = settings.base_url.rstrip("/") + "/"

        _common_headers = {
            "Accept": "application/json",
        }

        self._http = httpx.AsyncClient(
            base_url=_base,
            headers=_common_headers,
            timeout=settings.timeout,
        )

        # Unauthenticated client — only used for POST /auth/token/ (login)
        self._anon = httpx.AsyncClient(
            base_url=_base,
            headers=_common_headers,
            timeout=settings.timeout,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_params(params: dict | None) -> dict:
        """Strip None values so they are not sent as the literal string 'None'."""
        if not params:
            return {}
        return {k: v for k, v in params.items() if v is not None}

    @staticmethod
    def _clean_body(body: dict | None) -> dict:
        """Strip None values from a request body (for PATCH partial updates)."""
        if not body:
            return {}
        return {k: v for k, v in body.items() if v is not None}

    @staticmethod
    def _clean_multipart_data(data: dict | None) -> dict:
        """
        Strip None values and coerce values into multipart-friendly strings.

        httpx can serialize plain scalars directly, but converting bools and
        nested JSON explicitly keeps Django/DRF parsing predictable for form
        and multipart endpoints.
        """
        cleaned: dict[str, Any] = {}
        for key, value in (data or {}).items():
            if value is None:
                continue
            if isinstance(value, bool):
                cleaned[key] = "true" if value else "false"
            elif isinstance(value, (dict, list)):
                cleaned[key] = json.dumps(value)
            else:
                cleaned[key] = str(value)
        return cleaned

    @staticmethod
    def prepare_file_upload(
        path: str,
        *,
        allowed_mime_types: set[str] | None = None,
    ) -> tuple[Path, str]:
        """
        Resolve and validate a local file path for multipart upload.

        The returned Path should be opened by the caller and kept open for the
        duration of the httpx request.
        """
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"File not found: {path}") from exc

        if not resolved.is_file():
            raise ValueError(f"Path is not a file: {resolved}")

        mime_type, _ = mimetypes.guess_type(str(resolved))
        mime_type = mime_type or "application/octet-stream"
        if allowed_mime_types is not None and mime_type not in allowed_mime_types:
            allowed = ", ".join(sorted(allowed_mime_types))
            raise ValueError(
                f"Unsupported file type for {resolved.name}: {mime_type}. "
                f"Allowed types: {allowed}."
            )
        return resolved, mime_type

    @staticmethod
    def _norm(path: str) -> str:
        """Strip leading slash so the path resolves relative to base_url."""
        return path.lstrip("/")

    @staticmethod
    def _auth_headers() -> dict:
        """Build the per-request RetailOps API auth header from runtime context."""
        token = resolve_current_api_token()
        if not token:
            return {}
        return {"Authorization": f"Token {token}"}

    def _log(self, method: str, path: str, extra: Any = None) -> None:
        if settings.debug:
            msg = f"→ {method} {settings.base_url}/{path.lstrip('/')}"
            if extra:
                msg += f"  {json.dumps(extra)}"
            logger.debug(msg)

    def _log_response(self, response: httpx.Response) -> None:
        if settings.debug:
            logger.debug(f"← {response.status_code}")

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def get(self, path: str, params: dict | None = None) -> dict:
        """Send an authenticated GET request and return the parsed JSON body."""
        p = self._norm(path)
        cleaned = self._clean_params(params)
        self._log("GET", p, cleaned or None)
        r = await self._http.get(p, params=cleaned, headers=self._auth_headers())
        self._log_response(r)
        raise_for_status(r)
        return r.json()

    async def post(self, path: str, body: dict | None = None) -> dict | None:
        """
        Send an authenticated POST request.

        None values are stripped from the body before sending so that optional
        fields are absent rather than explicit JSON null — DRF serializers with
        allow_null=False (the default) reject null for optional fields.

        Returns the parsed JSON body, or None for 204 No Content responses
        (e.g. some action endpoints that return no body).
        """
        p = self._norm(path)
        b = self._clean_body(body)
        self._log("POST", p, b or None)
        r = await self._http.post(p, json=b, headers=self._auth_headers())
        self._log_response(r)
        raise_for_status(r)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    async def post_multipart(
        self,
        path: str,
        *,
        data: dict | None = None,
        files: dict | None = None,
    ) -> dict | None:
        """Send an authenticated multipart/form-data POST request."""
        p = self._norm(path)
        d = self._clean_multipart_data(data)
        self._log("POST(multipart)", p, d or None)
        r = await self._http.post(p, data=d, files=files or {}, headers=self._auth_headers())
        self._log_response(r)
        raise_for_status(r)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    async def post_anon(self, path: str, body: dict) -> dict:
        """
        Send an *unauthenticated* POST request.

        Used exclusively by the login tool (POST /auth/token/) which is a
        public endpoint — sending the agent token would authenticate as the
        wrong user.
        """
        p = self._norm(path)
        self._log("POST(anon)", p)
        r = await self._anon.post(p, json=body)
        self._log_response(r)
        raise_for_status(r)
        return r.json()

    async def patch(self, path: str, body: dict) -> dict:
        """
        Send an authenticated PATCH request.

        None values are stripped from the body so that only explicitly
        provided fields are sent to the API (true partial update semantics).
        """
        p = self._norm(path)
        b = self._clean_body(body)
        self._log("PATCH", p, b)
        r = await self._http.patch(p, json=b, headers=self._auth_headers())
        self._log_response(r)
        raise_for_status(r)
        return r.json()

    async def patch_multipart(
        self,
        path: str,
        *,
        data: dict | None = None,
        files: dict | None = None,
    ) -> dict:
        """Send an authenticated multipart/form-data PATCH request."""
        p = self._norm(path)
        d = self._clean_multipart_data(data)
        self._log("PATCH(multipart)", p, d or None)
        r = await self._http.patch(p, data=d, files=files or {}, headers=self._auth_headers())
        self._log_response(r)
        raise_for_status(r)
        return r.json()

    async def delete(self, path: str) -> None:
        """Send an authenticated DELETE request. Returns None on success (204)."""
        p = self._norm(path)
        self._log("DELETE", p)
        r = await self._http.delete(p, headers=self._auth_headers())
        self._log_response(r)
        raise_for_status(r)

    async def close(self) -> None:
        """Release underlying connection pools. Call on server shutdown."""
        await self._http.aclose()
        await self._anon.aclose()
