import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.cost_tracker import CostTracker, calculate_cost_usd


class TestCalculateCost(unittest.TestCase):
    def test_haiku_cost(self):
        cost = calculate_cost_usd("claude-haiku-4-5-20251001", tokens_in=1000, tokens_out=500)
        expected = (1000 * 0.80 / 1_000_000) + (500 * 4.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_sonnet_cost(self):
        cost = calculate_cost_usd("claude-sonnet-4-6", tokens_in=1000, tokens_out=500)
        expected = (1000 * 3.0 / 1_000_000) + (500 * 15.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_opus_cost(self):
        cost = calculate_cost_usd("claude-opus-4-6", tokens_in=1000, tokens_out=500)
        expected = (1000 * 15.0 / 1_000_000) + (500 * 75.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_unknown_model_uses_sonnet_rates(self):
        cost = calculate_cost_usd("unknown-model", tokens_in=1000, tokens_out=500)
        expected = (1000 * 3.0 / 1_000_000) + (500 * 15.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_cached_input_discount(self):
        cost = calculate_cost_usd("claude-sonnet-4-6", tokens_in=1000, tokens_out=500, cache_hit=True)
        expected = (1000 * 0.30 / 1_000_000) + (500 * 15.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_zero_tokens(self):
        cost = calculate_cost_usd("claude-haiku-4-5-20251001", tokens_in=0, tokens_out=0)
        self.assertEqual(cost, 0.0)


class TestCostTrackerLog(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        self.mock_doc = MagicMock()
        frappe.new_doc.return_value = self.mock_doc

    def test_log_creates_cost_log_entry(self):
        tracker = CostTracker()
        tracker.log(
            model="claude-haiku-4-5-20251001",
            tokens_in=500, tokens_out=100, latency_ms=230,
            module="p2p", function_name="create_po", cache_hit=False,
        )
        frappe.new_doc.assert_called_once_with("I8 Cost Log")
        self.mock_doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_log_sets_correct_cost(self):
        tracker = CostTracker()
        tracker.log(
            model="claude-haiku-4-5-20251001",
            tokens_in=1000, tokens_out=500, latency_ms=100,
            module="p2p", function_name="test",
        )
        expected = (1000 * 0.80 / 1_000_000) + (500 * 4.0 / 1_000_000)
        self.assertAlmostEqual(self.mock_doc.cost_usd, expected, places=6)

    def test_log_returns_doc_name(self):
        self.mock_doc.name = "COST-001"
        tracker = CostTracker()
        result = tracker.log(
            model="claude-sonnet-4-6", tokens_in=100, tokens_out=50,
            latency_ms=200, module="email", function_name="classify",
        )
        self.assertEqual(result, "COST-001")


class TestBudgetCheck(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.sql.side_effect = None

    def test_within_budget(self):
        frappe.db.sql.return_value = [[5.0]]
        tracker = CostTracker()
        self.assertTrue(tracker.check_daily_budget(limit_usd=10.0))

    def test_exceeds_budget(self):
        frappe.db.sql.return_value = [[12.0]]
        tracker = CostTracker()
        self.assertFalse(tracker.check_daily_budget(limit_usd=10.0))

    def test_no_data_is_within_budget(self):
        frappe.db.sql.return_value = [[0.0]]
        tracker = CostTracker()
        self.assertTrue(tracker.check_daily_budget(limit_usd=10.0))

    def test_get_daily_total(self):
        frappe.db.sql.return_value = [[7.5]]
        tracker = CostTracker()
        self.assertEqual(tracker.get_daily_total(), 7.5)


if __name__ == "__main__":
    unittest.main()
