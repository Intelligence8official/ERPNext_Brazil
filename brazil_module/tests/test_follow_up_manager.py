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
from unittest.mock import patch

import brazil_module.services.intelligence.recurring.follow_up_manager as _job_mod

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



def _open_the_window(test_case):
    """These tests are about what the job DOES; its hour has its own tests below."""
    patcher = patch.object(_job_mod, "is_due", return_value=True)
    patcher.start()
    test_case.addCleanup(patcher.stop)
    mark = patch.object(_job_mod, "mark_done")
    test_case.marked = mark.start()
    test_case.addCleanup(mark.stop)


class TestCheckOverdue(unittest.TestCase):
    def setUp(self):
        _open_the_window(self)
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
            [{"name": "Test", "supplier_name": "Test Corp", "expected_nf_days": 5,
              "follow_up_after_days": 3, "max_follow_ups": 3}],
            [{"name": "PO-001", "transaction_date": date(2026, 3, 10), "grand_total": 500}],
        ]
        frappe.db.exists.return_value = False
        check_overdue()
        frappe.enqueue.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TestTheConfiguredHour(unittest.TestCase):
    """The hour lives in I8 Agent Settings, so the job is woken often and decides for itself."""

    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None
        frappe.db.get_single_value.return_value = True
        frappe.get_all.side_effect = None
        frappe.get_all.return_value = []

    def test_outside_its_window_it_does_nothing(self):
        with patch.object(_job_mod, "is_due", return_value=False) as due:
            _job_mod.check_overdue()

        frappe.get_all.assert_not_called()
        self.assertEqual(due.call_args.args[0], "followup_check_time")
        self.assertEqual(due.call_args.args[1], _job_mod.FOLLOWUP_CHECK_MARKER)

    def test_the_day_is_marked_only_after_the_work(self):
        with patch.object(_job_mod, "is_due", return_value=True), \
             patch.object(_job_mod, "mark_done") as mark:
            _job_mod.check_overdue()

        mark.assert_called_once_with(_job_mod.FOLLOWUP_CHECK_MARKER)

    def test_a_disabled_agent_is_still_checked_first(self):
        frappe.db.get_single_value.return_value = False
        with patch.object(_job_mod, "is_due") as due:
            _job_mod.check_overdue()

        due.assert_not_called()
