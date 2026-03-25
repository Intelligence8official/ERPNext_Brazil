import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

# Mock channel_router
sys.modules.setdefault("brazil_module.services.intelligence.channels.channel_router", MagicMock())

import unittest
import brazil_module.services.intelligence.channels.erp_chat as erp_chat


class TestSendMessage(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.enqueue.side_effect = None
        frappe.utils.now_datetime.return_value = "2026-03-25 10:00:00"

    def test_returns_conversation_name(self):
        # Mock the ChannelRouter
        mock_router = MagicMock()
        mock_router.route_message.return_value = "CONV-001"
        erp_chat.ChannelRouter = lambda: mock_router
        result = erp_chat.send_message("user@test.com", "Hello agent")
        self.assertEqual(result["conversation"], "CONV-001")
        self.assertEqual(result["status"], "sent")

    def test_enqueues_agent_event(self):
        mock_router = MagicMock()
        mock_router.route_message.return_value = "CONV-001"
        erp_chat.ChannelRouter = lambda: mock_router
        erp_chat.send_message("user@test.com", "Test message")
        frappe.enqueue.assert_called_once()


class TestGetHistory(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_returns_messages_list(self):
        mock_conv = MagicMock()
        msg1 = MagicMock()
        msg1.channel = "telegram"
        msg1.direction = "incoming"
        msg1.actor = "human"
        msg1.content = "Hello"
        msg1.timestamp = "2026-03-25 09:00:00"
        mock_conv.messages = [msg1]
        frappe.get_doc.return_value = mock_conv
        result = erp_chat.get_conversation_history("CONV-001")
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["content"], "Hello")


if __name__ == "__main__":
    unittest.main()
