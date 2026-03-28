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

from brazil_module.services.intelligence.analytics.supplier_intelligence import (
    calculate_supplier_score,
    _build_score_summary,
)


class TestCalculateScore(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_value.return_value = 30
        frappe.db.sql.return_value = []

    def test_returns_overall_score(self):
        score = calculate_supplier_score("Test Supplier")
        self.assertIn("overall", score)
        self.assertIn("nf_delivery", score)
        self.assertIn("value_accuracy", score)

    def test_overall_zero_when_no_data(self):
        score = calculate_supplier_score("Test Supplier")
        self.assertEqual(score["overall"], 0)


class TestBuildSummary(unittest.TestCase):
    def test_builds_summary_string(self):
        scores = {"overall": 85, "nf_delivery": 0.9, "value_accuracy": 0.8}
        summary = _build_score_summary("Test", scores)
        self.assertIn("85%", summary)
        self.assertIn("90%", summary)


if __name__ == "__main__":
    unittest.main()
