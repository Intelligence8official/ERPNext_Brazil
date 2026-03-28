import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

sys.modules.setdefault("anthropic", MagicMock())

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.orchestrator import route_event, generate_trace_id


class TestRouteEvent(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.return_value = []

    def test_approved_action_uses_decision_log_module(self):
        frappe.db.get_value.return_value = "fiscal"
        result = route_event("approved_action", {"decision_log": "DL-001"})
        self.assertEqual(result, ["fiscal"])

    def test_configured_routing(self):
        settings = MagicMock()
        routing_row = MagicMock()
        routing_row.event_type = "recurring_schedule"
        routing_row.module_name = "p2p"
        settings.event_routing = [routing_row]
        frappe.get_single.return_value = settings
        result = route_event("recurring_schedule", {"module": "p2p"})
        self.assertEqual(result, ["p2p"])

    def test_fallback_to_conversational(self):
        settings = MagicMock()
        settings.event_routing = []
        frappe.get_single.return_value = settings
        frappe.get_all.return_value = []
        result = route_event("unknown_event", {})
        self.assertEqual(result, ["conversational"])

    def test_approved_action_without_decision_log(self):
        """approved_action with no decision_log falls through to routing table."""
        settings = MagicMock()
        settings.event_routing = []
        frappe.get_single.return_value = settings
        frappe.get_all.return_value = []
        result = route_event("approved_action", {})
        self.assertEqual(result, ["conversational"])

    def test_approved_action_with_missing_module(self):
        """approved_action where Decision Log has no module falls through."""
        frappe.db.get_value.return_value = None
        settings = MagicMock()
        settings.event_routing = []
        frappe.get_single.return_value = settings
        frappe.get_all.return_value = []
        result = route_event("approved_action", {"decision_log": "DL-999"})
        self.assertEqual(result, ["conversational"])


class TestGenerateTraceId(unittest.TestCase):
    def test_returns_string(self):
        tid = generate_trace_id()
        self.assertIsInstance(tid, str)
        self.assertEqual(len(tid), 12)

    def test_unique(self):
        ids = {generate_trace_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


if __name__ == "__main__":
    unittest.main()
