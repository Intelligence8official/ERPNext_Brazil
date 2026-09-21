"""
Core HTTP client for all Banco Inter API calls.

Every API call flows through this class to ensure consistent authentication,
logging, error handling, and retry logic.

Two send policies (see ``_request_with_retry``):

- ``retry_safe`` requests (everything that is not a POST, by default) are
  retried with backoff on 429, 5xx, timeouts and connection errors.
- Requests that are NOT ``retry_safe`` - the payment POSTs - are sent ONCE.
  Only what provably never reached the bank is tried again (connect timeout,
  HTTP 401 with a token refresh). An explicit allowlist of HTTP statuses is a
  definitive rejection (``InterAPIError``); every other outcome is ambiguous
  (``InterAmbiguousResultError``) and must be verified at the bank, never re-sent.
"""

import json
import re
import time
from datetime import date, datetime

import frappe
import requests
from frappe.utils import now_datetime

from brazil_module.services.banking.auth_manager import InterAuthManager


# Base URLs
BASE_URL_PRODUCTION = "https://cdpj.partners.bancointer.com.br"
BASE_URL_SANDBOX = "https://cdpj-sandbox.partners.uatinter.co"

REQUEST_TIMEOUT_SECONDS = 60

# Non-retry_safe requests: HTTP statuses after which the bank provably does not
# hold the operation. Any other non-2xx status (3xx, 408, 409, other 4xx, every
# 5xx) is ambiguous. 409 is ambiguous on purpose: Inter documents it on
# /banking/v2/pagamento as a generic internal error.
DEFINITIVE_STATUS_CODES = frozenset({400, 401, 403, 404, 405, 406, 415, 422, 429})

# retry_safe requests: server errors worth another attempt.
RETRYABLE_SERVER_ERRORS = frozenset({500, 502, 503, 504})

# Header x-id-idempotente of POST /banking/v2/pix (pattern of Inter's OpenAPI;
# str(uuid.uuid4()) matches it).
IDEMPOTENCY_HEADER = "x-id-idempotente"
_IDEMPOTENCY_KEY_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# GET /banking/v2/pagamento: what dataInicio/dataFim apply to.
PAYMENT_DATE_FILTERS = ("INCLUSAO", "PAGAMENTO", "VENCIMENTO")


DEFAULT_MAX_RETRIES = 3


def _retries(max_retries: int | None) -> int:
    """``None`` keeps the default. A caller running inside a web request passes 0: the retry policy
    sleeps in the worker, and no click in the desk should hold one of those for minutes."""
    return DEFAULT_MAX_RETRIES if max_retries is None else max_retries


