import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.context_builder import ContextBuilder


class TestBuildContext(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.side_effect = None
        frappe.get_all.return_value = []
        frappe.get_doc.side_effect = None
        frappe.db.get_single_value.return_value = "Test Company"
        frappe.utils.today.return_value = "2026-03-25"

    def test_returns_required_keys(self):
        cb = ContextBuilder()
        result = cb.build("recurring_schedule", {"module": "p2p"})
        self.assertIn("system_context", result)
        self.assertIn("module_context", result)
        self.assertIn("event_data", result)
        self.assertIn("history", result)

    def test_system_context_includes_date(self):
        cb = ContextBuilder()
        result = cb.build("test_event", {})
        self.assertIn("2026-03-25", result["system_context"])

    def test_module_context_loaded_from_registry(self):
        frappe.get_all.return_value = [{"name": "MOD-001"}]
        mock_doc = MagicMock()
        mock_doc.context_prompt = "You handle purchase orders."
        mock_doc.enabled = True
        frappe.get_doc.return_value = mock_doc
        cb = ContextBuilder()
        result = cb.build("recurring_schedule", {"module": "p2p"})
        self.assertIn("You handle purchase orders", result["module_context"])

    def test_supplier_profile_included_when_supplier_present(self):
        frappe.db.exists.return_value = True
        frappe.db.get_value.return_value = {
            "name": "Test Sup", "supplier_name": "Test Sup", "tax_id": "12345",
            "pix_key": None, "pix_key_type": None,
            "i8_expected_nf_days": 5, "i8_nf_due_day": None,
            "i8_follow_up_after_days": 7, "i8_max_follow_ups": 3,
            "i8_auto_pay": 0, "i8_agent_notes": None,
            "default_payment_terms_template": None,
        }
        frappe.get_all.return_value = []
        cb = ContextBuilder()
        result = cb.build("test", {"supplier": "Test Sup"})
        self.assertIsNotNone(result.get("supplier_profile"))

    def test_no_supplier_profile_when_not_found(self):
        frappe.db.exists.return_value = False
        frappe.db.get_value.return_value = None
        frappe.get_all.return_value = []
        cb = ContextBuilder()
        result = cb.build("test", {"supplier": "Unknown"})
        self.assertIsNone(result.get("supplier_profile"))

    def test_conversation_history_sliding_window(self):
        # Simulate 25 messages
        messages = [MagicMock(content=f"msg-{i}", timestamp=f"2026-03-{i+1:02d}") for i in range(25)]
        mock_conv = MagicMock()
        mock_conv.messages = messages
        frappe.get_doc.return_value = mock_conv
        frappe.get_all.return_value = [{"name": "CONV-001"}]

        cb = ContextBuilder()
        result = cb.build("test", {"conversation_name": "CONV-001"})
        # Should return last 20 messages
        self.assertEqual(len(result["history"]), 20)

    def test_empty_history_when_no_conversation(self):
        cb = ContextBuilder()
        result = cb.build("test", {})
        self.assertEqual(result["history"], [])


if __name__ == "__main__":
    unittest.main()
