"""Tests for InterAuthManager (OAuth2 + mTLS authentication for Banco Inter)."""

import unittest
from unittest.mock import MagicMock, patch
import sys
from datetime import datetime, timedelta

# Mock frappe and its submodules before importing the module under test
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils
frappe = sys.modules["frappe"]

# Mock requests module with real exception classes so except clauses work
_requests_mock = sys.modules.setdefault("requests", MagicMock())


class _SSLError(Exception):
    pass


class _ConnectionError(Exception):
    pass


class _Timeout(Exception):
    pass


_requests_mock.exceptions.SSLError = _SSLError
_requests_mock.exceptions.ConnectionError = _ConnectionError
_requests_mock.exceptions.Timeout = _Timeout

from brazil.services.banking.auth_manager import (
    InterAuthManager,
    InterAuthError,
    TOKEN_URL_PRODUCTION,
    TOKEN_URL_SANDBOX,
)

import brazil.services.banking.auth_manager as _am_mod


def _make_manager():
    """Create an InterAuthManager with a mocked account_doc."""
    mgr = InterAuthManager("Test Account")
    doc = MagicMock()
    doc.access_token = None
    doc.token_expiry = None
    doc.get_environment.return_value = "Sandbox"
    doc.client_id = "my-client-id"
    doc.get_client_secret_value.return_value = "my-secret"
    doc.certificate_file = "/private/files/cert.pem"
    doc.key_file = "/private/files/key.pem"
    mgr._account_doc = doc
    return mgr


class TestGetTokenUrl(unittest.TestCase):
    """Tests for InterAuthManager.get_token_url()."""

    def tearDown(self):
        frappe.get_doc.side_effect = None

    def test_production_url_when_env_is_production(self):
        """Should return the production token URL when environment is Production."""
        mgr = _make_manager()
        mgr._account_doc.get_environment.return_value = "Production"

        result = mgr.get_token_url()

        self.assertEqual(result, TOKEN_URL_PRODUCTION)

    def test_sandbox_url_when_env_is_not_production(self):
        """Should return the sandbox token URL when environment is not Production."""
        mgr = _make_manager()
        mgr._account_doc.get_environment.return_value = "Sandbox"

        result = mgr.get_token_url()

        self.assertEqual(result, TOKEN_URL_SANDBOX)


class TestGetValidToken(unittest.TestCase):
    """Tests for InterAuthManager.get_valid_token()."""

    def tearDown(self):
        frappe.get_doc.side_effect = None
        frappe.utils.get_datetime.side_effect = None
        frappe.utils.now_datetime.side_effect = None
        frappe.db.set_value.side_effect = None

    def test_cached_valid_token_returned(self):
        """A cached token that is not near expiry should be returned directly."""
        mgr = _make_manager()
        mgr._account_doc.access_token = "cached-token-abc"
        mgr._account_doc.token_expiry = "2099-12-31 23:59:59"

        now = datetime(2025, 1, 1, 12, 0, 0)
        expiry = datetime(2099, 12, 31, 23, 59, 59)
        frappe.utils.get_datetime.return_value = expiry
        frappe.utils.now_datetime.return_value = now

        result = mgr.get_valid_token()

        self.assertEqual(result, "cached-token-abc")

    @patch.object(InterAuthManager, "_request_new_token")
    @patch.object(InterAuthManager, "_cache_token")
    def test_expired_token_triggers_refresh(self, mock_cache, mock_request):
        """An expired cached token should trigger a new token request."""
        mgr = _make_manager()
        mgr._account_doc.access_token = "expired-token"
        mgr._account_doc.token_expiry = "2020-01-01 00:00:00"

        now = datetime(2025, 6, 1, 12, 0, 0)
        expiry = datetime(2020, 1, 1, 0, 0, 0)
        frappe.utils.get_datetime.return_value = expiry
        frappe.utils.now_datetime.return_value = now

        mock_request.return_value = {
            "access_token": "new-token-xyz",
            "expires_in": 3600,
        }

        result = mgr.get_valid_token()

        self.assertEqual(result, "new-token-xyz")
        mock_request.assert_called_once()
        mock_cache.assert_called_once_with("new-token-xyz", 3600)

    @patch.object(InterAuthManager, "_request_new_token")
    @patch.object(InterAuthManager, "_cache_token")
    def test_near_expiry_triggers_refresh(self, mock_cache, mock_request):
        """A token within the refresh buffer (5 min) should trigger refresh."""
        mgr = _make_manager()
        mgr._account_doc.access_token = "soon-expiring-token"
        mgr._account_doc.token_expiry = "2025-06-01 12:04:00"

        # now + 300s buffer = 12:05:00, which is >= expiry 12:04:00
        now = datetime(2025, 6, 1, 12, 0, 0)
        expiry = datetime(2025, 6, 1, 12, 4, 0)
        frappe.utils.get_datetime.return_value = expiry
        frappe.utils.now_datetime.return_value = now

        mock_request.return_value = {
            "access_token": "refreshed-token",
            "expires_in": 3600,
        }

        result = mgr.get_valid_token()

        self.assertEqual(result, "refreshed-token")
        mock_request.assert_called_once()

    @patch.object(InterAuthManager, "_request_new_token")
    @patch.object(InterAuthManager, "_cache_token")
    def test_no_cache_triggers_request(self, mock_cache, mock_request):
        """When no cached token exists, a new token should be requested."""
        mgr = _make_manager()
        mgr._account_doc.access_token = None
        mgr._account_doc.token_expiry = None

        mock_request.return_value = {
            "access_token": "fresh-token",
            "expires_in": 7200,
        }

        result = mgr.get_valid_token()

        self.assertEqual(result, "fresh-token")
        mock_request.assert_called_once()
        mock_cache.assert_called_once_with("fresh-token", 7200)


