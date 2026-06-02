from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from mcp.server.auth.provider import AccessToken

from mcp_server.runtime_auth import (
    RetailOpsTokenVerifier,
    clear_local_api_token,
    current_token_context,
    set_local_api_token,
)


class RuntimeTokenResolutionTests(TestCase):
    def tearDown(self):
        clear_local_api_token()

    def test_request_bearer_token_wins_over_local_and_env_tokens(self):
        set_local_api_token("local-token", {"email": "local@example.com"})
        fake_settings = SimpleNamespace(api_token="env-token")
        request_token = AccessToken(
            token="request-token",
            client_id="client",
            scopes=["retailops:access"],
        )

        with (
            patch("mcp_server.runtime_auth.settings", fake_settings),
            patch("mcp_server.runtime_auth.get_access_token", return_value=request_token),
        ):
            context = current_token_context()

        self.assertEqual(context.token, "request-token")
        self.assertEqual(context.source, "request")

    def test_local_login_token_wins_over_env_token(self):
        set_local_api_token("local-token", {"email": "local@example.com"})
        fake_settings = SimpleNamespace(api_token="env-token")

        with (
            patch("mcp_server.runtime_auth.settings", fake_settings),
            patch("mcp_server.runtime_auth.get_access_token", return_value=None),
        ):
            context = current_token_context()

        self.assertEqual(context.token, "local-token")
        self.assertEqual(context.source, "local")
        self.assertEqual(context.identity, {"email": "local@example.com"})

    def test_env_token_is_final_fallback(self):
        fake_settings = SimpleNamespace(api_token="env-token")

        with (
            patch("mcp_server.runtime_auth.settings", fake_settings),
            patch("mcp_server.runtime_auth.get_access_token", return_value=None),
        ):
            context = current_token_context()

        self.assertEqual(context.token, "env-token")
        self.assertEqual(context.source, "env")


class RetailOpsTokenVerifierTests(IsolatedAsyncioTestCase):
    async def test_valid_retailops_token_returns_access_token_with_role_scope(self):
        verifier = RetailOpsTokenVerifier(base_url="http://example.test/api/v1", timeout=1)
        verifier._fetch_identity = AsyncMock(return_value={
            "user_id": 7,
            "email": "manager@example.com",
            "role_name": "Manager",
            "is_active": True,
        })

        access_token = await verifier.verify_token("retailops-token")

        self.assertIsNotNone(access_token)
        self.assertEqual(access_token.token, "retailops-token")
        self.assertEqual(access_token.client_id, "7:manager@example.com")
        self.assertIn("retailops:access", access_token.scopes)
        self.assertIn("retailops:role:manager", access_token.scopes)

    async def test_invalid_retailops_token_returns_none(self):
        verifier = RetailOpsTokenVerifier(base_url="http://example.test/api/v1", timeout=1)
        verifier._fetch_identity = AsyncMock(return_value=None)

        self.assertIsNone(await verifier.verify_token("bad-token"))
