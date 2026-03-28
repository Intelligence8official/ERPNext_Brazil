import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.notifications import notify_desk


class TestNotifyDesk(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.new_doc.side_effect = None
        self.mock_doc = MagicMock()
        frappe.new_doc.return_value = self.mock_doc

    def test_creates_notification_log(self):
        notify_desk("Test Title", "Test Message")
        frappe.new_doc.assert_called_once_with("Notification Log")
        self.mock_doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_sets_subject_and_content(self):
        notify_desk("My Title", "My Message")
        self.assertEqual(self.mock_doc.subject, "My Title")
        self.assertEqual(self.mock_doc.email_content, "My Message")

    def test_sets_document_link(self):
        notify_desk("Title", "Msg", document_type="Purchase Order", document_name="PO-001")
        self.assertEqual(self.mock_doc.document_type, "Purchase Order")
        self.assertEqual(self.mock_doc.document_name, "PO-001")

    def test_default_user_is_administrator(self):
        notify_desk("Title", "Msg")
        self.assertEqual(self.mock_doc.for_user, "Administrator")

    def test_custom_user(self):
        notify_desk("Title", "Msg", user="user@test.com")
        self.assertEqual(self.mock_doc.for_user, "user@test.com")

    def test_handles_error_gracefully(self):
        frappe.new_doc.side_effect = Exception("DB error")
        notify_desk("Title", "Msg")  # Should not raise


if __name__ == "__main__":
    unittest.main()
