"""Inter Company Account - Per-company API credentials and sync state."""

import os
from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.model.document import Document


class InterCompanyAccount(Document):

    def validate(self):
        self.validate_cnpj()
        self.validate_certificate()

    def validate_cnpj(self):
        if self.cnpj:
            clean = self.cnpj.replace(".", "").replace("/", "").replace("-", "")
            if len(clean) != 14 or not clean.isdigit():
                frappe.throw(_("CNPJ must be 14 digits"))
            self.cnpj = clean

    def validate_certificate(self):
        """Validate certificate and key files exist and check expiry."""
        if not self.certificate_file or not self.key_file:
            self.certificate_valid = 0
            self.certificate_expiry = None
            return

        try:
            from brazil_module.services.banking.auth_manager import resolve_frappe_file_path

            cert_path = resolve_frappe_file_path(self.certificate_file)
            key_path = resolve_frappe_file_path(self.key_file)

            if not os.path.exists(cert_path):
                frappe.throw(_("Certificate file not found: {0}").format(self.certificate_file))
            if not os.path.exists(key_path):
                frappe.throw(_("Key file not found: {0}").format(self.key_file))

            # Try to read and validate the certificate
            from cryptography import x509

            with open(cert_path, "rb") as f:
                cert_data = f.read()

            cert = x509.load_pem_x509_certificate(cert_data)
            expiry = cert.not_valid_after_utc
            now = datetime.now(timezone.utc)

            self.certificate_expiry = expiry.strftime("%Y-%m-%d")
            self.certificate_valid = 1 if now < expiry else 0

            if now >= expiry:
                frappe.msgprint(
                    _("Certificate expired on {0}").format(self.certificate_expiry),
                    indicator="red",
                    alert=True,
                )

        except ImportError:
            # cryptography not installed yet, skip validation
            self.certificate_valid = 0
        except Exception as e:
            self.certificate_valid = 0
            frappe.log_error(str(e), "Inter Certificate Validation Error")
            frappe.msgprint(
                _("Certificate validation error: {0}").format(str(e)),
                indicator="red",
            )

    def get_environment(self):
        """Get the effective environment (own override or global setting)."""
        if self.environment:
            return self.environment
        return frappe.db.get_single_value("Banco Inter Settings", "environment") or "Sandbox"

    def get_client_secret_value(self):
        """Retrieve the decrypted client secret."""
        return self.get_password("client_secret")

    @frappe.whitelist()
    def test_connection(self):
        """Test API connection with stored credentials."""
        from brazil_module.services.banking.auth_manager import InterAuthManager

        try:
            auth = InterAuthManager(self.name)
            token = auth.get_valid_token()
            if token:
                frappe.msgprint(
                    _("Connection successful! Token acquired."),
                    indicator="green",
                    alert=True,
                )
                return {"status": "success"}
        except Exception as e:
            frappe.msgprint(
                _("Connection failed: {0}").format(str(e)),
                indicator="red",
                alert=True,
            )
            return {"status": "error", "message": str(e)}

    @frappe.whitelist()
    def sync_now(self):
        """Trigger manual statement sync."""
        from brazil_module.services.banking.statement_sync import sync_statements_for_company

        frappe.enqueue(
            sync_statements_for_company,
            company_account_name=self.name,
            queue="short",
        )
        frappe.msgprint(
            _("Statement sync initiated. Check Sync Log for results."),
            indicator="blue",
            alert=True,
        )
        return {"status": "queued"}

    @frappe.whitelist()
    def fetch_balance(self):
        """Fetch current account balance."""
        from brazil_module.services.banking.statement_sync import update_balance

        try:
            balance = update_balance(self.name)
            frappe.msgprint(
                _("Balance updated: R$ {0}").format(frappe.format_value(balance, "Currency")),
                indicator="green",
                alert=True,
            )
            return {"status": "success", "balance": balance}
        except Exception as e:
            frappe.msgprint(
                _("Balance fetch failed: {0}").format(str(e)),
                indicator="red",
                alert=True,
            )
            return {"status": "error", "message": str(e)}

    @frappe.whitelist()
    def register_webhook(self):
        """Register webhook URL with Banco Inter."""
        from brazil_module.services.banking.webhook_handler import register_webhook_for_account

        try:
            result = register_webhook_for_account(self.name)
            frappe.msgprint(
                _("Webhook registered successfully."),
                indicator="green",
                alert=True,
            )
            return result
        except Exception as e:
            frappe.msgprint(
                _("Webhook registration failed: {0}").format(str(e)),
                indicator="red",
                alert=True,
            )
            return {"status": "error", "message": str(e)}
