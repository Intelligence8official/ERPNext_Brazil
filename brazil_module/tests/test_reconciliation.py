"""Tests for the auto-reconciliation service."""

import unittest
from types import SimpleNamespace
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
    on_payment_entry_submit,
)
from brazil_module.tests._payment_fakes import DOCTYPE, FakeDB

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
    # reset_mock() keeps return_value: without this the file only passes when an earlier test
    # module happened to leave `sql` returning a list (a bare mock is truthy and has len() == 0).
    frappe.db.sql.side_effect = None
    frappe.db.sql.return_value = []


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


class TestOnPaymentEntrySubmit(unittest.TestCase):
    """The hook used to write ``status = Completed`` on its own: a second, unguarded writer."""

    ORDER = "IPO-2026-00002"
    INVOICE = "ACC-PINV-2026-00031"

    def setUp(self):
        _reset()
        # Shadow frappe.db, never patch.object it: see _payment_fakes.patch_frappe.
        self._namespace = _rec_mod.frappe.__dict__
        self.addCleanup(self._restore_db, self._namespace.get("db"))
        self.fresh_db()

    def fresh_db(self):
        self.db = FakeDB()
        self._namespace["db"] = self.db

    def _restore_db(self, previous):
        if previous is None:
            self._namespace.pop("db", None)
        else:
            self._namespace["db"] = previous

    def add_order(self, status, payment_entry=None, invoice_lock=INVOICE):
        self.db.add(
            DOCTYPE, self.ORDER, docstatus=1, status=status, purchase_invoice=self.INVOICE,
            payment_entry=payment_entry, invoice_lock=invoice_lock,
        )

    def submit(self, entry="ACC-PAY-2026-00001", order=ORDER):
        fields = {"name": entry, "inter_payment_order": order}
        on_payment_entry_submit(SimpleNamespace(get=fields.get, **fields), "on_submit")

    def writes(self):
        return [event for event in self.db.events if event[0] == "set_value"]

    def test_it_never_writes_the_status(self):
        for status in ("Approved", "Processing", "Awaiting Bank", "Needs Verification", "Failed"):
            with self.subTest(status=status):
                self.fresh_db()
                self.add_order(status)

                self.submit()

                row = self.db.row(DOCTYPE, self.ORDER)
                self.assertEqual(row["status"], status)
                self.assertTrue(all("status" not in event[3] for event in self.writes()))

    def test_it_links_the_entry_when_the_order_has_none(self):
        self.add_order("Awaiting Bank")

        self.submit()

        row = self.db.row(DOCTYPE, self.ORDER)
        self.assertEqual(row["payment_entry"], "ACC-PAY-2026-00001")
        self.assertEqual(row["invoice_lock"], self.INVOICE, "an order that is not Completed keeps blocking")

    def test_it_never_replaces_an_entry_that_is_already_linked(self):
        self.add_order("Completed", payment_entry="ACC-PAY-2026-00000", invoice_lock=None)

        self.submit()

        self.assertEqual(self.db.row(DOCTYPE, self.ORDER)["payment_entry"], "ACC-PAY-2026-00000")
        self.assertEqual(self.writes(), [])

    def test_a_completed_order_stops_blocking_once_its_entry_is_submitted(self):
        self.add_order("Completed")

        self.submit()

        row = self.db.row(DOCTYPE, self.ORDER)
        self.assertEqual((row["status"], row["payment_entry"], row["invoice_lock"]),
                         ("Completed", "ACC-PAY-2026-00001", None))

    def test_it_stays_inside_the_transaction_of_the_payment_entry(self):
        self.add_order("Completed")

        self.submit()

        kinds = {event[0] for event in self.db.events}
        self.assertEqual(kinds & {"commit", "rollback"}, set())
        self.assertIn(("get_value", DOCTYPE, self.ORDER, True), self.db.events)

    def test_an_entry_without_an_order_or_with_an_unknown_one_touches_nothing(self):
        on_payment_entry_submit(SimpleNamespace(name="ACC-PAY-1", inter_payment_order=None, get={}.get))
        self.submit(order="IPO-404")

        self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
