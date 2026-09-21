"""Tests for the shared "is it this job's hour yet?" check.

The scheduler map in hooks.py is a static dict, so a job whose hour is configurable has to be woken
often and decide for itself. Three things have to hold, and each one has already gone wrong here:
the configured hour must be read as the type Frappe really hands over (a Time field arrives as a
``timedelta``), the check must not write the marker it reads, and a job must not run twice in a day.
"""

import datetime
import sys
import unittest
from unittest.mock import MagicMock, patch

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import brazil_module.services.intelligence.daily_window as _dw

FIELD = "expense_check_time"
MARKER = "i8_last_expense_check_date"


class WindowCase(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None
        frappe.cache.get_value.return_value = None

    def due_at(self, moment, configured, marker=None):
        frappe.db.get_single_value.return_value = configured
        frappe.cache.get_value.return_value = marker
        return _dw.is_due(FIELD, MARKER, default="07:00:00", now=moment)


class TestTheConfiguredHourIsRead(WindowCase):
    def test_a_time_field_arrives_as_a_timedelta(self):
        """What production hands over. Splitting the object itself is what broke the briefing."""
        configured = datetime.timedelta(hours=4)

        self.assertTrue(self.due_at(datetime.datetime(2026, 9, 21, 4, 5), configured))
        self.assertFalse(self.due_at(datetime.datetime(2026, 9, 21, 18, 4), configured))

    def test_a_string_still_works(self):
        self.assertTrue(self.due_at(datetime.datetime(2026, 9, 21, 9, 35), "09:30:00"))

    def test_hours_and_minutes_without_seconds(self):
        """The two settings that were never read carry '07:00' and '09:00'."""
        self.assertTrue(self.due_at(datetime.datetime(2026, 9, 21, 7, 2), "07:00"))

    def test_a_time_object_works(self):
        self.assertTrue(self.due_at(datetime.datetime(2026, 9, 21, 9, 35), datetime.time(9, 30)))

    def test_an_empty_setting_falls_back_to_the_default(self):
        self.assertTrue(self.due_at(datetime.datetime(2026, 9, 21, 7, 10), None))
        self.assertFalse(self.due_at(datetime.datetime(2026, 9, 21, 8, 10), None))

    def test_an_unreadable_setting_falls_back_instead_of_raising(self):
        """A job woken every fifteen minutes must not die every fifteen minutes."""
        self.assertTrue(self.due_at(datetime.datetime(2026, 9, 21, 7, 10), object()))
        frappe.log_error.assert_called()


class TestTheWindow(WindowCase):
    CONFIGURED = "07:00:00"

    def test_it_opens_at_the_configured_minute(self):
        self.assertTrue(self.due_at(datetime.datetime(2026, 9, 21, 7, 0), self.CONFIGURED))

    def test_it_is_closed_before(self):
        self.assertFalse(self.due_at(datetime.datetime(2026, 9, 21, 6, 59), self.CONFIGURED))

    def test_it_stays_open_for_fifteen_minutes(self):
        self.assertTrue(self.due_at(datetime.datetime(2026, 9, 21, 7, 14), self.CONFIGURED))
        self.assertFalse(self.due_at(datetime.datetime(2026, 9, 21, 7, 15), self.CONFIGURED))


class TestOncePerDay(WindowCase):
    def test_a_marker_for_today_closes_the_window(self):
        self.assertFalse(
            self.due_at(datetime.datetime(2026, 9, 21, 7, 5), "07:00:00", marker="2026-09-21")
        )

    def test_a_marker_from_yesterday_does_not(self):
        self.assertTrue(
            self.due_at(datetime.datetime(2026, 9, 21, 7, 5), "07:00:00", marker="2026-09-20")
        )

    def test_asking_never_writes_the_marker(self):
        """Read-only on purpose: the job marks the day only once it has done the work, so a
        failure is retried on the next tick instead of latching the job off until tomorrow."""
        self.due_at(datetime.datetime(2026, 9, 21, 7, 5), "07:00:00")

        frappe.cache.set_value.assert_not_called()

    def test_marking_records_the_day_and_expires(self):
        _dw.mark_done(MARKER, now=datetime.datetime(2026, 9, 21, 7, 5))

        frappe.cache.set_value.assert_called_once()
        args, kwargs = frappe.cache.set_value.call_args
        self.assertEqual(args[0], MARKER)
        self.assertEqual(args[1], "2026-09-21")
        self.assertEqual(kwargs.get("expires_in_sec"), 86400)


if __name__ == "__main__":
    unittest.main()
