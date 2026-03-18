"""
OAuth2 + mTLS authentication manager for Banco Inter API.

Handles token acquisition, caching, refresh, and certificate path resolution.
"""

import os
from datetime import datetime, timezone, timedelta

import frappe
import requests


# Token endpoints
TOKEN_URL_PRODUCTION = "https://cdpj.partners.bancointer.com.br/oauth/v2/token"
TOKEN_URL_SANDBOX = "https://cdpj-sandbox.partners.uatinter.co/oauth/v2/token"

# Default scopes for all operations
DEFAULT_SCOPES = [
    "extrato.read",
    "boleto-cobranca.read",
    "boleto-cobranca.write",
    "pix.read",
    "pix.write",
    "pagamento-boleto.read",
    "pagamento-boleto.write",
    "pagamento-pix.read",
    "pagamento-pix.write",
    "pagamento-ted.read",
    "pagamento-ted.write",
]

# Buffer before token expiry to trigger refresh
TOKEN_REFRESH_BUFFER_SECONDS = 300  # 5 minutes


def resolve_frappe_file_path(file_url: str) -> str:
    """
    Resolve a Frappe file URL to an absolute file path.

    Handles /files/, /private/files/, and absolute paths.
    Adapted from brazil_nf/services/cert_utils.py.
    """
    if not file_url:
        raise ValueError("File path is empty")

    # Already an absolute path
    if os.path.isabs(file_url) and os.path.exists(file_url):
        return file_url

    clean_path = file_url.lstrip("/")
    possible_paths = []

    if clean_path.startswith("files/") or clean_path.startswith("private/files/"):
        possible_paths.append(frappe.get_site_path(clean_path))

    if file_url.startswith("/files/"):
        possible_paths.append(frappe.get_site_path("public", file_url[1:]))
        possible_paths.append(frappe.get_site_path(file_url[1:]))

    if file_url.startswith("/private/files/"):
        possible_paths.append(frappe.get_site_path(file_url[1:]))

    # Try File doctype
    try:
        file_doc = frappe.get_doc("File", {"file_url": file_url})
        if file_doc and file_doc.get_full_path():
            possible_paths.insert(0, file_doc.get_full_path())
    except Exception:
        pass

    for path in possible_paths:
        if path and os.path.exists(path):
            return path

    raise FileNotFoundError(
        f"Could not resolve file path: {file_url}. Tried: {possible_paths}"
    )


class InterAuthManager:
    """Manages OAuth2 authentication with mTLS for Banco Inter API."""

    def __init__(self, company_account_name: str):
        self.company_account_name = company_account_name
        self._account_doc = None

    @property
    def account_doc(self):
        if self._account_doc is None:
            self._account_doc = frappe.get_doc(
                "Inter Company Account", self.company_account_name
            )
        return self._account_doc

    def get_cert_paths(self) -> tuple[str, str]:
        """Resolve certificate and key file paths.

        Returns:
            Tuple of (cert_path, key_path) as absolute filesystem paths.
        """
        cert_path = resolve_frappe_file_path(self.account_doc.certificate_file)
        key_path = resolve_frappe_file_path(self.account_doc.key_file)
        return cert_path, key_path

    def get_token_url(self) -> str:
        """Get the appropriate token URL based on environment."""
        env = self.account_doc.get_environment()
        if env == "Production":
            return TOKEN_URL_PRODUCTION
        return TOKEN_URL_SANDBOX

    def get_valid_token(self, scopes: list[str] | None = None) -> str:
        """Get a valid OAuth2 access token, using cache or requesting a new one.

        Args:
            scopes: OAuth2 scopes to request. Defaults to all scopes.

        Returns:
            Valid access token string.
        """
        # Check cached token
        cached_token = self.account_doc.access_token
        token_expiry = self.account_doc.token_expiry

        if cached_token and token_expiry:
            expiry_dt = frappe.utils.get_datetime(token_expiry)
            now = frappe.utils.now_datetime()
            buffer = timedelta(seconds=TOKEN_REFRESH_BUFFER_SECONDS)

            if now + buffer < expiry_dt:
                return cached_token

        # Request new token
        token_data = self._request_new_token(scopes or DEFAULT_SCOPES)
        self._cache_token(token_data["access_token"], token_data["expires_in"])
        return token_data["access_token"]

    def _request_new_token(self, scopes: list[str]) -> dict:
        """Request a new OAuth2 token from Banco Inter.

        Args:
            scopes: List of OAuth2 scopes.

        Returns:
            Token response dict with access_token, expires_in, etc.
        """
        cert_path, key_path = self.get_cert_paths()
        client_secret = self.account_doc.get_client_secret_value()

        data = {
            "grant_type": "client_credentials",
            "client_id": self.account_doc.client_id,
            "client_secret": client_secret,
            "scope": " ".join(scopes),
        }

        try:
            response = requests.post(
                self.get_token_url(),
                data=data,
                cert=(cert_path, key_path),
                timeout=30,
            )
        except requests.exceptions.SSLError as e:
            raise InterAuthError(
                f"SSL/Certificate error during authentication: {e}"
            ) from e
        except requests.exceptions.ConnectionError as e:
            raise InterAuthError(
                f"Connection error during authentication: {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            raise InterAuthError(
                f"Timeout during authentication: {e}"
            ) from e

        if response.status_code != 200:
            error_detail = ""
            try:
                error_detail = response.json()
            except Exception:
                error_detail = response.text[:500]

            raise InterAuthError(
                f"Authentication failed (HTTP {response.status_code}): {error_detail}"
            )

        return response.json()

    def _cache_token(self, token: str, expires_in: int):
        """Cache the token in the Inter Company Account document.

        Uses db.set_value to avoid document locking conflicts.
        """
        expiry = frappe.utils.now_datetime() + timedelta(seconds=expires_in)

        frappe.db.set_value(
            "Inter Company Account",
            self.company_account_name,
            {
                "access_token": token,
                "token_expiry": expiry,
            },
            update_modified=False,
        )
        frappe.db.commit()

        # Update in-memory doc
        if self._account_doc:
            self._account_doc.access_token = token
            self._account_doc.token_expiry = expiry

    def validate_credentials(self) -> dict:
        """Test that credentials are valid by requesting a token.

        Returns:
            Dict with status and token info.
        """
        try:
            token_data = self._request_new_token(["extrato.read"])
            return {
                "status": "success",
                "token_type": token_data.get("token_type"),
                "expires_in": token_data.get("expires_in"),
                "scope": token_data.get("scope"),
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
            }


class InterAuthError(Exception):
    """Raised when authentication with Banco Inter fails."""
    pass
