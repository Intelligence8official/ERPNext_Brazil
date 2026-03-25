import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import unittest

from brazil_module.services.intelligence.prompts.approval_formatter import format_approval_message


class TestFormatApprovalMessage(unittest.TestCase):
    def _make_decision(self, **kw):
        return {
            "action": kw.get("action", "create_po"),
            "related_doctype": kw.get("doctype", "Purchase Order"),
            "related_docname": kw.get("docname", "PO-001"),
            "amount": kw.get("amount", "R$ 5.000,00"),
            "confidence": kw.get("confidence", 0.78),
            "reasoning": kw.get("reasoning", "Value differs from estimate"),
            "decision_log_name": kw.get("log_name", "DL-001"),
        }

    def test_returns_text_and_reply_markup(self):
        result = format_approval_message(self._make_decision())
        self.assertIn("text", result)
        self.assertIn("reply_markup", result)

    def test_text_includes_action(self):
        result = format_approval_message(self._make_decision(action="create_invoice"))
        self.assertIn("create_invoice", result["text"])

    def test_text_includes_confidence(self):
        result = format_approval_message(self._make_decision(confidence=0.78))
        self.assertIn("78%", result["text"])

    def test_reply_markup_has_three_buttons(self):
        result = format_approval_message(self._make_decision(log_name="DL-001"))
        keyboard = result["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(keyboard[0]), 3)

    def test_approve_callback_data(self):
        result = format_approval_message(self._make_decision(log_name="DL-005"))
        buttons = result["reply_markup"]["inline_keyboard"][0]
        approve_btn = buttons[0]
        self.assertEqual(approve_btn["callback_data"], "approve:DL-005")

    def test_reject_callback_data(self):
        result = format_approval_message(self._make_decision(log_name="DL-005"))
        buttons = result["reply_markup"]["inline_keyboard"][0]
        reject_btn = buttons[1]
        self.assertEqual(reject_btn["callback_data"], "reject:DL-005")


if __name__ == "__main__":
    unittest.main()
