import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.tools.erp_tools import execute_tool, TOOL_SCHEMAS


class TestReportToolSchemas(unittest.TestCase):
    def test_report_data_schema_exists(self):
        names = [s["name"] for s in TOOL_SCHEMAS]
        self.assertIn("erp-get_report_data", names)

    def test_account_balance_schema_exists(self):
        names = [s["name"] for s in TOOL_SCHEMAS]
        self.assertIn("erp-get_account_balance", names)

    def test_report_data_required_fields(self):
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "erp-get_report_data")
        self.assertIn("doctype", schema["input_schema"]["required"])

    def test_account_balance_has_date_properties(self):
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "erp-get_account_balance")
        props = schema["input_schema"]["properties"]
        self.assertIn("from_date", props)
        self.assertIn("to_date", props)
        self.assertIn("account", props)


class TestGetReportData(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.sql.side_effect = None
        frappe.get_all.side_effect = None

    def test_grouped_query(self):
        frappe.db.sql.return_value = [
            {"supplier": "Sup A", "value": 10000, "count": 5},
            {"supplier": "Sup B", "value": 5000, "count": 3},
        ]
        result = execute_tool("erp-get_report_data", {
            "doctype": "Purchase Invoice",
            "group_by": "supplier",
            "aggregate": "SUM",
            "aggregate_field": "grand_total",
        }, MagicMock())
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(result["grouped_by"], "supplier")
        self.assertEqual(result["aggregate"], "SUM")
        self.assertEqual(result["field"], "grand_total")

    def test_grouped_query_uses_sql(self):
        frappe.db.sql.return_value = []
        execute_tool("erp-get_report_data", {
            "doctype": "Purchase Invoice",
            "group_by": "supplier",
        }, MagicMock())
        frappe.db.sql.assert_called_once()

    def test_simple_list(self):
        frappe.get_all.return_value = [{"name": "PI-001", "grand_total": 5000}]
        result = execute_tool("erp-get_report_data", {
            "doctype": "Purchase Invoice",
        }, MagicMock())
        self.assertEqual(len(result["data"]), 1)
        frappe.get_all.assert_called_once()

    def test_simple_list_does_not_use_sql(self):
        frappe.get_all.return_value = []
        execute_tool("erp-get_report_data", {
            "doctype": "Purchase Invoice",
        }, MagicMock())
        frappe.db.sql.assert_not_called()

    def test_rejects_invalid_group_by(self):
        result = execute_tool("erp-get_report_data", {
            "doctype": "Purchase Invoice",
            "group_by": "supplier; DROP TABLE",
            "aggregate_field": "grand_total",
        }, MagicMock())
        self.assertIn("error", result)

    def test_rejects_invalid_aggregate_field(self):
        result = execute_tool("erp-get_report_data", {
            "doctype": "Purchase Invoice",
            "group_by": "supplier",
            "aggregate_field": "grand_total; DROP TABLE",
        }, MagicMock())
        self.assertIn("error", result)

    def test_invalid_aggregate_defaults_to_sum(self):
        frappe.db.sql.return_value = []
        execute_tool("erp-get_report_data", {
            "doctype": "Purchase Invoice",
            "group_by": "supplier",
            "aggregate": "INJECT",
            "aggregate_field": "grand_total",
        }, MagicMock())
        call_args = frappe.db.sql.call_args[0][0]
        self.assertIn("SUM(", call_args)

    def test_financial_doctype_sets_docstatus_filter(self):
        frappe.get_all.return_value = []
        execute_tool("erp-get_report_data", {
            "doctype": "Purchase Invoice",
        }, MagicMock())
        call_kwargs = frappe.get_all.call_args
        filters_arg = call_kwargs[1].get("filters") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
        # The filter must have been set via setdefault — check the call included docstatus=1
        self.assertIn("docstatus", filters_arg)
        self.assertEqual(filters_arg["docstatus"], 1)

    def test_non_financial_doctype_no_forced_docstatus(self):
        frappe.get_all.return_value = []
        execute_tool("erp-get_report_data", {
            "doctype": "Item",
        }, MagicMock())
        call_kwargs = frappe.get_all.call_args
        filters_arg = call_kwargs[1].get("filters") or {}
        self.assertNotIn("docstatus", filters_arg)


class TestGetAccountBalance(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.sql.side_effect = None

    def test_returns_balances(self):
        frappe.db.sql.return_value = [
            {"account": "Banco Inter", "total_debit": 100000, "total_credit": 30000, "balance": 70000},
        ]
        result = execute_tool("erp-get_account_balance", {
            "account": "Banco Inter",
        }, MagicMock())
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["balance"], 70000)

    def test_no_date_filter_omitted_from_query(self):
        frappe.db.sql.return_value = []
        execute_tool("erp-get_account_balance", {"account": "Caixa"}, MagicMock())
        sql_query = frappe.db.sql.call_args[0][0]
        self.assertNotIn("posting_date", sql_query)

    def test_from_date_and_to_date_included(self):
        frappe.db.sql.return_value = []
        execute_tool("erp-get_account_balance", {
            "account": "Caixa",
            "from_date": "2025-01-01",
            "to_date": "2025-12-31",
        }, MagicMock())
        sql_query = frappe.db.sql.call_args[0][0]
        self.assertIn("BETWEEN", sql_query)

    def test_only_from_date(self):
        frappe.db.sql.return_value = []
        execute_tool("erp-get_account_balance", {
            "account": "Caixa",
            "from_date": "2025-01-01",
        }, MagicMock())
        sql_query = frappe.db.sql.call_args[0][0]
        self.assertIn(">=", sql_query)
        self.assertNotIn("BETWEEN", sql_query)

    def test_only_to_date(self):
        frappe.db.sql.return_value = []
        execute_tool("erp-get_account_balance", {
            "account": "Caixa",
            "to_date": "2025-12-31",
        }, MagicMock())
        sql_query = frappe.db.sql.call_args[0][0]
        self.assertIn("<=", sql_query)
        self.assertNotIn("BETWEEN", sql_query)

    def test_empty_account_returns_data_key(self):
        frappe.db.sql.return_value = []
        result = execute_tool("erp-get_account_balance", {}, MagicMock())
        self.assertIn("data", result)


if __name__ == "__main__":
    unittest.main()
