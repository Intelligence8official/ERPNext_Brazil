import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import unittest

from brazil_module.services.intelligence.prompts.system_prompt import build_system_prompt


class TestBuildSystemPrompt(unittest.TestCase):
    def _make_settings(self, **kw):
        s = MagicMock()
        s.default_confidence_threshold = kw.get("threshold", 0.85)
        s.high_value_threshold = kw.get("high_value", 10000)
        return s

    def test_returns_string(self):
        result = build_system_prompt(self._make_settings(), ["p2p"])
        self.assertIsInstance(result, str)

    def test_includes_confidence_threshold(self):
        result = build_system_prompt(self._make_settings(threshold=0.90), ["p2p"])
        self.assertIn("0.9", result)

    def test_includes_active_modules(self):
        result = build_system_prompt(self._make_settings(), ["p2p", "fiscal", "banking"])
        self.assertIn("p2p", result)
        self.assertIn("fiscal", result)
        self.assertIn("banking", result)

    def test_includes_confidence_format_instruction(self):
        result = build_system_prompt(self._make_settings(), [])
        self.assertIn("Confidence:", result)

    def test_includes_portuguese_instruction(self):
        result = build_system_prompt(self._make_settings(), [])
        self.assertIn("portugu", result.lower())


if __name__ == "__main__":
    unittest.main()
