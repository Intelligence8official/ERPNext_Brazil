import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.channels.channel_router import ChannelRouter


class TestRouteMessage(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.side_effect = None
        frappe.get_all.return_value = []
        frappe.utils.now_datetime.return_value = "2026-03-25 10:00:00"
        self.mock_conv = MagicMock()
        self.mock_conv.name = "CONV-001"
        frappe.new_doc.return_value = self.mock_conv

    def test_creates_new_conversation_when_none_exists(self):
        frappe.get_all.return_value = []
        router = ChannelRouter()
        name = router.route_message(
            channel="telegram", direction="incoming", actor="human",
            content="Hello", related_doctype="Purchase Order", related_docname="PO-001",
        )
        frappe.new_doc.assert_called_with("I8 Conversation")
        self.mock_conv.insert.assert_called_once_with(ignore_permissions=True)
        self.assertEqual(name, "CONV-001")

    def test_reuses_existing_active_conversation(self):
        frappe.get_all.return_value = [{"name": "CONV-EXISTING"}]
        existing = MagicMock()
        existing.name = "CONV-EXISTING"
        frappe.get_doc.return_value = existing
        router = ChannelRouter()
        name = router.route_message(
            channel="erp_chat", direction="incoming", actor="human",
            content="Test", related_doctype="Purchase Order", related_docname="PO-001",
        )
        frappe.new_doc.assert_not_called()
        self.assertEqual(name, "CONV-EXISTING")

    def test_appends_message_to_conversation(self):
        mock_conv = MagicMock()
        mock_conv.name = "CONV-001"
        frappe.get_all.return_value = [{"name": "CONV-001"}]
        frappe.get_doc.return_value = mock_conv
        router = ChannelRouter()
        router.route_message(
            channel="telegram", direction="outgoing", actor="agent",
            content="Response here",
        )
        mock_conv.append.assert_called_once()
        call_args = mock_conv.append.call_args
        self.assertEqual(call_args[0][0], "messages")
        msg_data = call_args[0][1]
        self.assertEqual(msg_data["channel"], "telegram")
        self.assertEqual(msg_data["content"], "Response here")
        self.assertEqual(msg_data["actor"], "agent")

    def test_saves_after_append(self):
        mock_conv = MagicMock()
        mock_conv.name = "CONV-001"
        frappe.get_all.return_value = [{"name": "CONV-001"}]
        frappe.get_doc.return_value = mock_conv
        router = ChannelRouter()
        router.route_message(channel="system", direction="internal", actor="agent", content="action taken")
        mock_conv.save.assert_called_once_with(ignore_permissions=True)

    def test_creates_conversation_without_related_doc(self):
        frappe.get_all.return_value = []
        router = ChannelRouter()
        name = router.route_message(
            channel="telegram", direction="incoming", actor="human", content="General question",
        )
        frappe.new_doc.assert_called_with("I8 Conversation")
        self.assertEqual(name, "CONV-001")

    def test_telegram_message_id_stored(self):
        mock_conv = MagicMock()
        mock_conv.name = "CONV-001"
        frappe.get_all.return_value = [{"name": "CONV-001"}]
        frappe.get_doc.return_value = mock_conv
        router = ChannelRouter()
        router.route_message(
            channel="telegram", direction="incoming", actor="human",
            content="test", telegram_message_id="12345",
        )
        msg_data = mock_conv.append.call_args[0][1]
        self.assertEqual(msg_data["telegram_message_id"], "12345")


if __name__ == "__main__":
    unittest.main()
