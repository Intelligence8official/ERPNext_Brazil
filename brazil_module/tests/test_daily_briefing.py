import sys
import types as _types
from datetime import date
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

# Temporarily inject telegram_bot mock only for the daily_briefing import,
# then remove it so test_telegram_bot.py can load the real module undisturbed.
_tb_key = "brazil_module.services.intelligence.channels.telegram_bot"
_tb_was_present = _tb_key in sys.modules
_tb_original = sys.modules.get(_tb_key)
if not _tb_was_present or not isinstance(_tb_original, _types.ModuleType):
    sys.modules[_tb_key] = MagicMock()

import unittest

from brazil_module.services.intelligence.recurring.daily_briefing import (
    build_briefing,
    scheduled_briefing,
    _bank_balance_section,
    _receivables_section,
    _payables_section,
    _pending_actions_section,
    _agent_cost_section,
)

# Restore the original state so parallel test files get the real module
if not _tb_was_present:
    sys.modules.pop(_tb_key, None)
elif _tb_original is not None:
    sys.modules[_tb_key] = _tb_original


class TestScheduledBriefing(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None
        frappe.db.count.return_value = 0
        frappe.get_all.return_value = []
        frappe.db.sql.return_value = [{"total": 0, "calls": 0}]

    def test_skips_when_agent_disabled(self):
        frappe.db.get_single_value.side_effect = lambda dt, field: False
        scheduled_briefing()
        # Should not call get_all (no briefing built)
        frappe.get_all.assert_not_called()

    def test_skips_when_briefing_disabled(self):
        def side_effect(dt, field):
            if field == "enabled":
                return True
            if field == "briefing_enabled":
                return False
            return None
        frappe.db.get_single_value.side_effect = side_effect
        scheduled_briefing()
        frappe.get_all.assert_not_called()


class TestBuildBriefing(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.return_value = []
        frappe.db.count.return_value = 0
        frappe.db.sql.return_value = [{"total": 0, "calls": 0}]

    def test_returns_string(self):
        result = build_briefing()
        self.assertIsInstance(result, str)

    def test_includes_date(self):
        result = build_briefing()
        today_str = date.today().strftime("%d/%m/%Y")
        self.assertIn(today_str, result)


class TestBankBalanceSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_shows_balance(self):
        frappe.get_all.return_value = [{"name": "ACC-1", "account_name": "Inter", "bank_balance": 42000}]
        result = _bank_balance_section()
        self.assertIn("42,000.00", result)
        self.assertIn("Inter", result)

    def test_empty_when_no_accounts(self):
        frappe.get_all.return_value = []
        result = _bank_balance_section()
        self.assertEqual(result, "")


class TestReceivablesSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_shows_overdue(self):
        frappe.get_all.side_effect = [
            [{"total": 0}],  # due today
            [{"total": 15000, "count": 2}],  # overdue
        ]
        result = _receivables_section(date.today())
        self.assertIn("15,000.00", result)
        self.assertIn("2 faturas", result)

    def test_no_pendencias(self):
        frappe.get_all.side_effect = [
            [{"total": 0}],
            [{"total": 0, "count": 0}],
        ]
        result = _receivables_section(date.today())
        self.assertIn("Nenhuma pendencia", result)


class TestPayablesSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_shows_upcoming(self):
        frappe.get_all.side_effect = [
            [{"total": 8500, "count": 3}],  # upcoming 7 days
            [{"total": 0, "count": 0}],  # overdue
        ]
        result = _payables_section(date.today())
        self.assertIn("8,500.00", result)
        self.assertIn("3 faturas", result)


class TestPendingActionsSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_shows_pending_approvals(self):
        frappe.db.count.side_effect = [5, 3]  # approvals, NFs
        result = _pending_actions_section()
        self.assertIn("5", result)
        self.assertIn("3", result)

    def test_no_pendencias(self):
        frappe.db.count.side_effect = [0, 0]
        result = _pending_actions_section()
        self.assertIn("Nenhuma pendencia", result)


class TestAgentCostSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_shows_cost(self):
        frappe.db.sql.return_value = [{"total": 2.45, "calls": 127}]
        result = _agent_cost_section(date.today())
        self.assertIn("2.4500", result)
        self.assertIn("127", result)

    def test_no_calls(self):
        frappe.db.sql.return_value = [{"total": 0, "calls": 0}]
        result = _agent_cost_section(date.today())
        self.assertIn("Nenhuma chamada", result)


if __name__ == "__main__":
    unittest.main()
