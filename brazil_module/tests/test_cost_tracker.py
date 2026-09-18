import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.cost_tracker import CostTracker


class TestCostTrackerLog(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        self.mock_doc = MagicMock()
        frappe.new_doc.return_value = self.mock_doc

    def test_log_creates_cost_log_entry(self):
        tracker = CostTracker()
        tracker.log(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            tokens_in=500, tokens_out=100, latency_ms=230,
            module="p2p", function_name="create_po",
        )
        frappe.new_doc.assert_called_once_with("I8 Cost Log")
        self.mock_doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_the_price_follows_the_provider_that_answered(self):
        # The same tokens cost different money depending on who ran them, and
        # the old table priced every model as if it were Claude.
        tracker = CostTracker()
        tracker.log(
            provider="google", model="gemini-3.8-flash",
            tokens_in=1_000_000, tokens_out=0, latency_ms=100,
            module="briefing", function_name="format",
        )

        self.assertAlmostEqual(self.mock_doc.cost_usd, 0.75, places=6)

    def test_writes_down_who_answered(self):
        tracker = CostTracker()
        tracker.log(
            provider="google", model="gemini-3.8-flash",
            tokens_in=10, tokens_out=5, latency_ms=100,
            module="briefing", function_name="format",
        )

        self.assertEqual(self.mock_doc.provider, "google")
        self.assertEqual(self.mock_doc.model, "gemini-3.8-flash")

    def test_cached_tokens_are_recorded_and_priced_apart(self):
        tracker = CostTracker()
        tracker.log(
            provider="anthropic", model="claude-sonnet-5",
            tokens_in=0, tokens_out=0, cached_tokens=1_000_000,
            latency_ms=100, module="p2p", function_name="test",
        )

        self.assertEqual(self.mock_doc.cached_tokens, 1_000_000)
        self.assertTrue(self.mock_doc.cache_hit)
        self.assertAlmostEqual(self.mock_doc.cost_usd, 0.20, places=6)

    def test_a_call_with_no_cache_is_not_marked_as_cached(self):
        tracker = CostTracker()
        tracker.log(
            provider="anthropic", model="claude-sonnet-5",
            tokens_in=10, tokens_out=5, latency_ms=100,
            module="p2p", function_name="test",
        )

        self.assertFalse(self.mock_doc.cache_hit)

    def test_keeps_the_trace_that_ties_a_call_to_its_flow(self):
        # The field existed and was never written: a trace id was generated
        # per event and thrown away.
        tracker = CostTracker()
        tracker.log(
            provider="anthropic", model="claude-sonnet-5",
            tokens_in=10, tokens_out=5, latency_ms=100,
            module="p2p", function_name="test", trace_id="trace-123",
        )

        self.assertEqual(self.mock_doc.trace_id, "trace-123")

    def test_defaults_to_anthropic_when_nobody_says_otherwise(self):
        tracker = CostTracker()
        tracker.log(
            model="claude-sonnet-5", tokens_in=10, tokens_out=5,
            latency_ms=100, module="p2p", function_name="test",
        )

        self.assertEqual(self.mock_doc.provider, "anthropic")

    def test_log_returns_doc_name(self):
        self.mock_doc.name = "COST-001"
        tracker = CostTracker()
        result = tracker.log(
            provider="anthropic", model="claude-sonnet-5", tokens_in=100, tokens_out=50,
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
