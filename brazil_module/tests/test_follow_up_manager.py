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

from brazil_module.services.intelligence.recurring.follow_up_manager import (
    check_overdue, _find_overdue_pos,
)


class TestFindOverduePOs(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.exists.side_effect = None

    def test_returns_overdue_pos(self):
        frappe.get_all.return_value = [
            {"name": "PO-001", "transaction_date": date(2026, 3, 10), "grand_total": 1000},
        ]
        frappe.db.exists.return_value = False  # No NF received
        profile = {"supplier": "Test", "expected_nf_days": 5}
        result = _find_overdue_pos(profile)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "PO-001")

    def test_excludes_pos_with_nf(self):
        frappe.get_all.return_value = [
            {"name": "PO-001", "transaction_date": date(2026, 3, 10), "grand_total": 1000},
        ]
        frappe.db.exists.return_value = True  # NF exists
        profile = {"supplier": "Test", "expected_nf_days": 5}
        result = _find_overdue_pos(profile)
        self.assertEqual(len(result), 0)


class TestCheckOverdue(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None
        frappe.enqueue.side_effect = None
        frappe.db.exists.side_effect = None

    def test_skips_when_disabled(self):
        frappe.db.get_single_value.return_value = False
        check_overdue()
        frappe.get_all.assert_not_called()

    def test_enqueues_follow_up_for_overdue(self):
        frappe.db.get_single_value.return_value = True
        frappe.get_all.side_effect = [
            [{"name": "SP-001", "supplier": "Test", "expected_nf_days": 5,
              "follow_up_after_days": 3, "max_follow_ups": 3, "follow_up_interval_days": 3}],
            [{"name": "PO-001", "transaction_date": date(2026, 3, 10), "grand_total": 500}],
        ]
        frappe.db.exists.return_value = False
        check_overdue()
        frappe.enqueue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
