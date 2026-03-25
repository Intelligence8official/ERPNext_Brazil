"""
Tests for TelegramBot - webhook auth, user authorization, message handling,
callback approval flow, and send_message integration.

Pattern: inject frappe mock into sys.modules BEFORE importing the module under test.
"""
import json
import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Bootstrap frappe mock (must happen before any brazil_module import)
# ---------------------------------------------------------------------------
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils
    sys.modules["frappe.model.document"] = MagicMock()

frappe = sys.modules["frappe"]

# Stub heavy transitive dependencies so the module can be imported without
# a real Frappe installation.
for _mod in [
    "brazil_module.intelligence8.doctype.i8_agent_settings.i8_agent_settings",
    "brazil_module.services.intelligence.channels.channel_router",
    "brazil_module.services.intelligence.prompts.approval_formatter",
    "brazil_module.services.intelligence.agent",
    "requests",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import unittest

# Import module under test AFTER mocks are in place
from brazil_module.services.intelligence.channels import telegram_bot as _tb_mod

# Patch requests at module level so every instantiation picks the mock up
_requests_mock = MagicMock()
_tb_mod.requests = _requests_mock

# Patch the heavy internal imports that happen inside __init__
_MockI8AgentSettings = sys.modules[
    "brazil_module.intelligence8.doctype.i8_agent_settings.i8_agent_settings"
].I8AgentSettings
_MockChannelRouter = sys.modules[
    "brazil_module.services.intelligence.channels.channel_router"
].ChannelRouter
_mock_format = sys.modules[
    "brazil_module.services.intelligence.prompts.approval_formatter"
].format_approval_message

from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot


# ---------------------------------------------------------------------------
# Helper – build a TelegramBot with fully controlled settings / router
# ---------------------------------------------------------------------------

def _make_bot(
    token="bot-token-123",
    webhook_secret="my-secret",
    telegram_users=None,
    telegram_chat_id="chat-42",
):
    """Return a TelegramBot whose internal dependencies are mocked."""
    settings = MagicMock()
    settings.telegram_users = telegram_users or []
    settings.telegram_chat_id = telegram_chat_id
    settings.get_password.return_value = webhook_secret

    _MockI8AgentSettings.get_telegram_token.return_value = token
    _MockI8AgentSettings.get_settings.return_value = settings

    router = MagicMock()
    _MockChannelRouter.return_value = router

    bot = TelegramBot()
    bot._router = router          # expose for assertions
    bot._settings = settings      # expose for assertions
    return bot


def _make_user_row(telegram_user_id, user="admin@example.com", approval_limit=0, active=True):
    row = MagicMock()
    row.telegram_user_id = telegram_user_id
    row.user = user
    row.approval_limit = approval_limit
    row.active = active
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestValidateWebhook(unittest.TestCase):
    """validate_webhook must compare the incoming secret against settings."""

    def setUp(self):
        frappe.reset_mock()
        self.bot = _make_bot(webhook_secret="correct-secret")

    def test_wrong_secret_returns_false(self):
        self.assertFalse(self.bot.validate_webhook("wrong-secret"))

    def test_correct_secret_returns_true(self):
        self.assertTrue(self.bot.validate_webhook("correct-secret"))

    def test_empty_secret_rejected(self):
        self.assertFalse(self.bot.validate_webhook(""))

    def test_secret_is_case_sensitive(self):
        self.assertFalse(self.bot.validate_webhook("Correct-Secret"))


class TestAuthorizeUser(unittest.TestCase):
    """authorize_user returns None for unknown users, dict for known active ones."""

    def setUp(self):
        frappe.reset_mock()
        known = _make_user_row("111111", user="buyer@co.com", approval_limit=5000.0)
        inactive = _make_user_row("222222", user="old@co.com", approval_limit=1000.0, active=False)
        self.bot = _make_bot(telegram_users=[known, inactive])

    def test_unknown_user_returns_none(self):
        result = self.bot.authorize_user("999999")
        self.assertIsNone(result)

    def test_known_active_user_returns_dict(self):
        result = self.bot.authorize_user("111111")
        self.assertIsNotNone(result)
        self.assertEqual(result["user"], "buyer@co.com")
        self.assertAlmostEqual(result["approval_limit"], 5000.0)

    def test_inactive_user_returns_none(self):
        result = self.bot.authorize_user("222222")
        self.assertIsNone(result)

    def test_user_id_compared_as_string(self):
        # telegram_user_id stored as int – authorize_user must coerce to str
        result = self.bot.authorize_user(111111)  # int, not string
        self.assertIsNotNone(result)

    def test_zero_approval_limit_allowed(self):
        row = _make_user_row("333333", approval_limit=0)
        bot = _make_bot(telegram_users=[row])
        result = bot.authorize_user("333333")
        self.assertIsNotNone(result)
        self.assertEqual(result["approval_limit"], 0.0)


class TestHandleCallback(unittest.TestCase):
    """_handle_callback dispatches approve/reject to log.resolve."""

    def _make_log(self, action="create_purchase_order", related_docname="PO-001",
                  related_doctype="Purchase Order", module="purchasing",
                  input_summary=None):
        log = MagicMock()
        log.action = action
        log.related_docname = related_docname
        log.related_doctype = related_doctype
        log.module = module
        log.input_summary = input_summary or json.dumps({"amount": 100})
        return log

    def setUp(self):
        frappe.reset_mock()
        frappe.get_doc.side_effect = None
        user_row = _make_user_row("555", approval_limit=0)  # 0 = unlimited
        self.bot = _make_bot(telegram_users=[user_row])
        self.mock_log = self._make_log()
        frappe.get_doc.return_value = self.mock_log
        _requests_mock.post.return_value.json.return_value = {"ok": True}

    def test_approve_calls_log_resolve_with_success(self):
        callback = {
            "from": {"id": 555},
            "data": "approve:DL-001",
        }
        self.bot._handle_callback(callback)
        self.mock_log.resolve.assert_called_once()
        kwargs = self.mock_log.resolve.call_args[1]
        self.assertEqual(kwargs["result"], "Success")
        self.assertEqual(kwargs["actor"], "Human")
        self.assertTrue(kwargs["human_override"])

    def test_reject_calls_log_resolve_with_rejected(self):
        callback = {
            "from": {"id": 555},
            "data": "reject:DL-001",
        }
        self.bot._handle_callback(callback)
        self.mock_log.resolve.assert_called_once()
        kwargs = self.mock_log.resolve.call_args[1]
        self.assertEqual(kwargs["result"], "Rejected")

    def test_unauthorized_user_ignored(self):
        callback = {
            "from": {"id": 999},   # unknown
            "data": "approve:DL-001",
        }
        self.bot._handle_callback(callback)
        self.mock_log.resolve.assert_not_called()

    def test_approve_enqueues_agent_event(self):
        callback = {
            "from": {"id": 555},
            "data": "approve:DL-001",
        }
        self.bot._handle_callback(callback)
        frappe.enqueue.assert_called_once()
        eq_kwargs = frappe.enqueue.call_args[1]
        self.assertEqual(eq_kwargs["event_type"], "approved_action")
        self.assertEqual(eq_kwargs["event_id"], "DL-001")

    def test_callback_without_log_name_ignored(self):
        callback = {
            "from": {"id": 555},
            "data": "approve:",   # missing name
        }
        self.bot._handle_callback(callback)
        self.mock_log.resolve.assert_not_called()

    def test_approval_limit_exceeded_sends_limit_message(self):
        # User has limit of 100, transaction is 500
        user_row = _make_user_row("777", approval_limit=100.0)
        bot = _make_bot(telegram_users=[user_row])
        log = self._make_log(input_summary=json.dumps({"amount": 500}))
        frappe.get_doc.return_value = log

        callback = {"from": {"id": 777}, "data": "approve:DL-999"}
        with patch.object(bot, "send_message") as mock_send:
            bot._handle_callback(callback)
        log.resolve.assert_not_called()
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        self.assertIn("Limite", msg)

    def test_details_action_sends_detail_message(self):
        callback = {
            "from": {"id": 555},
            "data": "details:DL-001",
        }
        with patch.object(self.bot, "send_message") as mock_send:
            self.bot._handle_callback(callback)
        mock_send.assert_called_once()
        self.mock_log.resolve.assert_not_called()


class TestSendMessage(unittest.TestCase):
    """send_message must POST to the Telegram Bot API with correct payload."""

    def setUp(self):
        frappe.reset_mock()
        _requests_mock.reset_mock()
        _requests_mock.post.return_value.json.return_value = {"ok": True, "result": {}}
        self.bot = _make_bot(token="BOT-TOKEN")

    def test_posts_to_correct_url(self):
        self.bot.send_message("chat-1", "Hello")
        call_args = _requests_mock.post.call_args
        url = call_args[0][0]
        self.assertIn("BOT-TOKEN", url)
        self.assertIn("sendMessage", url)

    def test_payload_contains_chat_id_and_text(self):
        self.bot.send_message("chat-99", "Test message")
        payload = _requests_mock.post.call_args[1]["json"]
        self.assertEqual(payload["chat_id"], "chat-99")
        self.assertEqual(payload["text"], "Test message")

    def test_payload_parse_mode_markdown(self):
        self.bot.send_message("chat-1", "msg")
        payload = _requests_mock.post.call_args[1]["json"]
        self.assertEqual(payload["parse_mode"], "Markdown")

    def test_reply_markup_serialized_to_json_string(self):
        markup = {"inline_keyboard": [[{"text": "A", "callback_data": "b"}]]}
        self.bot.send_message("chat-1", "msg", reply_markup=markup)
        payload = _requests_mock.post.call_args[1]["json"]
        self.assertIn("reply_markup", payload)
        # Must be JSON string, not raw dict
        self.assertIsInstance(payload["reply_markup"], str)
        parsed = json.loads(payload["reply_markup"])
        self.assertIn("inline_keyboard", parsed)

    def test_returns_response_json(self):
        result = self.bot.send_message("chat-1", "msg")
        self.assertEqual(result["ok"], True)


class TestHandleMessage(unittest.TestCase):
    """_handle_message ignores unauthorized users; enqueues event for authorized ones."""

    def setUp(self):
        frappe.reset_mock()
        user_row = _make_user_row("888", user="manager@co.com")
        self.bot = _make_bot(telegram_users=[user_row])
        _requests_mock.post.return_value.json.return_value = {"ok": True}

    def test_unauthorized_user_message_ignored(self):
        message = {
            "from": {"id": 999},
            "text": "Hello",
            "chat": {"id": 42},
            "message_id": 1,
        }
        self.bot._handle_message(message)
        frappe.enqueue.assert_not_called()
        self.bot._router.route_message.assert_not_called()

    def test_authorized_user_enqueues_agent_event(self):
        message = {
            "from": {"id": 888},
            "text": "What is our cash balance?",
            "chat": {"id": 42},
            "message_id": 7,
        }
        self.bot._handle_message(message)
        frappe.enqueue.assert_called_once()
        eq_kwargs = frappe.enqueue.call_args[1]
        self.assertEqual(eq_kwargs["event_type"], "human_message")
        self.assertEqual(eq_kwargs["event_data"]["text"], "What is our cash balance?")
        self.assertEqual(eq_kwargs["event_data"]["user"], "manager@co.com")

    def test_authorized_user_routes_message(self):
        message = {
            "from": {"id": 888},
            "text": "Hi there",
            "chat": {"id": 42},
            "message_id": 8,
        }
        self.bot._handle_message(message)
        self.bot._router.route_message.assert_called_once()
        kw = self.bot._router.route_message.call_args[1]
        self.assertEqual(kw["channel"], "telegram")
        self.assertEqual(kw["direction"], "incoming")
        self.assertEqual(kw["actor"], "human")


class TestHandleUpdate(unittest.TestCase):
    """handle_update dispatches to _handle_callback or _handle_message."""

    def setUp(self):
        frappe.reset_mock()
        self.bot = _make_bot()

    def test_dispatches_callback_query(self):
        update = {"callback_query": {"from": {"id": 1}, "data": "approve:DL-1"}}
        with patch.object(self.bot, "_handle_callback") as mock_cb:
            self.bot.handle_update(update)
        mock_cb.assert_called_once_with(update["callback_query"])

    def test_dispatches_message(self):
        update = {"message": {"from": {"id": 2}, "text": "hi", "chat": {"id": 1}, "message_id": 1}}
        with patch.object(self.bot, "_handle_message") as mock_msg:
            self.bot.handle_update(update)
        mock_msg.assert_called_once_with(update["message"])


class TestExtractTransactionAmount(unittest.TestCase):
    """_extract_transaction_amount parses amount from input_summary JSON."""

    def test_amount_field(self):
        log = MagicMock()
        log.input_summary = json.dumps({"amount": 250.0})
        result = TelegramBot._extract_transaction_amount(log)
        self.assertAlmostEqual(result, 250.0)

    def test_rate_times_qty(self):
        log = MagicMock()
        log.input_summary = json.dumps({"rate": 50.0, "qty": 3})
        result = TelegramBot._extract_transaction_amount(log)
        self.assertAlmostEqual(result, 150.0)

    def test_invalid_json_returns_zero(self):
        log = MagicMock()
        log.input_summary = "not-json"
        result = TelegramBot._extract_transaction_amount(log)
        self.assertEqual(result, 0.0)

    def test_none_input_returns_zero(self):
        log = MagicMock()
        log.input_summary = None
        result = TelegramBot._extract_transaction_amount(log)
        self.assertEqual(result, 0.0)


if __name__ == "__main__":
    unittest.main()
