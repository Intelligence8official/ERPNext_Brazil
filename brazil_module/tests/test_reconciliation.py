"""Tests for the auto-reconciliation service."""

import unittest
from unittest.mock import MagicMock, patch, call
import sys

# Ensure frappe mock is in place
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]
sys.modules.setdefault("requests", MagicMock())

import brazil_module.services.banking.reconciliation as _rec_mod
from brazil_module.services.banking.reconciliation import (
    batch_reconcile,
    _find_match,
    _match_by_inter_reference,
    _allocate_transaction,
)

# Patch module-level bindings
_rec_mod.flt = float


def _reset():
    frappe.reset_mock()
    frappe.get_doc.side_effect = None
    frappe.get_all.side_effect = None
    frappe.get_all.return_value = []
    frappe.get_single.side_effect = None
    frappe.db.get_value.side_effect = None
    frappe.db.exists.side_effect = None
    frappe.db.commit.side_effect = None


class TestFindMatch(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_boleto_reference_match(self):
        txn = {"deposit": 500.0, "withdrawal": 0, "reference_number": "NOSSO-123", "date": "2024-01-15"}

        with patch.object(_rec_mod, "_match_by_inter_reference", return_value={"doctype": "Sales Invoice", "name": "SINV-001", "amount": 500.0}):
            result = _find_match(txn, "BankAccount-001")
            self.assertIsNotNone(result)
            self.assertEqual(result["doctype"], "Sales Invoice")
            self.assertEqual(result["name"], "SINV-001")

    def test_pix_reference_match(self):
        txn = {"deposit": 200.0, "withdrawal": 0, "reference_number": "TXID-456", "date": "2024-01-15"}

        with patch.object(_rec_mod, "_match_by_inter_reference", return_value={"doctype": "Sales Invoice", "name": "SINV-002", "amount": 200.0}):
            result = _find_match(txn, "BankAccount-001")
            self.assertIsNotNone(result)
            self.assertEqual(result["name"], "SINV-002")

    def test_no_match_returns_none(self):
        txn = {"deposit": 100.0, "withdrawal": 0, "reference_number": "UNKNOWN", "date": "2024-01-15"}

        with patch.object(_rec_mod, "_match_by_inter_reference", return_value=None):
            with patch.object(_rec_mod, "_match_to_sales_invoice", return_value=None):
                result = _find_match(txn, "BankAccount-001")
                self.assertIsNone(result)


class TestBatchReconcile(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_matched_count(self):
        frappe.get_all.return_value = [
            {"name": "BT-001", "date": "2024-01-15", "deposit": 500.0, "withdrawal": 0, "description": "PIX", "reference_number": "REF-1"},
        ]

        with patch.object(_rec_mod, "_find_match", return_value={"doctype": "Sales Invoice", "name": "SINV-001", "amount": 500.0}):
            with patch.object(_rec_mod, "_allocate_transaction"):
                result = batch_reconcile("BankAccount-001")
                self.assertEqual(result["matched"], 1)
                self.assertEqual(result["unmatched"], 0)

    def test_unmatched_count(self):
        frappe.get_all.return_value = [
            {"name": "BT-002", "date": "2024-01-15", "deposit": 100.0, "withdrawal": 0, "description": "Unknown", "reference_number": ""},
        ]

        with patch.object(_rec_mod, "_find_match", return_value=None):
            result = batch_reconcile("BankAccount-001")
            self.assertEqual(result["matched"], 0)
            self.assertEqual(result["unmatched"], 1)

    def test_error_count(self):
        frappe.get_all.return_value = [
            {"name": "BT-003", "date": "2024-01-15", "deposit": 100.0, "withdrawal": 0, "description": "", "reference_number": ""},
        ]

        with patch.object(_rec_mod, "_find_match", side_effect=Exception("DB error")):
            result = batch_reconcile("BankAccount-001")
            self.assertEqual(result["errors"], 1)


class TestAllocateTransaction(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_appends_to_bank_txn(self):
        bt = MagicMock()
        frappe.get_doc.return_value = bt

        _allocate_transaction("BT-001", "Sales Invoice", "SINV-001", 500.0)

        bt.append.assert_called_once_with("payment_entries", {
            "payment_document": "Sales Invoice",
            "payment_entry": "SINV-001",
            "allocated_amount": 500.0,
        })
        bt.save.assert_called_once_with(ignore_permissions=True)
        frappe.db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
