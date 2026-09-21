"""Tests for the Banco Inter API client.

Order-independent on purpose: a dozen test modules leave a ``MagicMock`` in
``sys.modules["requests"]``, and a ``MagicMock`` attribute in an ``except``
clause is a ``TypeError``, not a match. This file loads the REAL
``requests.exceptions`` and patches the client module's ``requests`` per test,
so the retry policy is exercised against the real exception hierarchy
(``ConnectTimeout`` is both a ``ConnectionError`` and a ``Timeout``).
"""

import importlib
import inspect
import sys
import unittest
import uuid
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Ensure frappe mock is in place
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils
# A module imported earlier may have installed the mock without ``frappe.utils``.
sys.modules.setdefault("frappe.utils", sys.modules["frappe"].utils)


def _load_real_requests_exceptions():
    """Import the real ``requests.exceptions`` without disturbing ``sys.modules``."""

    def _is_requests(key):
        return key == "requests" or key.startswith("requests.")

    saved = {key: sys.modules.pop(key) for key in list(sys.modules) if _is_requests(key)}
    try:
        return importlib.import_module("requests.exceptions")
    finally:
        for key in [key for key in sys.modules if _is_requests(key)]:
            del sys.modules[key]
        sys.modules.update(saved)


REAL_EXC = _load_real_requests_exceptions()

# Keep the suite-wide convention (a shared mock in sys.modules) for the other
# test modules; nothing in this file depends on it.
sys.modules.setdefault("requests", MagicMock())

# test_webhook_handler.py parks MagicMock placeholders for these modules when it is imported
# first; this file must exercise the real ones.
for _name in ("brazil_module.services.banking.auth_manager", "brazil_module.services.banking.inter_client"):
    if isinstance(sys.modules.get(_name), MagicMock):
        del sys.modules[_name]

import brazil_module.services.banking.inter_client as _ic_mod
from brazil_module.services.banking.auth_manager import InterAuthError
from brazil_module.services.banking.inter_client import (
    InterAmbiguousResultError,
    InterAPIClient,
    InterAPIError,
    InterCertificateError,
    InterConnectionError,
    InterTimeoutError,
)

# The mock the client module actually holds (identical to sys.modules["frappe"]
# unless another test module swapped it after the first import).
frappe = _ic_mod.frappe

# Patch module-level imports
_ic_mod.now_datetime = lambda: "2024-01-15 12:00:00"


def _reset():
    frappe.reset_mock()
    frappe.get_doc.side_effect = None
    frappe.new_doc.side_effect = None
    frappe.new_doc.return_value = MagicMock(name="Inter API Log")
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


def _resp(status_code, body=None):
    """A fake ``requests.Response``; ``body=None`` means a non-JSON body."""
    response = MagicMock(name=f"HTTP {status_code}")
    response.status_code = status_code
    response.text = "" if body is None else str(body)
    response.content = b""
    if body is None:
        response.json.side_effect = ValueError("no JSON")
    else:
        response.json.return_value = body
    return response


KEY = "0f8fad5b-d9cb-469f-a165-70867728950e"
PIX = {
    "valor": 16800.00,
    "descricao": "ACC-PINV-2026-00031",
    "destinatario": {"tipo": "CHAVE", "chave": "12345678000195"},
}
BOLETO = {
    "codBarraLinhaDigitavel": "03395988500000666539201493990000372830030102",
    "valorPagar": "666.53",
    "dataVencimento": "2026-09-30",
}

# spec 4.2: the bank does not hold the operation
DEFINITIVE_STATUSES = (400, 403, 404, 405, 406, 415, 422, 429)
# spec 4.2: everything else - the bank may hold it
AMBIGUOUS_STATUSES = (302, 307, 408, 409, 418, 500, 502, 503, 504)


