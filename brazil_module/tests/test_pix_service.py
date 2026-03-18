"""Tests for PIX charge service."""

import unittest
from unittest.mock import MagicMock, patch
import sys
from datetime import date, datetime

# Ensure frappe mock is in place before import
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

# Mock requests before importing (used by inter_client at module level)
sys.modules.setdefault("requests", MagicMock())

import brazil_module.services.banking.pix_service as _ps_mod
from brazil_module.services.banking.pix_service import (
    create_pix_charge_from_invoice,
    create_scheduled_pix_charge,
    poll_pix_charge_status,
    _handle_pix_payment,
)

def _flt(value, precision=None):
    """Mimic frappe.utils.flt: convert to float, optionally round."""
    v = float(value)
    if precision is not None:
        return round(v, precision)
    return v


# Patch module-level frappe.utils bindings
_ps_mod.flt = _flt
_ps_mod.today = lambda: "2024-01-15"
_ps_mod.now_datetime = lambda: datetime(2024, 1, 15, 12, 0, 0)


def _reset_frappe():
    """Reset frappe mock and clear any leaked side_effects."""
    frappe.reset_mock()
    frappe._ = lambda x: x
    frappe.throw.side_effect = None
    frappe.get_doc.side_effect = None
    frappe.get_doc.return_value = None
    frappe.new_doc.side_effect = None
    frappe.new_doc.return_value = None
    frappe.get_single.side_effect = None
    frappe.get_single.return_value = None
    frappe.get_all.side_effect = None
    frappe.get_all.return_value = None
    frappe.db.get_value.side_effect = None
    frappe.db.get_value.return_value = None
    frappe.db.get_single_value.side_effect = None
    frappe.db.get_single_value.return_value = None
    frappe.as_json.side_effect = None
    frappe.as_json.return_value = "{}"
    frappe.log_error.side_effect = None
    # Re-patch module-level bindings
    _ps_mod.flt = _flt
    _ps_mod.today = lambda: "2024-01-15"
    _ps_mod.now_datetime = lambda: datetime(2024, 1, 15, 12, 0, 0)


def _make_invoice(docstatus=1, outstanding=1000.0, company="Test Co",
                  customer="Customer A", name="SINV-001"):
    """Build a mock Sales Invoice."""
    inv = MagicMock()
    inv.name = name
    inv.docstatus = docstatus
    inv.outstanding_amount = outstanding
    inv.company = company
    inv.customer = customer
    inv.debit_to = "Debtors - TC"
    return inv


def _make_customer(name="Customer A", tax_id="12345678901"):
    """Build a mock Customer."""
    cust = MagicMock()
    cust.customer_name = name
    cust.tax_id = tax_id
    return cust


def _make_settings(pix_expiration_seconds=3600, auto_create_payment_entry=True,
                   enabled=True):
    """Build a mock Banco Inter Settings."""
    s = MagicMock()
    s.pix_expiration_seconds = pix_expiration_seconds
    s.auto_create_payment_entry = auto_create_payment_entry
    s.enabled = enabled
    return s


# ---------------------------------------------------------------------------
# 1. TestCreatePixChargeFromInvoice
# ---------------------------------------------------------------------------
class TestCreatePixChargeFromInvoice(unittest.TestCase):

    def tearDown(self):
        _reset_frappe()

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_creates_charge_successfully(self, MockClient):
        """A submitted invoice with outstanding amount produces a PIX charge."""
        _reset_frappe()

        invoice = _make_invoice()
        customer = _make_customer()
        settings = _make_settings()
        pix_charge = MagicMock()
        pix_charge.name = "PIX-001"
        pix_charge.pix_copia_cola = ""

        frappe.get_doc.side_effect = lambda dt, name=None: {
            ("Sales Invoice", "SINV-001"): invoice,
            ("Customer", "Customer A"): customer,
        }.get((dt, name), MagicMock())

        frappe.get_single.return_value = settings
        frappe.db.get_value.return_value = "ACCT-001"
        frappe.new_doc.return_value = pix_charge

        mock_client_instance = MagicMock()
        mock_client_instance.create_pix_charge.return_value = {
            "txid": "abc123",
            "chave": "pix@example.com",
            "pixCopiaECola": "",
        }
        MockClient.return_value = mock_client_instance

        result = create_pix_charge_from_invoice("SINV-001", expiration_seconds=600)

        self.assertEqual(result, "PIX-001")
        frappe.new_doc.assert_called_with("Inter PIX Charge")
        pix_charge.insert.assert_called_once_with(ignore_permissions=True)
        self.assertEqual(pix_charge.charge_type, "Immediate")
        self.assertEqual(pix_charge.status, "Active")
        self.assertEqual(pix_charge.calendario_expiracao, 600)

    def test_throws_on_unsubmitted_invoice(self):
        """An unsubmitted (draft) invoice causes frappe.throw."""
        _reset_frappe()
        frappe.throw.side_effect = Exception

        invoice = _make_invoice(docstatus=0)
        frappe.get_doc.return_value = invoice

        with self.assertRaises(Exception):
            create_pix_charge_from_invoice("SINV-001")

        frappe.throw.assert_called_once()

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_uses_default_expiration_from_settings(self, MockClient):
        """When expiration_seconds is not given, the value from settings is used."""
        _reset_frappe()

        invoice = _make_invoice()
        customer = _make_customer()
        settings = _make_settings(pix_expiration_seconds=7200)
        pix_charge = MagicMock()
        pix_charge.name = "PIX-002"
        pix_charge.pix_copia_cola = ""

        frappe.get_doc.side_effect = lambda dt, name=None: {
            ("Sales Invoice", "SINV-001"): invoice,
            ("Customer", "Customer A"): customer,
        }.get((dt, name), MagicMock())

        frappe.get_single.return_value = settings
        frappe.db.get_value.return_value = "ACCT-001"
        frappe.new_doc.return_value = pix_charge

        mock_client_instance = MagicMock()
        mock_client_instance.create_pix_charge.return_value = {
            "txid": "def456",
            "chave": "pix@example.com",
            "pixCopiaECola": "",
        }
        MockClient.return_value = mock_client_instance

        create_pix_charge_from_invoice("SINV-001")

        # The charge should use 7200 from settings
        self.assertEqual(pix_charge.calendario_expiracao, 7200)


