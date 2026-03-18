"""Tests for boleto_service module."""

import unittest
from unittest.mock import MagicMock, patch
import sys

# Ensure frappe mock is in place before import
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils
frappe = sys.modules["frappe"]

# Mock requests before importing (used by inter_client at module level)
sys.modules.setdefault("requests", MagicMock())

from brazil_module.services.banking.boleto_service import (
    create_boleto_from_invoice,
    poll_boleto_status,
    cancel_boleto,
    _handle_boleto_payment,
)
import brazil_module.services.banking.boleto_service as _bs_mod

# Patch module-level frappe.utils bindings
_bs_mod.flt = lambda x, precision=None: float(x)
_bs_mod.today = lambda: "2024-01-15"
_bs_mod.getdate = lambda x: x
_bs_mod.now_datetime = lambda: "2024-01-15 12:00:00"


def _reset_frappe():
    """Reset frappe mock and clear any leaked side_effects."""
    frappe.reset_mock()
    frappe._ = lambda x: x
    frappe.get_doc.side_effect = None
    frappe.get_single.side_effect = None
    frappe.db.get_value.side_effect = None
    frappe.db.exists.side_effect = None
    frappe.new_doc.side_effect = None
    frappe.throw.side_effect = None
    frappe.get_all.side_effect = None
    frappe.as_json.return_value = "{}"


class TestCreateBoletoFromInvoice(unittest.TestCase):
    """Tests for create_boleto_from_invoice."""

    def tearDown(self):
        frappe.get_doc.side_effect = None
        frappe.get_single.side_effect = None
        frappe.db.get_value.side_effect = None
        frappe.db.exists.side_effect = None
        frappe.new_doc.side_effect = None
        frappe.throw.side_effect = None

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_creates_boleto_successfully(self, MockClient):
        """Boleto is created and returned when invoice is valid."""
        _reset_frappe()

        # Set up invoice mock
        invoice = MagicMock()
        invoice.docstatus = 1
        invoice.outstanding_amount = 1500.00
        invoice.company = "Test Company"
        invoice.customer = "Customer A"
        invoice.customer_name = "Customer A"
        invoice.customer_address = None

        # Set up customer mock
        customer = MagicMock()
        customer.customer_name = "Customer A"
        customer.tax_id = "12345678901"
        customer.name = "Customer A"

        def get_doc_dispatch(doctype, name=None):
            if doctype == "Sales Invoice":
                return invoice
            if doctype == "Customer":
                return customer
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_dispatch

        # Settings
        settings = MagicMock()
        settings.default_days_to_due = 30
        settings.auto_cancel_expired_days = 5
        settings.enable_pix_on_boleto = False
        frappe.get_single.return_value = settings

        # Account found
        frappe.db.get_value.return_value = "ACC-001"

        # API response
        api_response = {
            "nossoNumero": "00001",
            "codigoBarras": "12345",
            "linhaDigitavel": "12345.12345",
            "codigoSolicitacao": "REQ-001",
        }
        mock_client_instance = MagicMock()
        mock_client_instance.create_boleto.return_value = api_response
        MockClient.return_value = mock_client_instance

        # New boleto doc
        boleto_doc = MagicMock()
        boleto_doc.name = "BOL-001"
        boleto_doc.pix_copia_cola = ""
        frappe.new_doc.return_value = boleto_doc

        result = create_boleto_from_invoice("INV-001")

        self.assertEqual(result, "BOL-001")
        frappe.new_doc.assert_called_with("Inter Boleto")
        boleto_doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_throws_on_unsubmitted_invoice(self):
        """Should throw when invoice is not submitted (docstatus != 1)."""
        _reset_frappe()
        frappe.throw.side_effect = Exception("thrown")

        invoice = MagicMock()
        invoice.docstatus = 0
        frappe.get_doc.return_value = invoice

        with self.assertRaises(Exception):
            create_boleto_from_invoice("INV-002")

        frappe.throw.assert_called_once()

    def test_throws_on_zero_outstanding(self):
        """Should throw when outstanding_amount is zero or negative."""
        _reset_frappe()
        frappe.throw.side_effect = Exception("thrown")

        invoice = MagicMock()
        invoice.docstatus = 1
        invoice.outstanding_amount = 0
        frappe.get_doc.return_value = invoice

        with self.assertRaises(Exception):
            create_boleto_from_invoice("INV-003")

        frappe.throw.assert_called_once()

    def test_throws_when_no_account_found(self):
        """Should throw when no Inter Company Account exists for company."""
        _reset_frappe()
        frappe.throw.side_effect = Exception("thrown")

        invoice = MagicMock()
        invoice.docstatus = 1
        invoice.outstanding_amount = 500.00
        invoice.company = "No Account Co"
        frappe.get_doc.return_value = invoice

        settings = MagicMock()
        frappe.get_single.return_value = settings

        # No account found
        frappe.db.get_value.return_value = None

        with self.assertRaises(Exception):
            create_boleto_from_invoice("INV-004")

        frappe.throw.assert_called_once()


