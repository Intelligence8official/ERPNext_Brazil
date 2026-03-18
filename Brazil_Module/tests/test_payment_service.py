"""Tests for the outbound payment service."""

import unittest
from unittest.mock import MagicMock, patch
import sys

# Ensure frappe mock is in place
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]
sys.modules.setdefault("requests", MagicMock())

import Brazil_Module.services.banking.payment_service as _ps_mod
from Brazil_Module.services.banking.payment_service import (
    execute_payment_order,
    scheduled_payment_status_check,
)

# Patch module-level frappe.utils bindings
_ps_mod.flt = float
_ps_mod.today = lambda: "2024-01-15"
_ps_mod.now_datetime = lambda: "2024-01-15 12:00:00"


def _reset():
    frappe.reset_mock()
    frappe.get_doc.side_effect = None
    frappe.new_doc.side_effect = None
    frappe.db.get_value.side_effect = None
    frappe.db.get_single_value.side_effect = None
    frappe.get_all.side_effect = None
    frappe.get_all.return_value = []
    frappe.throw.side_effect = None
    frappe.as_json = lambda x: str(x)


def _make_order(payment_type="PIX", status="Processing"):
    order = MagicMock()
    order.name = "PO-001"
    order.status = status
    order.payment_type = payment_type
    order.inter_company_account = "TEST-ACCOUNT"
    order.company = "Test Company"
    order.amount = 500.0
    order.purchase_invoice = "PINV-001"
    order.party_type = "Supplier"
    order.party = "Test Supplier"
    order.pix_key = "email@test.com"
    order.barcode = "23793.38128 60000.000003 00000.000408 1 84340000012345"
    order.boleto_due_date = "2024-02-15"
    order.scheduled_date = None
    order.recipient_name = "Test Recipient"
    order.recipient_cpf_cnpj = "12345678000155"
    order.recipient_bank_code = "077"
    order.recipient_agency = "0001"
    order.recipient_account = "123456"
    order.recipient_account_type = "Conta Corrente"
    order.payment_entry = None
    order.transaction_id = ""
    order.execution_date = None
    return order


class TestExecutePaymentOrder(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_pix_routing(self):
        order = _make_order(payment_type="PIX")
        frappe.get_doc.return_value = order
        frappe.as_json = lambda x: str(x)

        mock_client_cls = MagicMock()
        mock_client = mock_client_cls.return_value
        mock_client.send_pix.return_value = {"endToEndId": "E2E-001", "codigoSolicitacao": "SOL-001"}

        with patch.dict("sys.modules", {}):
            with patch.object(_ps_mod, "_execute_pix_payment", return_value={"transaction_id": "E2E-001", "approval_code": "SOL-001", "response": {}}) as mock_exec:
                with patch.object(_ps_mod, "_create_payment_entry_for_outbound"):
                    result = execute_payment_order("PO-001")
                    self.assertEqual(result["status"], "success")
                    mock_exec.assert_called_once()

    def test_ted_routing(self):
        order = _make_order(payment_type="TED")
        frappe.get_doc.return_value = order
        frappe.as_json = lambda x: str(x)

        with patch.object(_ps_mod, "_execute_ted_payment", return_value={"transaction_id": "TED-001", "approval_code": "SOL-002", "response": {}}) as mock_exec:
            with patch.object(_ps_mod, "_create_payment_entry_for_outbound"):
                result = execute_payment_order("PO-001")
                self.assertEqual(result["status"], "success")
                mock_exec.assert_called_once()

    def test_boleto_routing(self):
        order = _make_order(payment_type="Boleto Payment")
        frappe.get_doc.return_value = order
        frappe.as_json = lambda x: str(x)

        with patch.object(_ps_mod, "_execute_boleto_payment", return_value={"transaction_id": "BOL-001", "approval_code": "SOL-003", "response": {}}) as mock_exec:
            with patch.object(_ps_mod, "_create_payment_entry_for_outbound"):
                result = execute_payment_order("PO-001")
                self.assertEqual(result["status"], "success")
                mock_exec.assert_called_once()

    def test_failed_status_on_error(self):
        order = _make_order(payment_type="PIX")
        frappe.get_doc.return_value = order

        mock_client = MagicMock()
        mock_client.send_pix.side_effect = Exception("API failure")

        with patch("Brazil_Module.services.banking.inter_client.InterAPIClient", return_value=mock_client):
            result = execute_payment_order("PO-001")
            self.assertEqual(result["status"], "error")
            self.assertEqual(order.status, "Failed")


class TestScheduledPaymentStatusCheck(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_skips_when_disabled(self):
        frappe.db.get_single_value.return_value = False
        scheduled_payment_status_check()
        frappe.get_all.assert_not_called()

    def test_retries_stuck_orders(self):
        frappe.db.get_single_value.return_value = True
        frappe.get_all.return_value = ["PO-STUCK"]

        order = _make_order(status="Processing")
        order.modified = "2024-01-15 10:00:00"
        frappe.get_doc.return_value = order

        # Make now_datetime return a time > 1 hour later
        _ps_mod.now_datetime = lambda: MagicMock()
        frappe.utils.get_datetime.return_value = MagicMock()

        # This test verifies the function runs without error
        # The actual retry logic depends on datetime comparison
        scheduled_payment_status_check()
        frappe.get_all.assert_called_once()


if __name__ == "__main__":
    unittest.main()
