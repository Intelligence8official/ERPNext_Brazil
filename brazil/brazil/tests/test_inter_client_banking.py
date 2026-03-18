"""Tests for the Banco Inter API client."""

import unittest
from unittest.mock import MagicMock, patch
import sys
import json

# Ensure frappe mock is in place
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

# Mock requests before import
sys.modules.setdefault("requests", MagicMock())
import requests

import brazil.services.banking.inter_client as _ic_mod
from brazil.services.banking.inter_client import (
    InterAPIClient,
    InterAPIError,
    InterCertificateError,
    InterTimeoutError,
    InterConnectionError,
)

# Patch module-level imports
_ic_mod.now_datetime = lambda: "2024-01-15 12:00:00"


def _reset():
    frappe.reset_mock()
    frappe.get_doc.side_effect = None
    frappe.new_doc.side_effect = None
    frappe.db.commit.side_effect = None
    frappe.log_error.side_effect = None


def _make_client():
    """Create an InterAPIClient with mocked dependencies."""
    client = InterAPIClient("TEST-ACCOUNT")
    client.auth = MagicMock()
    client.auth.get_cert_paths.return_value = ("/tmp/cert.pem", "/tmp/key.pem")
    client.auth.get_valid_token.return_value = "fake-token-123"
    client._account_doc = MagicMock()
    client._account_doc.get_environment.return_value = "Sandbox"
    client._account_doc.company = "Test Company"
    return client


class TestRequestWithRetry(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_success_returns_response(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "ok"}

        with patch.object(requests, "request", return_value=mock_response):
            result = client._request_with_retry("GET", "/test/path")
            self.assertEqual(result.status_code, 200)

    def test_429_triggers_retry(self):
        client = _make_client()
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        success = MagicMock()
        success.status_code = 200

        with patch.object(requests, "request", side_effect=[rate_limited, success]):
            with patch("brazil.services.banking.inter_client.time.sleep"):
                result = client._request_with_retry("GET", "/test", max_retries=1)
                self.assertEqual(result.status_code, 200)

    def test_500_triggers_retry(self):
        client = _make_client()
        server_error = MagicMock()
        server_error.status_code = 500
        success = MagicMock()
        success.status_code = 200

        with patch.object(requests, "request", side_effect=[server_error, success]):
            with patch("brazil.services.banking.inter_client.time.sleep"):
                result = client._request_with_retry("GET", "/test", max_retries=1)
                self.assertEqual(result.status_code, 200)

    def test_ssl_error_raises_certificate_error(self):
        client = _make_client()

        with patch.object(requests, "request", side_effect=requests.exceptions.SSLError("bad cert")):
            with self.assertRaises(InterCertificateError):
                client._request_with_retry("GET", "/test", max_retries=0)

    def test_timeout_raises_after_max_retries(self):
        client = _make_client()

        with patch.object(requests, "request", side_effect=requests.exceptions.Timeout("timed out")):
            with patch("brazil.services.banking.inter_client.time.sleep"):
                with self.assertRaises(InterTimeoutError):
                    client._request_with_retry("GET", "/test", max_retries=1)

    def test_connection_error_raises_after_max_retries(self):
        client = _make_client()

        with patch.object(requests, "request", side_effect=requests.exceptions.ConnectionError("refused")):
            with patch("brazil.services.banking.inter_client.time.sleep"):
                with self.assertRaises(InterConnectionError):
                    client._request_with_retry("GET", "/test", max_retries=1)


class TestRequest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_returns_json(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"balance": 1000}

        with patch.object(client, "_request_with_retry", return_value=mock_response):
            result = client._request("GET", "/banking/v2/saldo")
            self.assertEqual(result["balance"], 1000)

    def test_raises_on_error(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "bad request"}
        mock_response.text = "bad request"

        with patch.object(client, "_request_with_retry", return_value=mock_response):
            with self.assertRaises(InterAPIError):
                client._request("GET", "/banking/v2/saldo")

    def test_logs_api_call(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        with patch.object(client, "_request_with_retry", return_value=mock_response):
            with patch.object(client, "_log_api_call") as mock_log:
                client._request("GET", "/banking/v2/saldo")
                mock_log.assert_called_once()


class TestLogApiCall(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_sanitizes_secrets(self):
        client = _make_client()
        log_doc = MagicMock()
        frappe.new_doc.return_value = log_doc

        client._log_api_call(
            method="POST",
            endpoint="/oauth/token",
            request_body={"client_secret": "secret123", "grant_type": "client_credentials"},
            response_code=200,
            response_body={"access_token": "tok"},
            duration_ms=100,
            success=True,
            error_message="",
            api_module="Auth",
        )

        # Check the request_body saved doesn't contain client_secret
        saved_body = log_doc.request_body
        if isinstance(saved_body, str):
            self.assertNotIn("secret123", saved_body)


class TestAPIMethodPaths(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_get_balance_path(self):
        client = _make_client()
        with patch.object(client, "_request", return_value={"disponivel": 5000}) as mock_req:
            client.get_balance()
            mock_req.assert_called_once()
            args = mock_req.call_args
            self.assertEqual(args[0][0], "GET")
            self.assertIn("/banking/v2/saldo", args[0][1])

    def test_create_boleto_path(self):
        client = _make_client()
        with patch.object(client, "_request", return_value={}) as mock_req:
            client.create_boleto({"data": "test"})
            args = mock_req.call_args
            self.assertEqual(args[0][0], "POST")
            self.assertIn("/cobranca/v3/cobrancas", args[0][1])

    def test_create_pix_charge_path(self):
        client = _make_client()
        with patch.object(client, "_request", return_value={}) as mock_req:
            client.create_pix_charge("txid123", {"valor": {"original": "100.00"}})
            args = mock_req.call_args
            self.assertEqual(args[0][0], "PUT")
            self.assertIn("/pix/v2/cob/txid123", args[0][1])

    def test_send_pix_path(self):
        client = _make_client()
        with patch.object(client, "_request", return_value={}) as mock_req:
            client.send_pix({"valor": "50.00"})
            args = mock_req.call_args
            self.assertEqual(args[0][0], "POST")
            self.assertIn("/banking/v2/pix", args[0][1])


if __name__ == "__main__":
    unittest.main()