class TestRequestNewToken(unittest.TestCase):
    """Tests for InterAuthManager._request_new_token()."""

    def tearDown(self):
        frappe.get_doc.side_effect = None
        frappe.db.set_value.side_effect = None

    @patch.object(InterAuthManager, "get_cert_paths", return_value=("/tmp/cert.pem", "/tmp/key.pem"))
    def test_success_returns_token_data(self, mock_cert_paths):
        """A 200 response should return the parsed JSON token data."""
        mgr = _make_manager()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "tok-success",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "extrato.read",
        }

        _am_mod.requests.post = MagicMock(return_value=mock_response)

        result = mgr._request_new_token(["extrato.read"])

        self.assertEqual(result["access_token"], "tok-success")
        self.assertEqual(result["expires_in"], 3600)
        _am_mod.requests.post.assert_called_once()

    @patch.object(InterAuthManager, "get_cert_paths", return_value=("/tmp/cert.pem", "/tmp/key.pem"))
    def test_ssl_error_raises_inter_auth_error(self, mock_cert_paths):
        """An SSLError during the request should raise InterAuthError."""
        mgr = _make_manager()

        _am_mod.requests.post = MagicMock(
            side_effect=_SSLError("SSL handshake failed")
        )

        with self.assertRaises(InterAuthError) as ctx:
            mgr._request_new_token(["extrato.read"])
        self.assertIn("SSL", str(ctx.exception))

    @patch.object(InterAuthManager, "get_cert_paths", return_value=("/tmp/cert.pem", "/tmp/key.pem"))
    def test_connection_error_raises_inter_auth_error(self, mock_cert_paths):
        """A ConnectionError during the request should raise InterAuthError."""
        mgr = _make_manager()

        _am_mod.requests.post = MagicMock(
            side_effect=_ConnectionError("Connection refused")
        )

        with self.assertRaises(InterAuthError) as ctx:
            mgr._request_new_token(["extrato.read"])
        self.assertIn("Connection error", str(ctx.exception))

    @patch.object(InterAuthManager, "get_cert_paths", return_value=("/tmp/cert.pem", "/tmp/key.pem"))
    def test_non_200_raises_inter_auth_error(self, mock_cert_paths):
        """A non-200 HTTP response should raise InterAuthError with status code."""
        mgr = _make_manager()

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"error": "invalid_client"}
        _am_mod.requests.post = MagicMock(return_value=mock_response)

        with self.assertRaises(InterAuthError) as ctx:
            mgr._request_new_token(["extrato.read"])
        self.assertIn("401", str(ctx.exception))


class TestValidateCredentials(unittest.TestCase):
    """Tests for InterAuthManager.validate_credentials()."""

    def tearDown(self):
        frappe.get_doc.side_effect = None
        frappe.db.set_value.side_effect = None

    @patch.object(InterAuthManager, "_request_new_token")
    def test_success_returns_status_dict(self, mock_request):
        """Successful validation should return a dict with status=success."""
        mgr = _make_manager()

        mock_request.return_value = {
            "access_token": "valid-tok",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "extrato.read",
        }

        result = mgr.validate_credentials()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["token_type"], "Bearer")
        self.assertEqual(result["expires_in"], 3600)
        self.assertEqual(result["scope"], "extrato.read")
        mock_request.assert_called_once_with(["extrato.read"])

    @patch.object(InterAuthManager, "_request_new_token")
    def test_failure_returns_error_dict(self, mock_request):
        """When _request_new_token raises, validate_credentials should return error dict."""
        mgr = _make_manager()

        mock_request.side_effect = InterAuthError("SSL certificate expired")

        result = mgr.validate_credentials()

        self.assertEqual(result["status"], "error")
        self.assertIn("SSL certificate expired", result["message"])


if __name__ == "__main__":
    unittest.main()
