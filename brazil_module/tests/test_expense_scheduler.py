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

from brazil_module.services.intelligence.recurring.expense_scheduler import (
    _is_due, calculate_next_due, daily_check,
)


class TestIsDue(unittest.TestCase):
    def test_due_when_today_past_trigger_date(self):
        expense = {"next_due": date(2026, 4, 1), "lead_days": 10, "last_created": None}
        self.assertTrue(_is_due(expense, date(2026, 3, 25)))

    def test_not_due_when_before_trigger(self):
        expense = {"next_due": date(2026, 4, 1), "lead_days": 5, "last_created": None}
        self.assertFalse(_is_due(expense, date(2026, 3, 25)))

    def test_not_due_when_already_created(self):
        expense = {"next_due": date(2026, 4, 1), "lead_days": 10, "last_created": date(2026, 4, 1)}
        self.assertFalse(_is_due(expense, date(2026, 3, 25)))

    def test_not_due_when_no_next_due(self):
        expense = {"next_due": None, "lead_days": 0, "last_created": None}
        self.assertFalse(_is_due(expense, date(2026, 3, 25)))


class TestCalculateNextDue(unittest.TestCase):
    def test_monthly(self):
        result = calculate_next_due("Monthly", 15, date(2026, 3, 25))
        self.assertEqual(result, date(2026, 4, 15))

    def test_monthly_december_wraps(self):
        result = calculate_next_due("Monthly", 10, date(2026, 12, 1))
        self.assertEqual(result, date(2027, 1, 10))

    def test_weekly(self):
        result = calculate_next_due("Weekly", 1, date(2026, 3, 25))
        self.assertEqual(result, date(2026, 4, 1))

    def test_quarterly(self):
        result = calculate_next_due("Quarterly", 5, date(2026, 1, 15))
        self.assertEqual(result, date(2026, 4, 5))

    def test_yearly(self):
        result = calculate_next_due("Yearly", 1, date(2026, 3, 25))
        self.assertEqual(result, date(2027, 3, 1))

    def test_day_31_clamps_to_month_end(self):
        result = calculate_next_due("Monthly", 31, date(2026, 1, 15))
        self.assertEqual(result, date(2026, 2, 28))


class TestDailyCheck(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None
        frappe.enqueue.side_effect = None

    def test_skips_when_disabled(self):
        frappe.db.get_single_value.return_value = False
        daily_check()
        frappe.get_all.assert_not_called()

    def test_enqueues_due_expenses(self):
        frappe.db.get_single_value.return_value = True
        frappe.get_all.return_value = [{
            "name": "RE-001", "title": "Hosting", "supplier": "DO",
            "document_type": "Purchase Order", "estimated_amount": 500,
            "currency": "USD", "frequency": "Monthly", "day_of_month": 5,
            "lead_days": 10, "notify_supplier": False,
            "last_created": None, "next_due": date.today(),
        }]
        daily_check()
        frappe.enqueue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
