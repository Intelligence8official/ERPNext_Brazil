import sys
from datetime import date
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]
sys.modules.setdefault("brazil_module.services.intelligence.recurring.planning_loop", MagicMock())
sys.modules.setdefault("brazil_module.services.intelligence.notifications", MagicMock())

import unittest

import brazil_module.services.intelligence.analytics.anomaly_detector as _ad_mod
_ad_mod.flt = float

from brazil_module.services.intelligence.analytics.anomaly_detector import (
    _check_nf_po_value_mismatch,
    _check_duplicate_payments,
    _check_unexpected_charges,
    _notify_anomalies,
)


class TestNfPoMismatch(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.sql.return_value = []

    def test_detects_mismatch(self):
        frappe.db.sql.return_value = [
            {"name": "NF-001", "valor_total": 12000, "razao_social": "Test", "po_name": "PO-001", "po_total": 10000}
        ]
        results = _check_nf_po_value_mismatch()
        self.assertEqual(len(results), 1)
        self.assertIn("20.0%", results[0]["message"])

    def test_no_mismatch_within_tolerance(self):
        frappe.db.sql.return_value = [
            {"name": "NF-001", "valor_total": 10200, "razao_social": "Test", "po_name": "PO-001", "po_total": 10000}
        ]
        results = _check_nf_po_value_mismatch()
        self.assertEqual(len(results), 0)


class TestDuplicatePayments(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_detects_duplicates(self):
        frappe.db.sql.return_value = [
            {"pe1": "PE-001", "pe2": "PE-002", "party": "S-001", "party_name": "Test",
             "paid_amount": 5000, "date1": "2026-03-20", "date2": "2026-03-22"}
        ]
        results = _check_duplicate_payments()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["severity"], "high")


class TestUnexpectedCharges(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.exists.return_value = None

    def test_detects_pi_without_po(self):
        frappe.db.sql.return_value = [
            {"name": "PI-001", "supplier": "S-001", "supplier_name": "Test", "grand_total": 3000, "posting_date": "2026-03-25"}
        ]
        frappe.db.exists.return_value = None
        results = _check_unexpected_charges()
        self.assertEqual(len(results), 1)


class TestNotifyAnomalies(unittest.TestCase):
    def test_sends_notification(self):
        anomalies = [
            {"type": "test", "severity": "high", "message": "Test anomaly"},
        ]
        _notify_anomalies(anomalies)  # Should not raise


if __name__ == "__main__":
    unittest.main()