def _ambiguous_exceptions():
    return (
        REAL_EXC.ReadTimeout("read timed out"),
        REAL_EXC.Timeout("timed out"),
        REAL_EXC.ConnectionError("Connection aborted"),
        REAL_EXC.SSLError("EOF occurred in violation of protocol"),
        REAL_EXC.ChunkedEncodingError("connection broken while reading the response"),
    )


class _ClientTestCase(unittest.TestCase):
    """Patches the client module's ``requests`` and ``time.sleep`` for every test."""

    def setUp(self):
        _reset()
        self.http = MagicMock(name="requests.request")
        requests_patch = patch.object(
            _ic_mod, "requests", SimpleNamespace(request=self.http, exceptions=REAL_EXC)
        )
        requests_patch.start()
        self.addCleanup(requests_patch.stop)
        sleep_patch = patch.object(_ic_mod.time, "sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.client = _make_client()


class TestRealExceptionsAreLoaded(unittest.TestCase):
    def test_real_hierarchy_not_a_mock(self):
        self.assertTrue(issubclass(REAL_EXC.ConnectTimeout, REAL_EXC.ConnectionError))
        self.assertTrue(issubclass(REAL_EXC.ConnectTimeout, REAL_EXC.Timeout))
        self.assertTrue(issubclass(REAL_EXC.SSLError, REAL_EXC.ConnectionError))
        self.assertFalse(issubclass(REAL_EXC.ReadTimeout, REAL_EXC.ConnectionError))

    def test_shared_requests_entry_was_restored(self):
        self.assertIsNot(sys.modules.get("requests.exceptions"), REAL_EXC)


class TestRequestWithRetry(_ClientTestCase):
    def test_success_returns_response(self):
        self.http.return_value = _resp(200, {"data": "ok"})

        result = self.client._request_with_retry("GET", "/test/path")

        self.assertEqual(result.status_code, 200)

    def test_429_triggers_retry(self):
        self.http.side_effect = [_resp(429), _resp(200, {})]

        result = self.client._request_with_retry("GET", "/test", max_retries=1)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.http.call_count, 2)

    def test_500_triggers_retry(self):
        self.http.side_effect = [_resp(500), _resp(200, {})]

        result = self.client._request_with_retry("GET", "/test", max_retries=1)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.http.call_count, 2)

    def test_ssl_error_raises_certificate_error(self):
        self.http.side_effect = REAL_EXC.SSLError("bad cert")

        with self.assertRaises(InterCertificateError):
            self.client._request_with_retry("GET", "/test", max_retries=0)

    def test_timeout_raises_after_max_retries(self):
        self.http.side_effect = REAL_EXC.Timeout("timed out")

        with self.assertRaises(InterTimeoutError):
            self.client._request_with_retry("GET", "/test", max_retries=1)
        self.assertEqual(self.http.call_count, 2)

    def test_connection_error_raises_after_max_retries(self):
        self.http.side_effect = REAL_EXC.ConnectionError("refused")

        with self.assertRaises(InterConnectionError):
            self.client._request_with_retry("GET", "/test", max_retries=1)
        self.assertEqual(self.http.call_count, 2)


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

        # No `if isinstance(...)`: behind a type guard the production code controls, storing the
        # body as a dict - secrets and all - would leave this test green.
        saved_body = log_doc.request_body
        self.assertIsInstance(saved_body, str)
        self.assertNotIn("secret123", saved_body)
        self.assertIn("client_credentials", saved_body, "the harmless part of the body must survive")


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
            client.send_pix({"valor": 50.00}, KEY)
            args = mock_req.call_args
            self.assertEqual(args[0][0], "POST")
            self.assertEqual(args[0][1], "/banking/v2/pix")
            self.assertIs(args[1]["retry_safe"], False)
            self.assertEqual(args[1]["extra_headers"], {"x-id-idempotente": KEY})

    def test_pay_barcode_path(self):
        client = _make_client()
        with patch.object(client, "_request", return_value={}) as mock_req:
            client.pay_barcode(BOLETO)
            args = mock_req.call_args
            self.assertEqual(args[0][0], "POST")
            self.assertEqual(args[0][1], "/banking/v2/pagamento")
            self.assertIs(args[1]["retry_safe"], False)
            self.assertNotIn("x-id-idempotente", args[1].get("extra_headers") or {})


