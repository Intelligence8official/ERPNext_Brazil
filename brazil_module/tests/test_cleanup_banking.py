"""Tests for the banking log cleanup service."""

import unittest
from unittest.mock import MagicMock
import sys
from datetime import datetime

# Ensure frappe mock is in place
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import brazil_module.services.banking.cleanup as _cl_mod
from brazil_module.services.banking.cleanup import (
    cleanup_old_api_logs,
    cleanup_old_webhook_logs,
)

# Patch module-level binding - must return datetime object for timedelta arithmetic
_cl_mod.now_datetime = lambda: datetime(2024, 1, 15, 12, 0, 0)


def _reset():
    frappe.reset_mock()
    frappe.db.get_single_value.side_effect = None
    frappe.get_all.side_effect = None
    frappe.get_all.return_value = []
    frappe.delete_doc.side_effect = None
    frappe.db.commit.side_effect = None


class TestCleanupApiLogs(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_deletes_old_logs(self):
        frappe.db.get_single_value.return_value = True
        frappe.get_all.return_value = ["LOG-001", "LOG-002", "LOG-003"]

        cleanup_old_api_logs(days=90)

        self.assertEqual(frappe.delete_doc.call_count, 3)
        frappe.db.commit.assert_called_once()

    def test_skips_when_disabled(self):
        frappe.db.get_single_value.return_value = False

        cleanup_old_api_logs()

        frappe.get_all.assert_not_called()


class TestCleanupWebhookLogs(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_deletes_old_logs(self):
        frappe.db.get_single_value.return_value = True
        frappe.get_all.return_value = ["WHLOG-001", "WHLOG-002"]

        cleanup_old_webhook_logs(days=90)

        self.assertEqual(frappe.delete_doc.call_count, 2)
        frappe.db.commit.assert_called_once()

    def test_skips_when_disabled(self):
        frappe.db.get_single_value.return_value = False

        cleanup_old_webhook_logs()

        frappe.get_all.assert_not_called()


if __name__ == "__main__":
    unittest.main()
