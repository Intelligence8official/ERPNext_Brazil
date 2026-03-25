import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.action_executor import ActionExecutor


class TestAllowlist(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_doc.side_effect = None
        frappe.new_doc.side_effect = None

    def test_allowed_create(self):
        mock_doc = MagicMock()
        mock_doc.name = "PO-001"
        frappe.new_doc.return_value = mock_doc
        executor = ActionExecutor()
        result = executor.execute("Purchase Order", "create", {"supplier": "Test"})
        self.assertEqual(result["name"], "PO-001")
        frappe.new_doc.assert_called_with("Purchase Order")

    def test_delete_always_blocked(self):
        executor = ActionExecutor()
        with self.assertRaises(PermissionError) as ctx:
            executor.execute("Purchase Order", "delete", {})
        self.assertIn("never allowed", str(ctx.exception).lower())

    def test_disallowed_doctype(self):
        executor = ActionExecutor()
        with self.assertRaises(PermissionError) as ctx:
            executor.execute("Company", "create", {})
        self.assertIn("no access", str(ctx.exception).lower())

    def test_disallowed_operation_on_allowed_doctype(self):
        executor = ActionExecutor()
        with self.assertRaises(PermissionError) as ctx:
            executor.execute("Nota Fiscal", "create", {})
        self.assertIn("not allowed", str(ctx.exception).lower())

    def test_read_returns_doc_dict(self):
        mock_doc = MagicMock()
        mock_doc.as_dict.return_value = {"name": "NF-001", "status": "Processed"}
        frappe.get_doc.return_value = mock_doc
        executor = ActionExecutor()
        result = executor.execute("Nota Fiscal", "read", {"name": "NF-001"})
        self.assertEqual(result["name"], "NF-001")

    def test_submit_calls_doc_submit(self):
        mock_doc = MagicMock()
        mock_doc.name = "PO-001"
        frappe.get_doc.return_value = mock_doc
        executor = ActionExecutor()
        result = executor.execute("Purchase Order", "submit", {"name": "PO-001"})
        mock_doc.submit.assert_called_once()

    def test_cancel_calls_doc_cancel(self):
        mock_doc = MagicMock()
        mock_doc.name = "PO-001"
        frappe.get_doc.return_value = mock_doc
        executor = ActionExecutor()
        result = executor.execute("Purchase Order", "cancel", {"name": "PO-001"})
        mock_doc.cancel.assert_called_once()

    def test_update_does_not_mutate_input(self):
        mock_doc = MagicMock()
        mock_doc.name = "SUP-001"
        frappe.get_doc.return_value = mock_doc
        executor = ActionExecutor()
        data = {"name": "SUP-001", "supplier_name": "New Name"}
        executor.execute("Supplier", "update", data)
        self.assertIn("name", data)  # name should still be in original dict

    def test_update_status_blocks_non_allowlisted_field(self):
        executor = ActionExecutor()
        with self.assertRaises(PermissionError) as ctx:
            executor.execute("Nota Fiscal", "update_status", {"name": "NF-001", "field": "cnpj_emitente", "value": "123"})
        self.assertIn("cannot update field", str(ctx.exception).lower())

    def test_update_status_allows_allowlisted_field(self):
        executor = ActionExecutor()
        executor.execute("Nota Fiscal", "update_status", {"name": "NF-001", "field": "processing_status", "value": "Completed"})
        frappe.db.set_value.assert_called_with("Nota Fiscal", "NF-001", "processing_status", "Completed")


if __name__ == "__main__":
    unittest.main()