class _PaymentPostPolicy:
    """spec 4.2 - a payment POST is sent once. Runs against send_pix and pay_barcode."""

    path = ""

    def send(self):
        raise NotImplementedError

    def _log_doc(self):
        frappe.new_doc.assert_called_once_with("Inter API Log")
        return frappe.new_doc.return_value

    def test_success_is_one_post_with_the_payload(self):
        self.http.return_value = _resp(200, {"codigoSolicitacao": "SOL-1"})

        result = self.send()

        self.assertEqual(result, {"codigoSolicitacao": "SOL-1"})
        self.assertEqual(self.http.call_count, 1)
        kwargs = self.http.call_args.kwargs
        self.assertEqual(kwargs["method"], "POST")
        self.assertTrue(kwargs["url"].endswith(self.path))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-token-123")

    def test_redirects_are_never_followed(self):
        self.http.return_value = _resp(200, {})

        self.send()

        self.assertIs(self.http.call_args.kwargs["allow_redirects"], False)

    def test_ambiguous_exception_is_one_send_and_ambiguous(self):
        for exc in _ambiguous_exceptions():
            with self.subTest(exc=type(exc).__name__):
                self.setUp()
                self.http.side_effect = exc

                with self.assertRaises(InterAmbiguousResultError) as ctx:
                    self.send()

                self.assertEqual(self.http.call_count, 1)
                self.assertIsNone(ctx.exception.status_code)
                self.sleep.assert_not_called()

    def test_ambiguous_status_is_one_send_and_ambiguous(self):
        for status in AMBIGUOUS_STATUSES:
            with self.subTest(status=status):
                self.setUp()
                self.http.return_value = _resp(status, {"title": "erro"})

                with self.assertRaises(InterAmbiguousResultError) as ctx:
                    self.send()

                self.assertEqual(self.http.call_count, 1)
                self.assertEqual(ctx.exception.status_code, status)
                self.assertEqual(ctx.exception.response_body, {"title": "erro"})
                self.sleep.assert_not_called()

    def test_definitive_status_is_one_send_and_not_ambiguous(self):
        for status in DEFINITIVE_STATUSES:
            with self.subTest(status=status):
                self.setUp()
                self.http.return_value = _resp(status, {"title": "Limite excedido [PIXP30]"})

                with self.assertRaises(InterAPIError) as ctx:
                    self.send()

                self.assertNotIsInstance(ctx.exception, InterAmbiguousResultError)
                self.assertEqual(ctx.exception.status_code, status)
                self.assertEqual(ctx.exception.response_body, {"title": "Limite excedido [PIXP30]"})
                self.assertEqual(self.http.call_count, 1)
                self.sleep.assert_not_called()

    def test_connect_timeout_is_retried_because_nothing_was_sent(self):
        self.http.side_effect = [REAL_EXC.ConnectTimeout("connect"), _resp(200, {"ok": 1})]

        result = self.send()

        self.assertEqual(result, {"ok": 1})
        self.assertEqual(self.http.call_count, 2)
        self.sleep.assert_not_called()

    def test_connect_timeout_exhausted_is_definitive(self):
        self.http.side_effect = REAL_EXC.ConnectTimeout("connect")

        with self.assertRaises(InterConnectionError) as ctx:
            self.send()

        self.assertNotIsInstance(ctx.exception, InterAmbiguousResultError)
        self.assertEqual(self.http.call_count, 4)  # max_retries=3 -> four connect attempts
        self.sleep.assert_not_called()

    def test_connect_timeout_then_read_timeout_is_ambiguous(self):
        self.http.side_effect = [REAL_EXC.ConnectTimeout("connect"), REAL_EXC.ReadTimeout("read")]

        with self.assertRaises(InterAmbiguousResultError):
            self.send()

        self.assertEqual(self.http.call_count, 2)

    def _tokens(self):
        """auth.get_valid_token that hands out a new token once force-refreshed."""
        state = {"token": "stale-token"}

        def get_valid_token(scopes=None, force_refresh=False):
            if force_refresh:
                state["token"] = "fresh-token"
            return state["token"]

        self.client.auth.get_valid_token.side_effect = get_valid_token

    def _forced_refreshes(self):
        return [c for c in self.client.auth.get_valid_token.call_args_list if c.kwargs.get("force_refresh")]

    def test_401_refreshes_the_token_once_and_retries(self):
        self._tokens()
        self.http.side_effect = [_resp(401, {"error": "invalid_token"}), _resp(200, {"ok": 1})]

        result = self.send()

        self.assertEqual(result, {"ok": 1})
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(len(self._forced_refreshes()), 1)
        sent = [c.kwargs["headers"]["Authorization"] for c in self.http.call_args_list]
        self.assertEqual(sent, ["Bearer stale-token", "Bearer fresh-token"])
        self.client.auth._request_new_token.assert_not_called()
        self.sleep.assert_not_called()

    def test_401_twice_is_definitive(self):
        self._tokens()
        self.http.return_value = _resp(401, {"error": "invalid_token"})

        with self.assertRaises(InterAPIError) as ctx:
            self.send()

        self.assertNotIsInstance(ctx.exception, InterAmbiguousResultError)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(len(self._forced_refreshes()), 1)

    def test_401_after_a_connect_timeout_still_refreshes_once(self):
        self._tokens()
        self.http.side_effect = [
            REAL_EXC.ConnectTimeout("connect"),
            _resp(401, {}),
            _resp(200, {"ok": 1}),
        ]

        self.assertEqual(self.send(), {"ok": 1})
        self.assertEqual(self.http.call_count, 3)
        self.assertEqual(len(self._forced_refreshes()), 1)

    def test_401_then_ambiguous_is_ambiguous(self):
        self._tokens()
        self.http.side_effect = [_resp(401, {}), _resp(500, {})]

        with self.assertRaises(InterAmbiguousResultError):
            self.send()

        self.assertEqual(self.http.call_count, 2)

    def test_failed_refresh_after_401_sends_nothing_more(self):
        def get_valid_token(scopes=None, force_refresh=False):
            if force_refresh:
                raise InterAuthError("Authentication failed (HTTP 400)")
            return "stale-token"

        self.client.auth.get_valid_token.side_effect = get_valid_token
        self.http.return_value = _resp(401, {})

        with self.assertRaises(InterAuthError):
            self.send()

        self.assertEqual(self.http.call_count, 1)

    def test_auth_error_before_the_send_is_not_an_api_error(self):
        self.client.auth.get_valid_token.side_effect = InterAuthError("certificate expired")

        with self.assertRaises(InterAuthError) as ctx:
            self.send()

        self.assertNotIsInstance(ctx.exception, InterAPIError)
        self.http.assert_not_called()

    def test_no_response_writes_a_failed_log_row(self):
        self.http.side_effect = REAL_EXC.ReadTimeout("read timed out")

        with self.assertRaises(InterAmbiguousResultError):
            self.send()

        log = self._log_doc()
        self.assertEqual(log.response_code, 0)
        self.assertFalse(log.success)
        self.assertEqual(log.method, "POST")
        self.assertEqual(log.endpoint, self.path)
        self.assertIn("read timed out", log.error_message)
        log.insert.assert_called_once()

    def test_exhausted_connect_timeout_writes_a_failed_log_row(self):
        self.http.side_effect = REAL_EXC.ConnectTimeout("connect")

        with self.assertRaises(InterConnectionError):
            self.send()

        log = self._log_doc()
        self.assertEqual(log.response_code, 0)
        self.assertFalse(log.success)

    def test_ambiguous_status_logs_the_http_code(self):
        self.http.return_value = _resp(503, {"title": "indisponivel"})

        with self.assertRaises(InterAmbiguousResultError):
            self.send()

        log = self._log_doc()
        self.assertEqual(log.response_code, 503)
        self.assertFalse(log.success)

    def test_unexpected_exception_propagates_after_one_send_and_is_logged(self):
        self.http.side_effect = RuntimeError("job timeout")

        with self.assertRaises(RuntimeError):
            self.send()

        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(self._log_doc().response_code, 0)

    def test_a_broken_api_log_never_changes_the_outcome(self):
        frappe.new_doc.side_effect = RuntimeError("db gone")
        frappe.log_error.side_effect = RuntimeError("db really gone")
        outcomes = (
            (_resp(200, {"codigoSolicitacao": "SOL-1"}), None),
            (_resp(422, {}), InterAPIError),
            (_resp(500, {}), InterAmbiguousResultError),
            (REAL_EXC.ReadTimeout("read"), InterAmbiguousResultError),
        )
        for outcome, expected in outcomes:
            with self.subTest(expected=expected):
                self.http.reset_mock()
                self.http.side_effect = [outcome]
                if expected is None:
                    self.assertEqual(self.send(), {"codigoSolicitacao": "SOL-1"})
                else:
                    with self.assertRaises(expected) as ctx:
                        self.send()
                    self.assertIs(type(ctx.exception), expected)

    def test_2xx_without_json_is_returned_not_raised(self):
        self.http.return_value = _resp(200, None)

        result = self.send()

        self.assertIsInstance(result, dict)
        self.assertNotIn("codigoSolicitacao", result)
        self.assertEqual(self.http.call_count, 1)

    def test_2xx_with_a_non_object_body_still_returns_a_dict(self):
        self.http.return_value = _resp(200, ["unexpected"])

        result = self.send()

        self.assertEqual(result, {"raw": ["unexpected"]})


