import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.tools import get_all_tool_schemas, execute_tool


class TestToolRegistry(unittest.TestCase):
    def test_get_all_tool_schemas_returns_list(self):
        schemas = get_all_tool_schemas()
        self.assertIsInstance(schemas, list)
        self.assertGreater(len(schemas), 10)

    def test_all_schemas_have_required_keys(self):
        for schema in get_all_tool_schemas():
            self.assertIn("name", schema)
            self.assertIn("description", schema)
            self.assertIn("input_schema", schema)

    def test_tool_names_are_namespaced(self):
        for schema in get_all_tool_schemas():
            self.assertIn(".", schema["name"], f"Tool {schema['name']} missing namespace prefix")


class TestErpTools(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_read_document(self):
        mock_executor = MagicMock()
        mock_executor.execute.return_value = {"name": "PO-001"}
        execute_tool("erp.read_document", {"doctype": "Purchase Order", "name": "PO-001"}, mock_executor)
        mock_executor.execute.assert_called_with("Purchase Order", "read", {"name": "PO-001"})

    def test_list_documents(self):
        frappe.get_all.return_value = [{"name": "PO-001"}, {"name": "PO-002"}]
        mock_executor = MagicMock()
        result = execute_tool("erp.list_documents", {"doctype": "Purchase Order"}, mock_executor)
        self.assertEqual(len(result["data"]), 2)

    def test_unknown_prefix_raises(self):
        mock_executor = MagicMock()
        with self.assertRaises(ValueError):
            execute_tool("unknown.tool", {}, mock_executor)

    def test_unknown_tool_in_known_prefix_raises(self):
        mock_executor = MagicMock()
        with self.assertRaises(ValueError):
            execute_tool("erp.nonexistent", {}, mock_executor)


class TestPurchasingTools(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.return_value = []

    def test_create_purchase_order(self):
        mock_executor = MagicMock()
        mock_executor.execute.return_value = {"name": "PO-NEW", "doctype": "Purchase Order"}
        execute_tool("p2p.create_purchase_order", {
            "supplier": "Test Sup",
            "required_by": "2026-04-05",
            "items": [{"item_code": "ITEM-001", "qty": 1, "rate": 100}],
        }, mock_executor)
        mock_executor.execute.assert_called_once()
        call_args = mock_executor.execute.call_args
        self.assertEqual(call_args[0][0], "Purchase Order")
        self.assertEqual(call_args[0][1], "create")


if __name__ == "__main__":
    unittest.main()
