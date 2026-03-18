"""
Core HTTP client for all Banco Inter API calls.

Every API call flows through this class to ensure consistent authentication,
logging, error handling, and retry logic.
"""

import json
import time
from datetime import date, datetime

import frappe
import requests
from frappe.utils import now_datetime

from Brazil_Module.services.banking.auth_manager import InterAuthManager, resolve_frappe_file_path


# Base URLs
BASE_URL_PRODUCTION = "https://cdpj.partners.bancointer.com.br"
BASE_URL_SANDBOX = "https://cdpj-sandbox.partners.uatinter.co"


class InterAPIClient:
    """Low-level HTTP client for Banco Inter API.

    All API calls go through _request() which handles:
    - mTLS certificate attachment
    - OAuth2 Bearer token
    - Request/response logging to Inter API Log
    - Retry with exponential backoff
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

    def send_pix(self, payment_data: dict) -> dict:
        """Send a PIX payment."""
        return self._request(
            "POST", "/banking/v2/pix", data=payment_data, api_module="Payment"
        )

    def get_pix_payment(self, e2e_id: str) -> dict:
        """Get PIX payment status."""
        return self._request(
            "GET", f"/banking/v2/pix/{e2e_id}", api_module="Payment"
        )

    # ── TED / Payments ─────────────────────────────────────────────────

    def send_ted(self, payment_data: dict) -> dict:
        """Send a TED transfer (free on Inter)."""
        return self._request(
            "POST", "/banking/v2/ted", data=payment_data, api_module="Payment"
        )

    def pay_barcode(self, payment_data: dict) -> dict:
        """Pay a boleto by barcode."""
        return self._request(
            "POST",
            "/banking/v2/pagamento",
            data=payment_data,
            api_module="Payment",
        )

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
    ) -> dict:
        """Execute an API request with retry logic and logging.

        Returns:
            Parsed JSON response as dict.
        """
        response = self._request_with_retry(method, path, data, params, max_retries)
        start_time = time.time()

        try:
            result = response.json()
        except Exception:
            result = {"raw": response.text[:2000]}

        duration = int((time.time() - start_time) * 1000)

        self._log_api_call(
            method=method,
            endpoint=path,
            request_body=data,
            response_code=response.status_code,
            response_body=result,
            duration_ms=duration,
            success=200 <= response.status_code < 300,
            error_message="" if 200 <= response.status_code < 300 else str(result),
            api_module=api_module,
        )

        if not (200 <= response.status_code < 300):
            raise InterAPIError(
                f"API error (HTTP {response.status_code}) on {method} {path}: {result}"
            )

        return result

    def _request_raw(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        api_module: str = "Banking",
    ) -> bytes:
        """Execute request and return raw bytes (for PDF downloads)."""
        response = self._request_with_retry(method, path, None, params, max_retries=2)

        self._log_api_call(
            method=method,
            endpoint=path,
            request_body=None,
            response_code=response.status_code,
            response_body={"type": "binary", "size": len(response.content)},
            duration_ms=0,
            success=200 <= response.status_code < 300,
            error_message="" if 200 <= response.status_code < 300 else "Binary request failed",
            api_module=api_module,
        )

        if not (200 <= response.status_code < 300):
            raise InterAPIError(
                f"API error (HTTP {response.status_code}) on {method} {path}"
            )

        return response.content

    def _request_with_retry(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        params: dict | None = None,
        max_retries: int = 3,
    ) -> requests.Response:
        """Execute HTTP request with exponential backoff retry."""
        cert_path, key_path = self.auth.get_cert_paths()
        url = f"{self.base_url}{path}"

        for attempt in range(max_retries + 1):
            try:
                token = self.auth.get_valid_token()
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }

                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data if data and method in ("POST", "PUT", "PATCH") else None,
                    params=params,
                    cert=(cert_path, key_path),
                    timeout=60,
                )

                # Rate limited
                if response.status_code == 429:
                    if attempt < max_retries:
                        wait = min(30 * (2 ** attempt), 300)
                        time.sleep(wait)
                        continue
                    return response

                # Server errors - retry
                if response.status_code in (500, 502, 503) and attempt < max_retries:
                    wait = min(30 * (2 ** attempt), 120)
                    time.sleep(wait)
                    continue

                # Auth error - refresh token and retry once
                if response.status_code == 401 and attempt == 0:
                    self.auth._account_doc = None  # Clear cached doc
                    self.auth._request_new_token(
                        self.auth.__class__.__mro__[0].__module__  # Force new token
                    )
                    continue

                return response

            except requests.exceptions.SSLError as e:
                raise InterCertificateError(
                    f"SSL/Certificate error: {e}"
                ) from e
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    time.sleep(10)
                    continue
                raise InterTimeoutError(
                    f"Request timed out after {max_retries + 1} attempts: {method} {path}"
                )
            except requests.exceptions.ConnectionError:
                if attempt < max_retries:
                    time.sleep(10)
                    continue
                raise InterConnectionError(
                    f"Connection failed after {max_retries + 1} attempts: {method} {path}"
                )

        # Should not reach here, but just in case
        raise InterAPIError(f"Max retries exceeded for {method} {path}")

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
            # Never let logging failures break the main flow
            frappe.log_error(str(e), "Inter API Log Error")


# ── Exception Classes ──────────────────────────────────────────────────

class InterAPIError(Exception):
    """General API error."""
    pass


class InterCertificateError(InterAPIError):
    """Certificate/SSL error."""
    pass


class InterTimeoutError(InterAPIError):
    """Request timeout."""
    pass


class InterConnectionError(InterAPIError):
    """Connection error."""
    pass