class TestSendPixNeverResends(_PaymentPostPolicy, _ClientTestCase):
    path = "/banking/v2/pix"

    def send(self):
        return self.client.send_pix(PIX, KEY)

    def test_sends_the_idempotency_header_and_the_body(self):
        self.http.return_value = _resp(200, {})

        self.send()

        kwargs = self.http.call_args.kwargs
        self.assertEqual(kwargs["headers"]["x-id-idempotente"], KEY)
        self.assertEqual(kwargs["json"], PIX)

    def test_a_fresh_uuid4_is_accepted(self):
        self.http.return_value = _resp(200, {})
        key = str(uuid.uuid4())

        self.client.send_pix(PIX, key)

        self.assertEqual(self.http.call_args.kwargs["headers"]["x-id-idempotente"], key)

    def test_invalid_key_is_refused_before_any_request(self):
        bad_keys = ("", None, KEY.upper(), "not-a-uuid", KEY.replace("-", ""), KEY + "\n", " " + KEY, 12345)
        for key in bad_keys:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.client.send_pix(PIX, key)
        self.http.assert_not_called()
        self.client.auth.get_valid_token.assert_not_called()

    def test_the_key_is_mandatory(self):
        with self.assertRaises(TypeError):
            self.client.send_pix(PIX)
        self.http.assert_not_called()

    def test_extra_headers_cannot_replace_the_bearer_token(self):
        self.http.return_value = _resp(200, {})

        self.client._request(
            "POST", "/banking/v2/pix", data=PIX, extra_headers={"Authorization": "Bearer evil", "x-a": "b"}
        )

        headers = self.http.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer fake-token-123")
        self.assertEqual(headers["x-a"], "b")