# ---------------------------------------------------------------------------
# 2. TestCreateScheduledPixCharge
# ---------------------------------------------------------------------------
class TestCreateScheduledPixCharge(unittest.TestCase):

    def tearDown(self):
        _reset_frappe()

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_creates_scheduled_charge(self, MockClient):
        """A scheduled PIX charge is created with charge_type Scheduled."""
        _reset_frappe()

        invoice = _make_invoice()
        customer = _make_customer()
        pix_charge = MagicMock()
        pix_charge.name = "PIX-010"
        pix_charge.pix_copia_cola = ""

        frappe.get_doc.side_effect = lambda dt, name=None: {
            ("Sales Invoice", "SINV-001"): invoice,
            ("Customer", "Customer A"): customer,
        }.get((dt, name), MagicMock())

        frappe.db.get_value.return_value = "ACCT-001"
        frappe.new_doc.return_value = pix_charge

        mock_client_instance = MagicMock()
        mock_client_instance.create_pix_charge_with_due_date.return_value = {
            "txid": "sched123",
            "pixCopiaECola": "",
        }
        MockClient.return_value = mock_client_instance

        result = create_scheduled_pix_charge("SINV-001", due_date=date(2024, 2, 15))

        self.assertEqual(result, "PIX-010")
        self.assertEqual(pix_charge.charge_type, "Scheduled")
        self.assertEqual(pix_charge.status, "Active")
        pix_charge.insert.assert_called_once_with(ignore_permissions=True)

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_includes_fine_and_interest_params(self, MockClient):
        """Fine and interest percentages are included in the charge_data."""
        _reset_frappe()

        invoice = _make_invoice()
        customer = _make_customer()
        pix_charge = MagicMock()
        pix_charge.name = "PIX-011"
        pix_charge.pix_copia_cola = ""

        frappe.get_doc.side_effect = lambda dt, name=None: {
            ("Sales Invoice", "SINV-001"): invoice,
            ("Customer", "Customer A"): customer,
        }.get((dt, name), MagicMock())

        frappe.db.get_value.return_value = "ACCT-001"
        frappe.new_doc.return_value = pix_charge

        mock_client_instance = MagicMock()
        mock_client_instance.create_pix_charge_with_due_date.return_value = {
            "txid": "sched456",
            "pixCopiaECola": "",
        }
        MockClient.return_value = mock_client_instance

        create_scheduled_pix_charge(
            "SINV-001",
            due_date=date(2024, 2, 15),
            fine_percent=2.0,
            interest_percent=0.033,
        )

        # Verify the client was called with charge data containing fine/interest
        call_args = mock_client_instance.create_pix_charge_with_due_date.call_args
        charge_data = call_args[0][1]
        self.assertIn("multa", charge_data["valor"])
        self.assertEqual(charge_data["valor"]["multa"]["valorPerc"], "2.00")
        self.assertIn("juros", charge_data["valor"])
        self.assertEqual(charge_data["valor"]["juros"]["valorPerc"], "0.03")


