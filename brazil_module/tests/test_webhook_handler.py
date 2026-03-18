"""Tests for webhook handler service."""

import unittest
from unittest.mock import MagicMock
import sys

# Ensure frappe mock is in place before import
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    frappe_mock._ = lambda x: x
    sys.modules["frappe"] = frappe_mock
    sys.modules["frappe.utils"] = frappe_mock.utils

# Mock banking service dependencies before import
for mod in [
    "brazil_module.services.banking.pix_service",
    "brazil_module.services.banking.boleto_service",
    "brazil_module.services.banking.inter_client",
    "brazil_module.services.banking.auth_manager",
]:
    sys.modules.setdefault(mod, MagicMock())

import frappe
from brazil_module.services.banking.webhook_handler import (
    _identify_event_type,
    process_webhook,
    _handle_pix_received,
    _handle_boleto_paid,
)
import brazil_module.services.banking.webhook_handler as _wh_mod


def _reset_frappe():
    """Reset frappe mock and clear any leaked side_effects."""
    frappe.reset_mock()
    frappe.db.get_value.side_effect = None
    frappe.db.get_value.return_value = None
    frappe.new_doc.side_effect = None
    # Patch module-level imports
    _wh_mod.flt = float
    _wh_mod.now_datetime = lambda: "2024-01-01 12:00:00"


class TestIdentifyEventType(unittest.TestCase):
    def test_pix_event(self):
        data = {"pix": [{"txid": "abc123"}]}
        self.assertEqual(_identify_event_type(data), "pix_received")

    def test_boleto_by_situacao(self):
        data = {"situacao": "PAGO"}
        self.assertEqual(_identify_event_type(data), "boleto_paid")

    def test_boleto_by_request_code(self):
        data = {"codigoSolicitacao": "abc-123"}
        self.assertEqual(_identify_event_type(data), "boleto_paid")

    def test_unknown_event(self):
        data = {"something_else": "value"}
        self.assertEqual(_identify_event_type(data), "unknown")

    def test_empty_data(self):
        self.assertEqual(_identify_event_type({}), "unknown")


class TestProcessWebhook(unittest.TestCase):
    def test_creates_log_entry(self):
        _reset_frappe()
        log = MagicMock()
        frappe.new_doc.return_value = log

        process_webhook({"unknown_field": True}, "127.0.0.1")
        frappe.new_doc.assert_called_with("Inter Webhook Log")

    def test_returns_received_status(self):
        _reset_frappe()
        log = MagicMock()
        frappe.new_doc.return_value = log

        result = process_webhook({"unknown_field": True}, "127.0.0.1")
        self.assertEqual(result["status"], "received")

    def test_exception_in_handler_still_returns_received(self):
        _reset_frappe()
        log = MagicMock()
        frappe.new_doc.return_value = log
        # Cause exception inside the try block via _handle_pix_received
        frappe.db.get_value.side_effect = Exception("DB error")

        result = process_webhook({"pix": [{"txid": "abc", "valor": "10"}]}, "127.0.0.1")
        # Should handle exception gracefully and still return received
        self.assertEqual(result["status"], "received")
        # Clean up side_effect
        frappe.db.get_value.side_effect = None


class TestHandlePixReceived(unittest.TestCase):
    def test_match_pix_charge(self):
        _reset_frappe()
        charge = MagicMock()
        charge.name = "PIX-001"
        charge.status = "Pending"
        frappe.db.get_value.return_value = "PIX-001"
        frappe.get_doc.return_value = charge

        log = MagicMock()
        data = {"pix": [{"txid": "abc123", "valor": "100.00", "horario": "2024-01-01T10:00:00", "endToEndId": "E123"}]}
        result = _handle_pix_received(data, log)
        self.assertIn("pix_payments", result)

    def test_no_matching_charge(self):
        _reset_frappe()
        frappe.db.get_value.return_value = None

        log = MagicMock()
        data = {"pix": [{"txid": "unknown_txid"}]}
        result = _handle_pix_received(data, log)
        self.assertIn("pix_payments", result)


class TestHandleBolletoPaid(unittest.TestCase):
    def test_match_by_request_code(self):
        _reset_frappe()
        boleto = MagicMock()
        boleto.name = "BOL-001"
        boleto.status = "Registered"
        frappe.db.get_value.return_value = "BOL-001"
        frappe.get_doc.return_value = boleto

        log = MagicMock()
        data = {"codigoSolicitacao": "REQ-123", "valorTotalRecebimento": "500.00"}
        result = _handle_boleto_paid(data, log)
        self.assertIn("boleto", result)

    def test_no_match(self):
        _reset_frappe()
        frappe.db.get_value.return_value = None

        log = MagicMock()
        data = {"codigoSolicitacao": "UNKNOWN"}
        result = _handle_boleto_paid(data, log)
        self.assertIn("boleto", result)


if __name__ == "__main__":
    unittest.main()