class TestPayBarcodeNeverResends(_PaymentPostPolicy, _ClientTestCase):
    path = "/banking/v2/pagamento"

    def send(self):
        return self.client.pay_barcode(BOLETO)

    def test_sends_the_body_without_an_idempotency_header(self):
        self.http.return_value = _resp(200, {})

        self.send()

        kwargs = self.http.call_args.kwargs
        self.assertEqual(kwargs["json"], BOLETO)
        self.assertNotIn("x-id-idempotente", kwargs["headers"])


class TestRetrySafeDefault(_ClientTestCase):
    """retry_safe defaults to ``method != "POST"`` and can be set explicitly."""

    def test_any_post_is_sent_once_by_default(self):
        self.http.return_value = _resp(500, {})

        with self.assertRaises(InterAmbiguousResultError):
            self.client.create_boleto({"seuNumero": "1"})

        self.assertEqual(self.http.call_count, 1)

    def test_send_ted_is_sent_once(self):
        self.http.side_effect = REAL_EXC.ReadTimeout("read")

        with self.assertRaises(InterAmbiguousResultError):
            self.client.send_ted({"valor": 1})

        self.assertEqual(self.http.call_count, 1)

    def test_a_post_declared_retry_safe_is_retried(self):
        self.http.side_effect = [_resp(500, {}), _resp(200, {"ok": 1})]

        result = self.client._request("POST", "/x", data={"a": 1}, retry_safe=True)

        self.assertEqual(result, {"ok": 1})
        self.assertEqual(self.http.call_count, 2)

    def test_a_get_declared_unsafe_is_sent_once(self):
        self.http.return_value = _resp(500, {})

        with self.assertRaises(InterAmbiguousResultError):
            self.client._request("GET", "/x", retry_safe=False)

        self.assertEqual(self.http.call_count, 1)

    def test_put_is_retry_safe(self):
        self.http.side_effect = [_resp(503, {}), _resp(200, {"ok": 1})]

        self.assertEqual(self.client.create_pix_charge("tx1", {"valor": {}}), {"ok": 1})
        self.assertEqual(self.http.call_count, 2)

    def test_request_with_retry_applies_the_same_default(self):
        """The low-level method hands the response back; _request classifies it."""
        self.http.return_value = _resp(500, {})

        response = self.client._request_with_retry("POST", "/banking/v2/pagamento", BOLETO)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.http.call_count, 1)
        self.assertIs(self.http.call_args.kwargs["allow_redirects"], False)
        self.sleep.assert_not_called()


