import sys
from datetime import date
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.learning_engine import (
    check_learned_pattern,
    record_approval,
    record_rejection,
    _build_pattern_key,
)


class TestBuildPatternKey(unittest.TestCase):
    def test_supplier_key(self):
        key = _build_pattern_key("p2p-create_purchase_order", {"supplier": "Test Corp"})
        self.assertEqual(key, "supplier:Test Corp")

    def test_nf_key(self):
        key = _build_pattern_key("fiscal-create_purchase_invoice", {"nota_fiscal": "NF-001"})
        self.assertEqual(key, "nf_processing")

    def test_email_key(self):
        key = _build_pattern_key("email-classify", {"communication": "COM-001"})
        self.assertEqual(key, "email_classification")

    def test_generic_key(self):
        key = _build_pattern_key("some-action", {})
        self.assertEqual(key, "generic:some-action")

    def test_doctype_key(self):
        key = _build_pattern_key("erp-create_document", {"doctype": "Purchase Order"})
        self.assertEqual(key, "doctype:Purchase Order")

    def test_supplier_key_truncation(self):
        long_name = "A" * 200
        key = _build_pattern_key("action", {"supplier": long_name})
        self.assertEqual(key, f"supplier:{'A' * 100}")


class TestCheckLearnedPattern(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_value.side_effect = None
        frappe.db.get_value.return_value = None

    def test_returns_false_when_disabled(self):
        settings = MagicMock()
        settings.learning_enabled = False
        frappe.get_single.return_value = settings
        result = check_learned_pattern("action", {})
        self.assertFalse(result)

    def test_returns_true_when_threshold_met(self):
        settings = MagicMock()
        settings.learning_enabled = True
        settings.learning_approval_count = 3
        frappe.get_single.return_value = settings
        frappe.db.get_value.side_effect = [
            {"consecutive_approvals": 3, "name": "LP-001"},  # pattern lookup
            5,  # auto_approved_count
        ]
        result = check_learned_pattern("p2p-create_purchase_order", {"supplier": "Test"})
        self.assertTrue(result)

    def test_returns_false_when_below_threshold(self):
        settings = MagicMock()
        settings.learning_enabled = True
        settings.learning_approval_count = 3
        frappe.get_single.return_value = settings
        frappe.db.get_value.return_value = {"consecutive_approvals": 2, "name": "LP-001"}
        result = check_learned_pattern("p2p-create_purchase_order", {"supplier": "Test"})
        self.assertFalse(result)

    def test_returns_false_when_no_pattern(self):
        settings = MagicMock()
        settings.learning_enabled = True
        settings.learning_approval_count = 3
        frappe.get_single.return_value = settings
        frappe.db.get_value.return_value = None
        result = check_learned_pattern("p2p-create_purchase_order", {"supplier": "Test"})
        self.assertFalse(result)


class TestRecordApproval(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_value.side_effect = None

    def test_increments_existing(self):
        frappe.db.get_value.side_effect = ["LP-001", 2]  # exists, current count
        record_approval("action", {"supplier": "Test"})
        frappe.db.set_value.assert_called_once()

    def test_creates_new(self):
        frappe.db.get_value.return_value = None
        mock_doc = MagicMock()
        frappe.new_doc.return_value = mock_doc
        record_approval("action", {"supplier": "Test"})
        frappe.new_doc.assert_called_with("I8 Learning Pattern")
        mock_doc.insert.assert_called_once_with(ignore_permissions=True)


class TestRecordRejection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_value.side_effect = None

    def test_resets_counter(self):
        frappe.db.get_value.return_value = "LP-001"
        record_rejection("action", {"supplier": "Test"})
        frappe.db.set_value.assert_called_once()
        call_args = frappe.db.set_value.call_args
        self.assertEqual(call_args[0][2]["consecutive_approvals"], 0)

    def test_no_op_when_no_pattern(self):
        frappe.db.get_value.return_value = None
        record_rejection("action", {"supplier": "Test"})
        frappe.db.set_value.assert_not_called()


if __name__ == "__main__":
    unittest.main()