class TestPollBoletoStatus(unittest.TestCase):
    """Tests for poll_boleto_status."""

    def tearDown(self):
        frappe.get_doc.side_effect = None
        frappe.get_single.side_effect = None
        frappe.db.get_value.side_effect = None
        frappe.db.exists.side_effect = None
        frappe.new_doc.side_effect = None
        frappe.throw.side_effect = None

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_paid_status_updates(self, MockClient):
        """Boleto marked as Paid when bank returns PAGO."""
        _reset_frappe()

        boleto = MagicMock()
        boleto.name = "BOL-010"
        boleto.inter_request_code = "REQ-010"
        boleto.inter_company_account = "ACC-001"
        boleto.status = "Registered"
        boleto.sales_invoice = "INV-010"
        boleto.payment_entry = None
        boleto.valor_nominal = 1000
        boleto.nosso_numero = "00010"

        frappe.get_doc.return_value = boleto

        # Settings for _handle_boleto_payment -- disable auto create
        settings = MagicMock()
        settings.auto_create_payment_entry = False
        frappe.get_single.return_value = settings

        mock_client = MagicMock()
        mock_client.get_boleto.return_value = {
            "situacao": "PAGO",
            "valorTotalRecebimento": 1000.00,
            "dataPagamento": "2024-01-15",
        }
        MockClient.return_value = mock_client

        results = poll_boleto_status("BOL-010")

        self.assertEqual(results["paid"], 1)
        self.assertEqual(results["updated"], 1)
        self.assertEqual(boleto.status, "Paid")
        boleto.save.assert_called_with(ignore_permissions=True)

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_overdue_status_updates(self, MockClient):
        """Boleto marked as Overdue when bank returns VENCIDO."""
        _reset_frappe()

        boleto = MagicMock()
        boleto.name = "BOL-011"
        boleto.inter_request_code = "REQ-011"
        boleto.inter_company_account = "ACC-001"
        boleto.status = "Registered"

        frappe.get_doc.return_value = boleto

        mock_client = MagicMock()
        mock_client.get_boleto.return_value = {"situacao": "VENCIDO"}
        MockClient.return_value = mock_client

        results = poll_boleto_status("BOL-011")

        self.assertEqual(results["updated"], 1)
        self.assertEqual(boleto.status, "Overdue")

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_cancelled_status_updates(self, MockClient):
        """Boleto marked as Cancelled when bank returns CANCELADO."""
        _reset_frappe()

        boleto = MagicMock()
        boleto.name = "BOL-012"
        boleto.inter_request_code = "REQ-012"
        boleto.inter_company_account = "ACC-001"
        boleto.status = "Registered"

        frappe.get_doc.return_value = boleto

        mock_client = MagicMock()
        mock_client.get_boleto.return_value = {"situacao": "CANCELADO"}
        MockClient.return_value = mock_client

        results = poll_boleto_status("BOL-012")

        self.assertEqual(results["updated"], 1)
        self.assertEqual(boleto.status, "Cancelled")

    def test_skips_boleto_without_request_code(self):
        """Boleto without inter_request_code is skipped entirely."""
        _reset_frappe()

        boleto = MagicMock()
        boleto.name = "BOL-013"
        boleto.inter_request_code = ""  # empty -> falsy
        boleto.status = "Registered"

        frappe.get_doc.return_value = boleto

        results = poll_boleto_status("BOL-013")

        self.assertEqual(results["checked"], 0)
        self.assertEqual(results["updated"], 0)


