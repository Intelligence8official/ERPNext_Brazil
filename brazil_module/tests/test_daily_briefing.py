import sys
import types as _types
from datetime import date
from unittest.mock import MagicMock, patch

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

# Temporarily inject telegram_bot mock only for the daily_briefing import
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
    _payables_section,
    _pending_actions_section,
    _agent_cost_section,
    _cash_flow_section,
)

# Restore
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
        frappe.db.sql.return_value = []

    def test_skips_when_agent_disabled(self):
        frappe.db.get_single_value.side_effect = lambda dt, field: False
        scheduled_briefing()
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
        frappe.db.sql.return_value = []

    def test_returns_string(self):
        result = build_briefing()
        self.assertIsInstance(result, str)

    def test_includes_date(self):
        result = build_briefing()
        today_str = date.today().strftime("%d/%m/%Y")
        self.assertIn(today_str, result)

    def test_includes_weekday_name(self):
        result = build_briefing()
        # Should contain a weekday name in Portuguese
        self.assertTrue(
            any(day in result for day in ["Segunda", "Terca", "Quarta", "Quinta", "Sexta", "Sabado", "Domingo"])
        )


class TestBankBalanceSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.sql.return_value = []

    def test_shows_inter_balance(self):
        frappe.get_all.return_value = [{"name": "ICA-1", "company": "I8", "current_balance": 42000, "balance_date": "2026-03-26"}]
        result = _bank_balance_section()
        self.assertIn("42,000.00", result)
        self.assertIn("Inter", result)

    def test_shows_gl_balance(self):
        frappe.get_all.return_value = []
        frappe.db.sql.return_value = [{"account_name": "BANCO INTER", "balance": 67488.03}]
        result = _bank_balance_section()
        self.assertIn("67,488.03", result)

    def test_no_accounts(self):
        frappe.get_all.return_value = []
        frappe.db.sql.return_value = []
        result = _bank_balance_section()
        self.assertIn("Nenhuma conta", result)


class TestPayablesSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_monday_shows_7_day_detail(self):
        # First call: overdue (empty), second call: upcoming 7 days
        frappe.get_all.side_effect = [
            [],  # overdue
            [
                {"name": "PI-001", "supplier_name": "Fornecedor A", "outstanding_amount": 5000, "due_date": "2026-03-27"},
                {"name": "PI-002", "supplier_name": "Fornecedor B", "outstanding_amount": 3500, "due_date": "2026-03-28"},
            ],
        ]
        result = _payables_section(date(2026, 3, 23), is_monday=True)  # Monday
        self.assertIn("8,500.00", result)
        self.assertIn("Proximos 7 dias", result)
        self.assertIn("Fornecedor A", result)
        self.assertIn("PI-001", result)

    def test_weekday_shows_today_only(self):
        frappe.get_all.side_effect = [
            [],  # overdue
            [{"name": "PI-001", "supplier_name": "Fornecedor A", "outstanding_amount": 2000}],  # today
        ]
        result = _payables_section(date(2026, 3, 24), is_monday=False)  # Tuesday
        self.assertIn("Vencendo hoje", result)
        self.assertIn("2,000.00", result)

    def test_shows_overdue(self):
        frappe.get_all.side_effect = [
            [{"name": "PI-OLD", "supplier_name": "Atrasado", "outstanding_amount": 10000, "due_date": "2026-03-20"}],
            [],  # today
        ]
        result = _payables_section(date(2026, 3, 26), is_monday=False)
        self.assertIn("Vencido", result)
        self.assertIn("10,000.00", result)

    def test_no_payments_weekday(self):
        frappe.get_all.side_effect = [[], []]
        result = _payables_section(date(2026, 3, 26), is_monday=False)
        self.assertIn("Nenhum pagamento para hoje", result)

    def test_no_payments_monday(self):
        frappe.get_all.side_effect = [[], []]
        result = _payables_section(date(2026, 3, 23), is_monday=True)
        self.assertIn("Nenhum pagamento nos proximos 7 dias", result)


class TestCashFlowSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.side_effect = None
        frappe.get_all.return_value = []
        frappe.db.sql.side_effect = None

    def test_shows_projection(self):
        def sql_side_effect(query, *args, **kwargs):
            if "GL Entry" in query:
                return [{"balance": 50000}]
            elif "Sales Invoice" in query:
                return [{"total": 10000}]
            elif "Purchase Invoice" in query:
                return [{"total": 20000}]
            return []
        frappe.db.sql.side_effect = sql_side_effect
        frappe.get_all.return_value = [{"estimated_amount": 5000}]  # recurring
        result = _cash_flow_section(date(2026, 3, 23))
        self.assertIn("50,000.00", result)       # saldo atual
        self.assertIn("10,000.00", result)       # a receber
        self.assertIn("20,000.00", result)       # a pagar
        self.assertIn("5,000.00", result)        # recorrentes
        self.assertIn("35,000.00", result)       # projetado: 50k + 10k - 20k - 5k

    def test_warns_negative_balance(self):
        def sql_side_effect(query, *args, **kwargs):
            if "GL Entry" in query:
                return [{"balance": 5000}]
            elif "Sales Invoice" in query:
                return [{"total": 0}]
            elif "Purchase Invoice" in query:
                return [{"total": 10000}]
            return []
        frappe.db.sql.side_effect = sql_side_effect
        result = _cash_flow_section(date(2026, 3, 23))
        self.assertIn("ATENCAO", result)
        self.assertIn("negativo", result)

    def test_warns_low_balance(self):
        def sql_side_effect(query, *args, **kwargs):
            if "GL Entry" in query:
                return [{"balance": 8000}]
            elif "Sales Invoice" in query:
                return [{"total": 0}]
            elif "Purchase Invoice" in query:
                return [{"total": 5000}]
            return []
        frappe.db.sql.side_effect = sql_side_effect
        result = _cash_flow_section(date(2026, 3, 23))
        self.assertIn("baixo", result)


class TestPendingActionsSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_shows_pending_approvals(self):
        frappe.db.count.side_effect = [5, 3]
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
        frappe.db.sql.side_effect = None

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