# ---------------------------------------------------------------------------
# 3. TestPollPixChargeStatus
# ---------------------------------------------------------------------------
class TestPollPixChargeStatus(unittest.TestCase):

    def tearDown(self):
        _reset_frappe()

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_paid_status_updates_and_calls_handle_payment(self, MockClient):
        """A CONCLUIDA response updates the charge to Paid and triggers payment handling."""
        _reset_frappe()

        charge = MagicMock()
        charge.name = "PIX-020"
        charge.txid = "txid_paid"
        charge.charge_type = "Immediate"
        charge.inter_company_account = "ACCT-001"
        charge.sales_invoice = "SINV-001"
        charge.payment_entry = None
        charge.data_expiracao = None

        frappe.get_doc.side_effect = lambda dt, name=None: {
            ("Inter PIX Charge", "PIX-020"): charge,
        }.get((dt, name), MagicMock())

        mock_client_instance = MagicMock()
        mock_client_instance.get_pix_charge.return_value = {
            "status": "CONCLUIDA",
            "pix": [{
                "valor": "1000.00",
                "horario": "2024-01-15T10:00:00",
                "endToEndId": "E2E123",
            }],
        }
        MockClient.return_value = mock_client_instance

        # Mock _handle_pix_payment to avoid side effects
        with patch.object(_ps_mod, "_handle_pix_payment") as mock_handle:
            results = poll_pix_charge_status("PIX-020")

        self.assertEqual(results["paid"], 1)
        self.assertEqual(charge.status, "Paid")
        charge.save.assert_called_with(ignore_permissions=True)
        mock_handle.assert_called_once_with(charge)

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_expired_charge_marked_as_expired(self, MockClient):
        """A charge past its expiration date is marked Expired."""
        _reset_frappe()

        charge = MagicMock()
        charge.name = "PIX-021"
        charge.txid = "txid_exp"
        charge.charge_type = "Immediate"
        charge.inter_company_account = "ACCT-001"
        charge.data_expiracao = "2024-01-14 12:00:00"

        frappe.get_doc.side_effect = lambda dt, name=None: {
            ("Inter PIX Charge", "PIX-021"): charge,
        }.get((dt, name), MagicMock())

        mock_client_instance = MagicMock()
        # Return an ATIVA (active) status so we fall through to expiration check
        mock_client_instance.get_pix_charge.return_value = {"status": "ATIVA"}
        MockClient.return_value = mock_client_instance

        # Make now_datetime() > data_expiracao so the expiration branch fires
        mock_now = MagicMock()
        frappe.utils.now_datetime.return_value = mock_now
        mock_exp = MagicMock()
        frappe.utils.get_datetime.return_value = mock_exp
        mock_now.__gt__ = lambda self, other: True  # now > expiration

        results = poll_pix_charge_status("PIX-021")

        self.assertEqual(results["expired"], 1)
        self.assertEqual(charge.status, "Expired")
        charge.save.assert_called_with(ignore_permissions=True)

    @patch("brazil_module.services.banking.inter_client.InterAPIClient")
    def test_skips_charge_without_txid(self, MockClient):
        """A charge without a txid is silently skipped."""
        _reset_frappe()

        charge = MagicMock()
        charge.name = "PIX-022"
        charge.txid = ""  # Empty txid

        frappe.get_doc.side_effect = lambda dt, name=None: {
            ("Inter PIX Charge", "PIX-022"): charge,
        }.get((dt, name), MagicMock())

        results = poll_pix_charge_status("PIX-022")

        self.assertEqual(results["checked"], 0)
        self.assertEqual(results["paid"], 0)
        self.assertEqual(results["expired"], 0)
        MockClient.assert_not_called()


# ---------------------------------------------------------------------------
# 4. TestHandlePixPayment
# ---------------------------------------------------------------------------
class TestHandlePixPayment(unittest.TestCase):

    def tearDown(self):
        _reset_frappe()

    def test_creates_payment_entry(self):
        """When auto_create is enabled, a Payment Entry is created and submitted."""
        _reset_frappe()

        settings = _make_settings(auto_create_payment_entry=True)
        frappe.get_single.return_value = settings

        invoice = _make_invoice()
        account_doc = MagicMock()
        account_doc.bank_account = "BA-001"

        frappe.get_doc.side_effect = lambda dt, name=None: {
            ("Sales Invoice", "SINV-001"): invoice,
            ("Inter Company Account", "ACCT-001"): account_doc,
        }.get((dt, name), MagicMock())

        frappe.db.get_value.return_value = "Bank - TC"

        pe = MagicMock()
        pe.name = "PE-001"
        frappe.new_doc.return_value = pe

        charge = MagicMock()
        charge.name = "PIX-030"
        charge.payment_entry = None
        charge.sales_invoice = "SINV-001"
        charge.inter_company_account = "ACCT-001"
        charge.valor_pago = 1000.0
        charge.valor = 1000.0
        charge.txid = "txid_pay"
        charge.data_pagamento = "2024-01-15"

        _handle_pix_payment(charge)

        frappe.new_doc.assert_called_with("Payment Entry")
        pe.insert.assert_called_once_with(ignore_permissions=True)
        pe.submit.assert_called_once()
        self.assertEqual(charge.payment_entry, "PE-001")

    def test_skips_when_auto_create_disabled(self):
        """No Payment Entry is created when auto_create_payment_entry is off."""
        _reset_frappe()

        settings = _make_settings(auto_create_payment_entry=False)
        frappe.get_single.return_value = settings

        charge = MagicMock()
        charge.name = "PIX-031"
        charge.payment_entry = None
        charge.sales_invoice = "SINV-001"

        _handle_pix_payment(charge)

        frappe.new_doc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