class TestRetrySafePolicyPreserved(_ClientTestCase):
    """GET/PUT/DELETE keep the old policy (plus 504 and a working 401 refresh)."""

    def test_5xx_is_retried_with_backoff(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.setUp()
                self.http.side_effect = [_resp(status, {}), _resp(200, {"disponivel": 1})]

                self.assertEqual(self.client.get_balance(), {"disponivel": 1})

                self.assertEqual(self.http.call_count, 2)
                self.sleep.assert_called_once_with(30)

    def test_5xx_exhausted_is_a_plain_api_error(self):
        self.http.return_value = _resp(500, {"title": "erro"})

        with self.assertRaises(InterAPIError) as ctx:
            self.client.get_balance()

        self.assertNotIsInstance(ctx.exception, InterAmbiguousResultError)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(self.http.call_count, 4)

    def test_read_timeout_is_retried_then_timeout_error(self):
        self.http.side_effect = REAL_EXC.ReadTimeout("read timed out")

        with self.assertRaises(InterTimeoutError) as ctx:
            self.client.get_balance()

        self.assertNotIsInstance(ctx.exception, InterAmbiguousResultError)
        self.assertEqual(self.http.call_count, 4)
        self.assertEqual([c.args for c in self.sleep.call_args_list], [(10,), (10,), (10,)])

    def test_connection_error_is_retried_then_connection_error(self):
        self.http.side_effect = [REAL_EXC.ConnectionError("reset"), _resp(200, {"disponivel": 2})]

        self.assertEqual(self.client.get_balance(), {"disponivel": 2})
        self.assertEqual(self.http.call_count, 2)

    def test_ssl_error_is_a_certificate_error_without_retry(self):
        self.http.side_effect = REAL_EXC.SSLError("certificate verify failed")

        with self.assertRaises(InterCertificateError):
            self.client.get_balance()

        self.assertEqual(self.http.call_count, 1)
        self.sleep.assert_not_called()

    def test_429_backs_off_exponentially(self):
        self.http.side_effect = [_resp(429, {}), _resp(429, {}), _resp(200, {"disponivel": 3})]

        self.assertEqual(self.client.get_balance(), {"disponivel": 3})

        self.assertEqual([c.args for c in self.sleep.call_args_list], [(30,), (60,)])

    def test_401_forces_a_real_token_refresh(self):
        self.http.side_effect = [_resp(401, {}), _resp(200, {"disponivel": 4})]

        self.assertEqual(self.client.get_balance(), {"disponivel": 4})

        self.assertEqual(self.http.call_count, 2)
        self.client.auth.get_valid_token.assert_any_call(force_refresh=True)
        self.client.auth._request_new_token.assert_not_called()

    def test_redirect_behaviour_is_left_to_requests(self):
        self.http.return_value = _resp(200, {})

        self.client.get_balance()

        self.assertNotIn("allow_redirects", self.http.call_args.kwargs)

    def test_failure_without_a_response_writes_a_log_row(self):
        self.http.side_effect = REAL_EXC.ReadTimeout("read timed out")

        with self.assertRaises(InterTimeoutError):
            self.client.get_balance()

        frappe.new_doc.assert_called_once_with("Inter API Log")
        log = frappe.new_doc.return_value
        self.assertEqual(log.response_code, 0)
        self.assertFalse(log.success)
        self.assertEqual(log.endpoint, "/banking/v2/saldo")

    def test_raw_download_failure_writes_a_log_row(self):
        self.http.side_effect = REAL_EXC.SSLError("bad cert")

        with self.assertRaises(InterCertificateError):
            self.client.download_boleto_pdf("REQ-1")

        frappe.new_doc.assert_called_once_with("Inter API Log")
        self.assertEqual(frappe.new_doc.return_value.response_code, 0)


class TestPaymentQueries(_ClientTestCase):
    """Read-only lookups used by the status poll: GET, so retry_safe."""

    TX = "c42f0787-02cb-4b31-827e-459ec9d7ece1"

    def test_get_pix_payment_queries_by_codigo_solicitacao(self):
        self.http.return_value = _resp(200, {"transacaoPix": {"status": "PAGO"}})

        result = self.client.get_pix_payment(codigo_solicitacao="SOL-123")

        self.assertEqual(result, {"transacaoPix": {"status": "PAGO"}})
        kwargs = self.http.call_args.kwargs
        self.assertEqual(kwargs["method"], "GET")
        self.assertTrue(kwargs["url"].endswith("/banking/v2/pix/SOL-123"))

    def test_get_pix_payment_parameter_is_not_an_e2e_id(self):
        parameters = list(inspect.signature(InterAPIClient.get_pix_payment).parameters)
        self.assertEqual(parameters, ["self", "codigo_solicitacao"])

    def test_find_by_transaction_code_and_dates(self):
        self.http.return_value = _resp(200, [{"codigoTransacao": self.TX, "statusPagamento": "PAGO"}])

        result = self.client.find_barcode_payments(
            codigo_transacao=self.TX, start_date=date(2026, 9, 19), end_date=date(2026, 9, 21)
        )

        self.assertEqual(result, [{"codigoTransacao": self.TX, "statusPagamento": "PAGO"}])
        kwargs = self.http.call_args.kwargs
        self.assertEqual(kwargs["method"], "GET")
        self.assertTrue(kwargs["url"].endswith("/banking/v2/pagamento"))
        self.assertIsNone(kwargs["json"])
        self.assertEqual(
            kwargs["params"],
            {
                "codigoTransacao": self.TX,
                "dataInicio": "2026-09-19",
                "dataFim": "2026-09-21",
                "filtrarDataPor": "INCLUSAO",
            },
        )

    def test_find_by_barcode_with_another_date_filter(self):
        self.http.return_value = _resp(200, [])

        self.client.find_barcode_payments(
            barcode=BOLETO["codBarraLinhaDigitavel"],
            start_date="2026-09-01",
            end_date=datetime(2026, 9, 30, 23, 59),
            filter_date_by="VENCIMENTO",
        )

        self.assertEqual(
            self.http.call_args.kwargs["params"],
            {
                "codBarraLinhaDigitavel": BOLETO["codBarraLinhaDigitavel"],
                "dataInicio": "2026-09-01",
                "dataFim": "2026-09-30",
                "filtrarDataPor": "VENCIMENTO",
            },
        )

    def test_find_without_dates_sends_no_date_parameters(self):
        self.http.return_value = _resp(200, [])

        self.client.find_barcode_payments(codigo_transacao=self.TX)

        self.assertEqual(
            self.http.call_args.kwargs["params"], {"codigoTransacao": self.TX, "filtrarDataPor": "INCLUSAO"}
        )

    def test_find_arguments_are_keyword_only(self):
        with self.assertRaises(TypeError):
            self.client.find_barcode_payments(self.TX)
        self.http.assert_not_called()

    def test_find_refuses_half_a_date_range(self):
        """The API requires dataInicio and dataFim together."""
        for kwargs in ({"start_date": date(2026, 9, 1)}, {"end_date": date(2026, 9, 1)}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.client.find_barcode_payments(codigo_transacao=self.TX, **kwargs)
        self.http.assert_not_called()

    def test_find_refuses_an_unknown_date_filter(self):
        with self.assertRaises(ValueError):
            self.client.find_barcode_payments(codigo_transacao=self.TX, filter_date_by="EMISSAO")
        self.http.assert_not_called()

    def test_find_non_list_response_is_an_empty_list(self):
        for body in ({"message": "nada"}, None, "texto", {"pagamentos": [{"x": 1}]}):
            with self.subTest(body=body):
                self.http.return_value = _resp(200, body)
                self.assertEqual(self.client.find_barcode_payments(codigo_transacao=self.TX), [])

    def test_find_drops_items_that_are_not_objects(self):
        self.http.return_value = _resp(200, [{"statusPagamento": "PAGO"}, "lixo", None])

        self.assertEqual(
            self.client.find_barcode_payments(codigo_transacao=self.TX), [{"statusPagamento": "PAGO"}]
        )

    def test_find_is_retry_safe_and_errors_are_not_ambiguous(self):
        self.http.side_effect = [_resp(503, {}), _resp(200, [])]
        self.assertEqual(self.client.find_barcode_payments(codigo_transacao=self.TX), [])
        self.assertEqual(self.http.call_count, 2)

        self.http.reset_mock()
        self.http.side_effect = [_resp(403, {"title": "escopo"})]
        with self.assertRaises(InterAPIError) as ctx:
            self.client.find_barcode_payments(codigo_transacao=self.TX)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertNotIsInstance(ctx.exception, InterAmbiguousResultError)


class TestErrorTypes(unittest.TestCase):
    def test_api_error_carries_status_and_body(self):
        err = InterAPIError("boom", status_code=422, response_body={"title": "x"})
        self.assertEqual(str(err), "boom")
        self.assertEqual(err.status_code, 422)
        self.assertEqual(err.response_body, {"title": "x"})

    def test_api_error_defaults(self):
        err = InterTimeoutError("slow")
        self.assertIsNone(err.status_code)
        self.assertIsNone(err.response_body)

    def test_ambiguous_is_an_api_error_but_auth_error_is_not(self):
        self.assertTrue(issubclass(InterAmbiguousResultError, InterAPIError))
        self.assertFalse(issubclass(InterAuthError, InterAPIError))
        for definitive in (InterCertificateError, InterTimeoutError, InterConnectionError):
            self.assertFalse(issubclass(definitive, InterAmbiguousResultError))


if __name__ == "__main__":
    unittest.main()
