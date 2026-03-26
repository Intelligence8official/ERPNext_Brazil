import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.tools.email_tools import execute_tool, TOOL_SCHEMAS


class TestEmailToolSchemas(unittest.TestCase):
    def test_classify_schema_has_enum(self):
        classify = next(s for s in TOOL_SCHEMAS if s["name"] == "email-classify")
        props = classify["input_schema"]["properties"]
        self.assertIn("classification", props)
        self.assertIn("FISCAL", props["classification"]["enum"])
        self.assertIn("SPAM", props["classification"]["enum"])

    def test_classify_requires_classification(self):
        classify = next(s for s in TOOL_SCHEMAS if s["name"] == "email-classify")
        self.assertIn("classification", classify["input_schema"]["required"])

    def test_classify_schema_has_all_categories(self):
        classify = next(s for s in TOOL_SCHEMAS if s["name"] == "email-classify")
        expected = {"FISCAL", "COMMERCIAL", "FINANCIAL", "OPERATIONAL", "SPAM", "UNCERTAIN"}
        actual = set(classify["input_schema"]["properties"]["classification"]["enum"])
        self.assertEqual(expected, actual)

    def test_classify_schema_has_communication_property(self):
        classify = next(s for s in TOOL_SCHEMAS if s["name"] == "email-classify")
        self.assertIn("communication", classify["input_schema"]["properties"])

    def test_classify_schema_has_reasoning_property(self):
        classify = next(s for s in TOOL_SCHEMAS if s["name"] == "email-classify")
        self.assertIn("reasoning", classify["input_schema"]["properties"])


class TestEmailClassify(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.exists.return_value = True
        frappe.db.set_value.side_effect = None

    def test_saves_classification(self):
        result = execute_tool("email-classify", {
            "communication": "COM-001",
            "classification": "FISCAL",
            "reasoning": "Contains NF attachment",
        }, MagicMock())
        frappe.db.set_value.assert_called_once()
        call_args = frappe.db.set_value.call_args
        self.assertEqual(call_args[0][0], "Communication")
        self.assertEqual(call_args[0][1], "COM-001")
        self.assertEqual(call_args[0][2]["i8_classification"], "FISCAL")
        self.assertEqual(call_args[0][2]["i8_processed"], 1)

    def test_returns_classification(self):
        result = execute_tool("email-classify", {
            "classification": "SPAM",
        }, MagicMock())
        self.assertEqual(result["classification"], "SPAM")

    def test_returns_status_classified(self):
        result = execute_tool("email-classify", {
            "communication": "COM-001",
            "classification": "FISCAL",
        }, MagicMock())
        self.assertEqual(result["status"], "classified")

    def test_returns_communication_name(self):
        result = execute_tool("email-classify", {
            "communication": "COM-002",
            "classification": "COMMERCIAL",
        }, MagicMock())
        self.assertEqual(result["communication"], "COM-002")

    def test_handles_missing_communication(self):
        frappe.db.exists.return_value = False
        result = execute_tool("email-classify", {
            "classification": "COMMERCIAL",
        }, MagicMock())
        self.assertEqual(result["classification"], "COMMERCIAL")
        frappe.db.set_value.assert_not_called()

    def test_handles_nonexistent_communication(self):
        frappe.db.exists.return_value = False
        result = execute_tool("email-classify", {
            "communication": "COM-MISSING",
            "classification": "SPAM",
        }, MagicMock())
        frappe.db.set_value.assert_not_called()
        self.assertEqual(result["classification"], "SPAM")

    def test_defaults_to_uncertain_when_no_classification(self):
        result = execute_tool("email-classify", {}, MagicMock())
        self.assertEqual(result["classification"], "UNCERTAIN")

    def test_marks_i8_processed_as_1(self):
        execute_tool("email-classify", {
            "communication": "COM-001",
            "classification": "FINANCIAL",
        }, MagicMock())
        saved = frappe.db.set_value.call_args[0][2]
        self.assertEqual(saved["i8_processed"], 1)


class TestEmailSearch(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_search_by_sender(self):
        frappe.get_all.return_value = [{"name": "COM-001", "subject": "Test"}]
        result = execute_tool("email-search", {"sender": "test@example.com"}, MagicMock())
        self.assertEqual(len(result["data"]), 1)

    def test_search_by_subject(self):
        frappe.get_all.return_value = []
        result = execute_tool("email-search", {"subject_contains": "invoice"}, MagicMock())
        self.assertIsInstance(result["data"], list)

    def test_search_returns_data_key(self):
        frappe.get_all.return_value = []
        result = execute_tool("email-search", {}, MagicMock())
        self.assertIn("data", result)

    def test_search_passes_filters_to_frappe(self):
        frappe.get_all.return_value = []
        execute_tool("email-search", {"sender": "vendor@test.com"}, MagicMock())
        call_kwargs = frappe.get_all.call_args
        filters = call_kwargs[1]["filters"] if call_kwargs[1] else call_kwargs[0][1]
        self.assertIn("sender", filters)


class TestEmailGetContent(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_returns_email_content(self):
        mock_doc = MagicMock()
        mock_doc.name = "COM-001"
        mock_doc.subject = "Test Subject"
        mock_doc.sender = "test@example.com"
        mock_doc.content = "Email body here"
        mock_doc.communication_date = "2026-03-26"
        frappe.get_doc.return_value = mock_doc
        result = execute_tool("email-get_content", {"communication": "COM-001"}, MagicMock())
        self.assertEqual(result["subject"], "Test Subject")
        self.assertEqual(result["content"], "Email body here")

    def test_returns_all_expected_fields(self):
        mock_doc = MagicMock()
        mock_doc.name = "COM-001"
        mock_doc.subject = "NF-e recebida"
        mock_doc.sender = "fiscal@supplier.com"
        mock_doc.content = "Segue NF em anexo."
        mock_doc.communication_date = "2026-03-26"
        frappe.get_doc.return_value = mock_doc
        result = execute_tool("email-get_content", {"communication": "COM-001"}, MagicMock())
        for field in ("name", "subject", "sender", "content", "communication_date"):
            self.assertIn(field, result)


class TestUnknownTool(unittest.TestCase):
    def test_raises_on_unknown_tool(self):
        with self.assertRaises(ValueError):
            execute_tool("email-nonexistent", {}, MagicMock())


if __name__ == "__main__":
    unittest.main()
