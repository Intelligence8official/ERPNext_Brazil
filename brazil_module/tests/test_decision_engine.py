import sys
from unittest.mock import MagicMock, patch

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.decision_engine import DecisionEngine


def _make_settings(**overrides):
    s = MagicMock()
    s.default_confidence_threshold = overrides.get("threshold", 0.85)
    s.high_value_confirmation_pin = overrides.get("pin", False)
    s.high_value_threshold = overrides.get("pin_threshold", 10000)
    return s


class TestEvaluate(unittest.TestCase):
    def test_auto_approves_above_threshold(self):
        engine = DecisionEngine(_make_settings(threshold=0.85))
        result = engine.evaluate(action="create", doctype="Purchase Order", confidence=0.90)
        self.assertTrue(result["auto_approve"])

    def test_requires_human_below_threshold(self):
        engine = DecisionEngine(_make_settings(threshold=0.85))
        result = engine.evaluate(action="create", doctype="Purchase Order", confidence=0.70)
        self.assertFalse(result["auto_approve"])

    def test_submit_always_requires_human(self):
        engine = DecisionEngine(_make_settings(threshold=0.50))
        result = engine.evaluate(action="submit", doctype="Purchase Order", confidence=0.99)
        self.assertFalse(result["auto_approve"])

    def test_cancel_always_requires_human(self):
        engine = DecisionEngine(_make_settings(threshold=0.50))
        result = engine.evaluate(action="cancel", doctype="Purchase Invoice", confidence=0.99)
        self.assertFalse(result["auto_approve"])

    def test_high_value_pin_requires_human(self):
        engine = DecisionEngine(_make_settings(pin=True, pin_threshold=5000))
        result = engine.evaluate(action="create", doctype="Payment Entry", confidence=0.99, amount=6000)
        self.assertFalse(result["auto_approve"])

    def test_high_value_below_threshold_auto_approves(self):
        engine = DecisionEngine(_make_settings(pin=True, pin_threshold=5000))
        result = engine.evaluate(action="create", doctype="Payment Entry", confidence=0.99, amount=3000)
        self.assertTrue(result["auto_approve"])

    def test_custom_threshold_overrides_default(self):
        engine = DecisionEngine(_make_settings(threshold=0.85))
        result = engine.evaluate(action="create", doctype="Purchase Order", confidence=0.80, custom_threshold=0.75)
        self.assertTrue(result["auto_approve"])

    def test_returns_confidence_and_threshold(self):
        engine = DecisionEngine(_make_settings(threshold=0.85))
        result = engine.evaluate(action="create", doctype="Purchase Order", confidence=0.90)
        self.assertEqual(result["confidence"], 0.90)
        self.assertEqual(result["threshold"], 0.85)


class TestLogDecision(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        self.mock_doc = MagicMock()
        self.mock_doc.name = "DL-001"
        frappe.new_doc.return_value = self.mock_doc

    def test_creates_decision_log(self):
        engine = DecisionEngine(_make_settings())
        name = engine.log_decision(
            event_type="recurring", module="p2p", action="create_po",
            actor="Agent", channel="system", confidence=0.90,
            model="claude-haiku-4-5-20251001", input_summary="test",
            reasoning="Auto-approved", result="Success",
        )
        frappe.new_doc.assert_called_with("I8 Decision Log")
        self.assertEqual(name, "DL-001")

    def test_pending_result_not_submitted(self):
        engine = DecisionEngine(_make_settings())
        engine.log_decision(
            event_type="recurring", module="p2p", action="create_po",
            actor="Agent", channel="system", confidence=0.50,
            model="claude-sonnet-4-6", input_summary="test",
            reasoning="Low confidence", result="Pending",
        )
        self.mock_doc.insert.assert_called_once()
        self.mock_doc.submit.assert_not_called()

    def test_success_result_is_submitted(self):
        engine = DecisionEngine(_make_settings())
        engine.log_decision(
            event_type="recurring", module="p2p", action="create_po",
            actor="Agent", channel="system", confidence=0.95,
            model="claude-haiku-4-5-20251001", input_summary="test",
            reasoning="Auto-approved", result="Success",
        )
        self.mock_doc.insert.assert_called_once()
        self.mock_doc.submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