class InterAPIClient:
    """Low-level HTTP client for Banco Inter API.

    All API calls go through _request() which handles:
    - mTLS certificate attachment
    - OAuth2 Bearer token
    - Request/response logging to Inter API Log (also when no response came back)
    - Retry with exponential backoff for retry_safe requests; a single send for
      the others (payment POSTs)
    - Standardized error handling
    """

    def __init__(self, company_account_name: str):
        self.company_account_name = company_account_name
        self.auth = InterAuthManager(company_account_name)
        self._account_doc = None

    @property
    def account_doc(self):
        if self._account_doc is None:
            self._account_doc = frappe.get_doc(
                "Inter Company Account", self.company_account_name
            )
        return self._account_doc

    @property
    def base_url(self) -> str:
        env = self.account_doc.get_environment()
        if env == "Production":
            return BASE_URL_PRODUCTION
        return BASE_URL_SANDBOX

    # ── Banking API ─────────────────────────────────────────────────────

    def get_balance(self, check_date: date | None = None) -> dict:
        """Get account balance."""
        params = {}
        if check_date:
            params["dataSaldo"] = check_date.isoformat()
        return self._request("GET", "/banking/v2/saldo", params=params, api_module="Banking")

    def get_statement(self, start_date: date, end_date: date) -> list[dict]:
        """Get bank statement (extrato) for a date range."""
        params = {
            "dataInicio": start_date.isoformat(),
            "dataFim": end_date.isoformat(),
        }
        response = self._request(
            "GET", "/banking/v2/extrato", params=params, api_module="Banking"
        )
        return response.get("transacoes", [])

    def get_statement_pdf(self, start_date: date, end_date: date) -> bytes:
        """Get bank statement as PDF."""
        params = {
            "dataInicio": start_date.isoformat(),
            "dataFim": end_date.isoformat(),
        }
        return self._request_raw(
            "GET", "/banking/v2/extrato/exportar", params=params, api_module="Banking"
        )

    # ── Cobranca API (Boleto + PIX) ────────────────────────────────────

    def create_boleto(self, boleto_data: dict) -> dict:
        """Create a boleto (or BoletoPIX hybrid)."""
        return self._request(
            "POST", "/cobranca/v3/cobrancas", data=boleto_data, api_module="Cobranca"
        )

    def get_boleto(self, request_code: str) -> dict:
        """Get boleto details by request code."""
        return self._request(
            "GET", f"/cobranca/v3/cobrancas/{request_code}", api_module="Cobranca"
        )

    def cancel_boleto(self, request_code: str, reason: str) -> dict:
        """Cancel (baixar) a boleto."""
        return self._request(
            "POST",
            f"/cobranca/v3/cobrancas/{request_code}/cancelar",
            data={"motivoCancelamento": reason},
            api_module="Cobranca",
        )

    def download_boleto_pdf(self, request_code: str) -> bytes:
        """Download boleto as PDF."""
        return self._request_raw(
            "GET", f"/cobranca/v3/cobrancas/{request_code}/pdf", api_module="Cobranca"
        )

    def list_boletos(
        self,
        start_date: date,
        end_date: date,
        status: str | None = None,
        page: int = 0,
        page_size: int = 100,
    ) -> dict:
        """List boletos in a date range."""
        params = {
            "dataInicial": start_date.isoformat(),
            "dataFinal": end_date.isoformat(),
            "paginaAtual": page,
            "itensPorPagina": page_size,
        }
        if status:
            params["situacao"] = status
        return self._request(
            "GET", "/cobranca/v3/cobrancas", params=params, api_module="Cobranca"
        )

    # ── PIX API ────────────────────────────────────────────────────────

    def create_pix_charge(self, txid: str, charge_data: dict) -> dict:
        """Create an immediate PIX charge (cobranca imediata)."""
        return self._request(
            "PUT", f"/pix/v2/cob/{txid}", data=charge_data, api_module="PIX"
        )

    def get_pix_charge(self, txid: str) -> dict:
        """Get PIX charge details."""
        return self._request("GET", f"/pix/v2/cob/{txid}", api_module="PIX")

    def list_pix_charges(self, start_date: date, end_date: date) -> dict:
        """List PIX charges in a date range."""
        params = {
            "inicio": f"{start_date.isoformat()}T00:00:00Z",
            "fim": f"{end_date.isoformat()}T23:59:59Z",
        }
        return self._request("GET", "/pix/v2/cob", params=params, api_module="PIX")

    def create_pix_charge_with_due_date(self, txid: str, charge_data: dict) -> dict:
        """Create a scheduled PIX charge (cobranca com vencimento)."""
        return self._request(
            "PUT", f"/pix/v2/cobv/{txid}", data=charge_data, api_module="PIX"
        )

    def get_pix_charge_with_due_date(self, txid: str) -> dict:
        """Get scheduled PIX charge details."""
        return self._request("GET", f"/pix/v2/cobv/{txid}", api_module="PIX")

    # ── PIX Payments (Outbound) ────────────────────────────────────────

    def send_pix(self, payment_data: dict, idempotency_key: str) -> dict:
        """Send a PIX payment. Sent once - never retried once it may have reached the bank.

        ``idempotency_key`` goes out as ``x-id-idempotente``. It is a second line
        of defence, not a licence to retry: the bank's key retention is undocumented.

        Raises:
            ValueError: the key is not a lower-case UUID. Nothing is requested.
            InterAmbiguousResultError: outcome unknown - verify at the bank.
            InterAPIError: (any other) definitive rejection - the bank does not hold it.
        """
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            raise ValueError("idempotency_key must be a lower-case UUID, e.g. str(uuid.uuid4())")
        result = self._request(
            "POST",
            "/banking/v2/pix",
            data=payment_data,
            api_module="Payment",
            retry_safe=False,
            extra_headers={IDEMPOTENCY_HEADER: idempotency_key},
        )
        return _as_dict(result)

    def get_pix_payment(self, codigo_solicitacao: str, max_retries: int | None = None) -> dict:
        """Get an outbound PIX payment by the ``codigoSolicitacao`` of the POST.

        The status is in ``transacaoPix.status``. The bank only answers for
        payments of the last 90 days.
        """
        return self._request(
            "GET", f"/banking/v2/pix/{codigo_solicitacao}", api_module="Payment",
            max_retries=_retries(max_retries),
        )

    # ── TED / Payments ─────────────────────────────────────────────────

    def send_ted(self, payment_data: dict) -> dict:
        """Send a TED transfer.

        Inter's Banking API has no TED endpoint; nothing calls this. Kept sent-once
        like every other payment POST.
        """
        return self._request(
            "POST", "/banking/v2/ted", data=payment_data, api_module="Payment", retry_safe=False
        )

    def pay_barcode(self, payment_data: dict) -> dict:
        """Pay a boleto by barcode. Sent once - this endpoint has no idempotency key.

        Raises:
            InterAmbiguousResultError: outcome unknown - verify at the bank.
            InterAPIError: (any other) definitive rejection - the bank does not hold it.
        """
        result = self._request(
            "POST",
            "/banking/v2/pagamento",
            data=payment_data,
            api_module="Payment",
            retry_safe=False,
        )
        return _as_dict(result)

    def find_barcode_payments(
        self,
        *,
        barcode: str | None = None,
        codigo_transacao: str | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        filter_date_by: str = "INCLUSAO",
        max_retries: int | None = None,
    ) -> list[dict]:
        """Search boleto payments (``GET /banking/v2/pagamento``).

        Without dates the bank only searches the last 30 days; a range is at most
        90 days and both ends are required. An empty list is "nothing found",
        never proof that a payment does not exist.
        """
        if (start_date is None) != (end_date is None):
            raise ValueError("start_date and end_date must be given together")
        if filter_date_by not in PAYMENT_DATE_FILTERS:
            raise ValueError(f"filter_date_by must be one of {', '.join(PAYMENT_DATE_FILTERS)}")

        params = {}
        if barcode:
            params["codBarraLinhaDigitavel"] = barcode
        if codigo_transacao:
            params["codigoTransacao"] = codigo_transacao
        if start_date is not None:
            params["dataInicio"] = _iso_date(start_date)
            params["dataFim"] = _iso_date(end_date)
        params["filtrarDataPor"] = filter_date_by

        response = self._request(
            "GET", "/banking/v2/pagamento", params=params, api_module="Payment",
            max_retries=_retries(max_retries),
        )
        if not isinstance(response, list):
            return []
        return [payment for payment in response if isinstance(payment, dict)]

    # ── Webhooks ───────────────────────────────────────────────────────

    def register_webhook(self, webhook_url: str, webhook_type: str = "pix") -> dict:
        """Register a webhook URL with Banco Inter."""
        if webhook_type == "pix":
            return self._request(
                "PUT",
                "/pix/v2/webhook",
                data={"webhookUrl": webhook_url},
                api_module="PIX",
            )
        return self._request(
            "PUT",
            "/cobranca/v3/cobrancas/webhook",
            data={"webhookUrl": webhook_url},
            api_module="Cobranca",
        )

    def get_webhook(self, webhook_type: str = "pix") -> dict:
        """Get current webhook configuration."""
        if webhook_type == "pix":
            return self._request("GET", "/pix/v2/webhook", api_module="PIX")
        return self._request(
            "GET", "/cobranca/v3/cobrancas/webhook", api_module="Cobranca"
        )

    def delete_webhook(self, webhook_type: str = "pix") -> dict:
        """Remove webhook registration."""
        if webhook_type == "pix":
            return self._request("DELETE", "/pix/v2/webhook", api_module="PIX")
        return self._request(
            "DELETE", "/cobranca/v3/cobrancas/webhook", api_module="Cobranca"
        )

    # ── Internal HTTP Methods ──────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        params: dict | None = None,
        api_module: str = "Banking",
        max_retries: int = 3,
        retry_safe: bool | None = None,
        extra_headers: dict | None = None,
    ) -> dict:
        """Execute an API request under its send policy, with logging.

        Args:
            retry_safe: may the request be sent again? Defaults to ``method != "POST"``.
                When False, every ``InterAPIError`` raised here that is not an
                ``InterAmbiguousResultError`` is a definitive rejection.
            extra_headers: added to the request; cannot replace Authorization.

        Returns:
            Parsed JSON response as dict.
        """
        if retry_safe is None:
            retry_safe = _default_retry_safe(method)
        started = time.time()

        try:
            response = self._request_with_retry(
                method, path, data, params, max_retries, retry_safe=retry_safe, extra_headers=extra_headers
            )
        except Exception as e:
            self._log_failed_call(method, path, data, e, started, api_module)
            raise

        result = _parse_body(response)
        succeeded = 200 <= response.status_code < 300
        self._log_answer(
            method, path, data, response, result, started, api_module, "" if succeeded else str(result)
        )

        if not succeeded:
            raise _error_for_status(f"{method} {path}", response.status_code, result, retry_safe)

        return result

    def _request_raw(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        api_module: str = "Banking",
    ) -> bytes:
        """Execute request and return raw bytes (for PDF downloads)."""
        retry_safe = _default_retry_safe(method)
        started = time.time()

        try:
            response = self._request_with_retry(
                method, path, None, params, max_retries=2, retry_safe=retry_safe
            )
        except Exception as e:
            self._log_failed_call(method, path, None, e, started, api_module)
            raise

        succeeded = 200 <= response.status_code < 300
        summary = {"type": "binary", "size": len(response.content)}
        self._log_answer(
            method, path, None, response, summary, started, api_module, "" if succeeded else "Binary request failed"
        )

        if not succeeded:
            raise _error_for_status(f"{method} {path}", response.status_code, None, retry_safe)

        return response.content

    def _request_with_retry(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        params: dict | None = None,
        max_retries: int = 3,
        retry_safe: bool | None = None,
        extra_headers: dict | None = None,
    ) -> requests.Response:
        """Issue the HTTP request under the policy that fits it.

        Returns the last response, whatever its status; ``_request`` turns a
        non-2xx status into the right exception.
        """
        if retry_safe is None:
            retry_safe = _default_retry_safe(method)
        cert_path, key_path = self.auth.get_cert_paths()
        call = {
            "method": method,
            "url": f"{self.base_url}{path}",
            "json": data if data and method in ("POST", "PUT", "PATCH") else None,
            "params": params,
            "cert": (cert_path, key_path),
            "timeout": REQUEST_TIMEOUT_SECONDS,
        }
        label = f"{method} {path}"

        if retry_safe:
            return self._send_with_backoff(call, extra_headers, max_retries, label)
        # A followed 307/308 would POST the payment a second time.
        return self._send_once({**call, "allow_redirects": False}, extra_headers, max_retries, label)

    def _issue(self, call: dict, token: str, extra_headers: dict | None) -> requests.Response:
        """One HTTP request. The fixed headers win over ``extra_headers``."""
        headers = {
            **(extra_headers or {}),
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        return requests.request(headers=headers, **call)

    def _send_once(
        self, call: dict, extra_headers: dict | None, max_retries: int, label: str
    ) -> requests.Response:
        """Policy for requests that must never be sent twice (payment POSTs).

        No sleeping in the worker. Tried again only when the bank provably did
        not process the attempt: a connect timeout (up to ``max_retries`` times)
        and one HTTP 401 (after a forced token refresh). Every other exception of
        ``requests`` raises ``InterAmbiguousResultError`` at once - so whenever
        this method raises, all earlier attempts were provably not processed.
        """
        refreshed = False
        connect_timeouts = 0

        while True:
            # Token first, outside the try: an InterAuthError means nothing was sent.
            token = self.auth.get_valid_token()
            try:
                response = self._issue(call, token, extra_headers)
            except requests.exceptions.ConnectTimeout as e:
                # Must precede ConnectionError and Timeout, its base classes. The
                # TCP connection was never established: nothing reached the bank.
                connect_timeouts += 1
                if connect_timeouts > max_retries:
                    raise InterConnectionError(
                        f"Could not connect after {connect_timeouts} attempts, nothing was sent: {label}"
                    ) from e
                continue
            except requests.exceptions.RequestException as e:
                # Read timeout, reset connection, SSL error (it can surface after
                # the body was written), broken response... the bank may hold it.
                raise InterAmbiguousResultError(
                    f"Outcome unknown for {label} ({type(e).__name__}: {e}). The request may have "
                    "reached the bank: verify there, do not send again."
                ) from e

            if response.status_code == 401 and not refreshed:
                refreshed = True
                self.auth.get_valid_token(force_refresh=True)
                continue

            return response

    def _send_with_backoff(
        self, call: dict, extra_headers: dict | None, max_retries: int, label: str
    ) -> requests.Response:
        """Policy for retry_safe requests: exponential backoff, as before."""
        refreshed = False

        for attempt in range(max_retries + 1):
            can_retry = attempt < max_retries
            try:
                response = self._issue(call, self.auth.get_valid_token(), extra_headers)
            except requests.exceptions.SSLError as e:
                raise InterCertificateError(f"SSL/Certificate error: {e}") from e
            except requests.exceptions.Timeout as e:
                if can_retry:
                    time.sleep(10)
                    continue
                raise InterTimeoutError(
                    f"Request timed out after {max_retries + 1} attempts: {label}"
                ) from e
            except requests.exceptions.ConnectionError as e:
                if can_retry:
                    time.sleep(10)
                    continue
                raise InterConnectionError(
                    f"Connection failed after {max_retries + 1} attempts: {label}"
                ) from e

            status = response.status_code
            if status == 429 and can_retry:  # rate limited
                time.sleep(min(30 * (2 ** attempt), 300))
                continue
            if status in RETRYABLE_SERVER_ERRORS and can_retry:
                time.sleep(min(30 * (2 ** attempt), 120))
                continue
            if status == 401 and not refreshed and can_retry:
                refreshed = True
                self.auth.get_valid_token(force_refresh=True)
                continue
            return response

        # Only reachable with a negative max_retries: no attempt was made.
        raise InterAPIError(f"Max retries exceeded for {label}")

    def _log_answer(
        self, method: str, path: str, data: dict | None, response, body, started: float,
        api_module: str, error_message: str,
    ) -> None:
        """Log a call the bank answered, whatever the status."""
        self._log_api_call(
            method=method,
            endpoint=path,
            request_body=data,
            response_code=response.status_code,
            response_body=body,
            duration_ms=_elapsed_ms(started),
            success=200 <= response.status_code < 300,
            error_message=error_message,
            api_module=api_module,
        )

    def _log_failed_call(
        self, method: str, path: str, data: dict | None, error: Exception, started: float, api_module: str
    ) -> None:
        """Leave a trace of a call that ended without an HTTP response."""
        self._log_api_call(
            method=method,
            endpoint=path,
            request_body=data,
            response_code=getattr(error, "status_code", None) or 0,
            response_body=None,
            duration_ms=_elapsed_ms(started),
            success=False,
            error_message=f"{type(error).__name__}: {error}",
            api_module=api_module,
        )

    def _log_api_call(
        self,
        method: str,
        endpoint: str,
        request_body: dict | None,
        response_code: int,
        response_body: dict | None,
        duration_ms: int,
        success: bool,
        error_message: str,
        api_module: str,
    ):
        """Log API call to Inter API Log doctype."""
        try:
            # Sanitize request body - remove secrets
            safe_request = None
            if request_body:
                safe_request = {k: v for k, v in request_body.items()}
                for sensitive_key in ("client_secret", "access_token", "token"):
                    safe_request.pop(sensitive_key, None)

            # Truncate response body
            response_str = json.dumps(response_body, default=str)[:5000] if response_body else ""

            log = frappe.new_doc("Inter API Log")
            log.timestamp = now_datetime()
            log.company = self.account_doc.company
            log.api_module = api_module
            log.method = method
            log.endpoint = endpoint
            log.request_body = json.dumps(safe_request, default=str) if safe_request else ""
            log.response_code = response_code
            log.response_body = response_str
            log.success = success
            log.error_message = error_message[:500] if error_message else ""
            log.duration_ms = duration_ms
            log.insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception as e:
            # Never let logging failures break the main flow: the caller must see
            # the bank's answer (or the real error), not a logging problem.
            try:
                frappe.log_error(title="Inter API Log Error", message=str(e))
            except Exception:
                pass


# ── Helpers ────────────────────────────────────────────────────────────

def _default_retry_safe(method: str) -> bool:
    """A POST is never sent twice unless the caller says it is safe."""
    return method.upper() != "POST"


def _iso_date(value: date | str) -> str:
    """YYYY-MM-DD from a date, a datetime or an ISO string."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _elapsed_ms(started: float) -> int:
    return int((time.time() - started) * 1000)


def _parse_body(response) -> dict | list:
    try:
        return response.json()
    except Exception:
        return {"raw": response.text[:2000]}


def _as_dict(result) -> dict:
    """The payment endpoints answer with an object; anything else is kept under ``raw``."""
    return result if isinstance(result, dict) else {"raw": result}


def _error_for_status(label: str, status_code: int, body, retry_safe: bool) -> "InterAPIError":
    """Exception for a non-2xx response (see DEFINITIVE_STATUS_CODES)."""
    message = f"API error (HTTP {status_code}) on {label}: {body}"
    if retry_safe or status_code in DEFINITIVE_STATUS_CODES:
        return InterAPIError(message, status_code=status_code, response_body=body)
    return InterAmbiguousResultError(
        f"Outcome unknown - {message}. The bank may hold the operation: verify there, do not send again.",
        status_code=status_code,
        response_body=body,
    )


# ── Exception Classes ──────────────────────────────────────────────────

class InterAPIError(Exception):
    """General API error.

    ``status_code`` is the HTTP status when the bank answered, ``None`` when the
    call ended without an HTTP response. ``response_body`` is the parsed body.

    Contract for requests that are not ``retry_safe`` (payment POSTs): every
    ``InterAPIError`` that is NOT an ``InterAmbiguousResultError`` is definitive -
    the bank does not hold the operation.
    """

    def __init__(self, message: str, status_code: int | None = None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class InterAmbiguousResultError(InterAPIError):
    """The request may have reached the bank and the outcome is unknown.

    Never re-send on this error: the payment has to be verified at the bank.
    """


class InterCertificateError(InterAPIError):
    """Certificate/SSL error."""
    pass


class InterTimeoutError(InterAPIError):
    """Request timeout."""
    pass


class InterConnectionError(InterAPIError):
    """Connection error."""
    pass