class TestCancelBoleto(unittest.TestCase):
    """Tests for cancel_boleto."""

    def tearDown(self):
        frappe.get_doc.side_effect = None
        frappe.get_single.side_effect = None
        frappe.db.get_value.side_effect = None
        frappe.db.exists.side_effect = None
        frappe.new_doc.side_effect = None
        frappe.throw.side_effect = None

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_cancels_registered_boleto(self, MockClient):
        """Registered boleto is cancelled successfully."""
        _reset_frappe()

        boleto = MagicMock()
        boleto.name = "BOL-020"
        boleto.status = "Registered"
        boleto.inter_request_code = "REQ-020"
        boleto.inter_company_account = "ACC-001"

        frappe.get_doc.return_value = boleto

        mock_client = MagicMock()
        mock_client.cancel_boleto.return_value = {"status": "ok"}
        MockClient.return_value = mock_client

        result = cancel_boleto("BOL-020")

        self.assertEqual(result["status"], "success")
        self.assertEqual(boleto.status, "Cancelled")
        boleto.save.assert_called_once_with(ignore_permissions=True)

    def test_throws_on_already_paid_boleto(self):
        """Should throw when trying to cancel a paid boleto."""
        _reset_frappe()
        frappe.throw.side_effect = Exception("thrown")

        boleto = MagicMock()
        boleto.name = "BOL-021"
        boleto.status = "Paid"
        boleto.inter_request_code = "REQ-021"

        frappe.get_doc.return_value = boleto

        with self.assertRaises(Exception):
            cancel_boleto("BOL-021")

        frappe.throw.assert_called_once()


class TestHandleBoletoPayment(unittest.TestCase):
    """Tests for _handle_boleto_payment."""

    def tearDown(self):
        frappe.get_doc.side_effect = None
        frappe.get_single.side_effect = None
        frappe.db.get_value.side_effect = None
        frappe.db.exists.side_effect = None
        frappe.new_doc.side_effect = None
        frappe.throw.side_effect = None

    def test_creates_payment_entry_when_auto_create_enabled(self):
        """Payment Entry is created when auto_create_payment_entry is on."""
        _reset_frappe()

        boleto = MagicMock()
        boleto.name = "BOL-030"
        boleto.sales_invoice = "INV-030"
        boleto.payment_entry = None  # no existing PE
        boleto.inter_company_account = "ACC-001"
        boleto.valor_pago = 2000.00
        boleto.valor_nominal = 2000.00
        boleto.nosso_numero = "00030"
        boleto.data_pagamento = "2024-01-15"

        settings = MagicMock()
        settings.auto_create_payment_entry = True
        frappe.get_single.return_value = settings

        invoice = MagicMock()
        invoice.outstanding_amount = 2000.00
        invoice.customer = "Customer X"
        invoice.company = "Test Company"
        invoice.debit_to = "Debtors - TC"

        account_doc = MagicMock()
        account_doc.bank_account = "Bank - TC"

        def get_doc_dispatch(doctype, name=None):
            if doctype == "Sales Invoice":
                return invoice
            if doctype == "Inter Company Account":
                return account_doc
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_dispatch

        pe = MagicMock()
        pe.name = "PE-030"
        frappe.new_doc.return_value = pe
        frappe.db.get_value.return_value = "GL Account - TC"

        _handle_boleto_payment(boleto)

        frappe.new_doc.assert_called_once_with("Payment Entry")
        pe.insert.assert_called_once_with(ignore_permissions=True)
        pe.submit.assert_called_once()
        self.assertEqual(boleto.payment_entry, "PE-030")

    def test_skips_when_payment_entry_already_exists(self):
        """No Payment Entry is created if boleto already has one."""
        _reset_frappe()

        boleto = MagicMock()
        boleto.name = "BOL-031"
        boleto.sales_invoice = "INV-031"
        boleto.payment_entry = "PE-EXISTING"  # already has PE

        settings = MagicMock()
        settings.auto_create_payment_entry = True
        frappe.get_single.return_value = settings

        _handle_boleto_payment(boleto)

        frappe.new_doc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
