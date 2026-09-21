"""Tests for `alert_operator` (spec 4.1): three channels, each on its own, never raises."""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from brazil_module.tests._payment_fakes import DOCTYPE, FakeDB, install_frappe_mock, patch_frappe, patch_frappe_db

frappe = install_frappe_mock()

import brazil_module.services.banking.payment_alerts as alerts
from brazil_module.services.banking.payment_alerts import alert_operator

TELEGRAM_MODULE = "brazil_module.services.intelligence.channels.telegram_bot"
ORDER = "IPO-2026-00001"
SUBJECT = "Inter payment needs verification"
MESSAGE = "The bank did not answer. Check the statement before doing anything else."


class _Bot:
    """TelegramBot, recording what it was asked to send."""

    sent: list = []
    init_error = None
    send_error = None

    def __init__(self):
        if _Bot.init_error:
            raise _Bot.init_error

    def send_message(self, chat_id, text, reply_markup=None):
        if _Bot.send_error:
            raise _Bot.send_error
        _Bot.sent.append((chat_id, text))
        return {"ok": True}


class AlertCase(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.log_error.side_effect = None
        frappe.new_doc.side_effect = self._new_doc
        self.notifications = []
        self.insert_errors = {}

        self.db = FakeDB(singles={"I8 Agent Settings": {"telegram_chat_id": "chat-1"}})
        self.db.add("Has Role", "hr-1", role="Banco Inter Manager", parenttype="User", parent="abel@i8.com")
        self.db.add("Has Role", "hr-2", role="Banco Inter Manager", parenttype="User", parent="maria@i8.com")
        self.db.add("Has Role", "hr-3", role="Banco Inter Manager", parenttype="User", parent="gone@i8.com")
        self.db.add("Has Role", "hr-4", role="Banco Inter User", parenttype="User", parent="clerk@i8.com")
        self.db.add("Has Role", "hr-5", role="Banco Inter Manager", parenttype="Role Profile", parent="Finance")
        for user, enabled in (("abel@i8.com", 1), ("maria@i8.com", 1), ("gone@i8.com", 0), ("clerk@i8.com", 1)):
            self.db.add("User", user, enabled=enabled)
        patch_frappe_db(self, self.db)

        _Bot.sent, _Bot.init_error, _Bot.send_error = [], None, None
        self.use_telegram_module(types.SimpleNamespace(TelegramBot=_Bot))
        self.patch(alerts, "get_url_to_form", lambda doctype, name: f"https://erp.test/app/inter-payment-order/{name}")
        self.addCleanup(setattr, frappe.new_doc, "side_effect", None)
        self.addCleanup(setattr, frappe.log_error, "side_effect", None)

    def patch(self, target, attribute, value):
        patcher = patch.object(target, attribute, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_telegram_module(self, module):
        """`None` makes the lazy import raise ImportError, like a broken install."""
        patcher = patch.dict(sys.modules, {TELEGRAM_MODULE: module})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _new_doc(self, doctype):
        doc = types.SimpleNamespace(doctype=doctype, insert=MagicMock())
        doc.insert.side_effect = lambda **kw: self._insert(doc)
        self.notifications.append(doc)
        return doc

    def _insert(self, doc):
        error = self.insert_errors.get(doc.for_user)
        if error:
            raise error

    def inserted_for(self):
        return [doc.for_user for doc in self.notifications if doc.insert.called]

    def error_log_titles(self):
        return [call.kwargs.get("title") for call in frappe.log_error.call_args_list]


class TestEveryChannelFires(AlertCase):
    def test_writes_an_error_log_linked_to_the_order(self):
        alert_operator(SUBJECT, MESSAGE, ORDER)

        frappe.log_error.assert_called_once()
        kwargs = frappe.log_error.call_args.kwargs
        self.assertEqual(kwargs["title"], SUBJECT)
        self.assertIn(MESSAGE, kwargs["message"])
        self.assertIn(ORDER, kwargs["message"])
        self.assertEqual((kwargs["reference_doctype"], kwargs["reference_name"]), (DOCTYPE, ORDER))

    def test_rings_the_bell_of_every_enabled_manager(self):
        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(sorted(self.inserted_for()), ["abel@i8.com", "maria@i8.com"])
        for doc in self.notifications:
            self.assertEqual(doc.doctype, "Notification Log")
            self.assertEqual(doc.type, "Alert")
            self.assertEqual(doc.subject, SUBJECT)
            self.assertIn(MESSAGE, doc.email_content)
            self.assertIn(ORDER, doc.email_content)
            self.assertEqual((doc.document_type, doc.document_name), (DOCTYPE, ORDER))
            doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_sends_a_telegram_message_with_the_order_name_and_its_link(self):
        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(len(_Bot.sent), 1)
        chat_id, text = _Bot.sent[0]
        self.assertEqual(chat_id, "chat-1")
        for fragment in (SUBJECT, MESSAGE, ORDER, f"https://erp.test/app/inter-payment-order/{ORDER}"):
            self.assertIn(fragment, text)

    def test_an_order_name_already_in_the_message_is_not_needed_twice_but_is_there(self):
        alert_operator(SUBJECT, f"{ORDER} was not sent", ORDER)

        self.assertIn(ORDER, _Bot.sent[0][1])

    def test_without_an_order_there_is_no_document_link(self):
        alert_operator(SUBJECT, MESSAGE)

        kwargs = frappe.log_error.call_args.kwargs
        self.assertIsNone(kwargs.get("reference_doctype"))
        self.assertIsNone(kwargs.get("reference_name"))
        for doc in self.notifications:
            self.assertFalse(hasattr(doc, "document_type"))
            self.assertFalse(hasattr(doc, "document_name"))
        self.assertIn(MESSAGE, _Bot.sent[0][1])
        self.assertNotIn("None", _Bot.sent[0][1])

    def test_returns_nothing_and_leaves_the_transaction_to_the_caller(self):
        self.assertIsNone(alert_operator(SUBJECT, MESSAGE, ORDER))

        kinds = {event[0] for event in self.db.events}
        self.assertNotIn("commit", kinds)
        self.assertNotIn("rollback", kinds)
        self.assertNotIn("set_value", kinds)

    def test_the_error_log_title_is_one_line_of_at_most_140_characters(self):
        # frappe.log_error swaps title and message when the title has a line break,
        # and Error Log.method is a 140-character column.
        alert_operator("Payment\nneeds   verification " + "x" * 200, MESSAGE, ORDER)

        title = frappe.log_error.call_args.kwargs["title"]
        self.assertNotIn("\n", title)
        self.assertLessEqual(len(title), 140)
        self.assertTrue(title.startswith("Payment needs verification"))

    def test_text_that_came_from_the_bank_cannot_inject_html_into_the_desk(self):
        alert_operator(
            "Rejected <img src=x onerror=alert(1)>", "Bank said: <script>alert(1)</script>\nsecond line", ORDER
        )

        doc = self.notifications[0]
        self.assertNotIn("<script>", doc.email_content)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", doc.email_content)
        self.assertIn("<br>", doc.email_content)
        self.assertNotIn("<img", doc.subject)
        self.assertIn("&lt;img", doc.subject)
        # The Error Log and Telegram get the text as it is: both escape on their own.
        self.assertIn("<script>", frappe.log_error.call_args.kwargs["message"])
        self.assertIn("<script>", _Bot.sent[0][1])

    def test_nobody_holding_the_role_falls_back_to_the_administrator(self):
        for name in ("hr-1", "hr-2", "hr-3"):
            self.db.row("Has Role", name)["role"] = "Accounts User"

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(self.inserted_for(), ["Administrator"])


class TestTelegramIsOptional(AlertCase):
    def test_is_skipped_when_there_is_no_chat_id(self):
        self.db.singles["I8 Agent Settings"]["telegram_chat_id"] = ""
        self.use_telegram_module(None)  # importing it would fail - and be reported

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(_Bot.sent, [])
        self.assertEqual(self.error_log_titles(), [SUBJECT])
        self.assertEqual(len(self.inserted_for()), 2)

    def test_is_skipped_when_the_agent_was_never_configured(self):
        self.db.singles.clear()

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(_Bot.sent, [])
        self.assertEqual(self.error_log_titles(), [SUBJECT])


class TestAChannelFailingDoesNotStopTheOthers(AlertCase):
    def assert_bell_and_telegram(self):
        self.assertEqual(sorted(self.inserted_for()), ["abel@i8.com", "maria@i8.com"])
        self.assertEqual(len(_Bot.sent), 1)

    def test_error_log_failing(self):
        frappe.log_error.side_effect = RuntimeError("Error Log table is locked")

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assert_bell_and_telegram()

    def test_the_role_lookup_failing(self):
        patch_frappe(self, get_all=MagicMock(side_effect=RuntimeError("Has Role is gone")))

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(self.inserted_for(), [])
        self.assertEqual(len(_Bot.sent), 1)
        self.assertEqual(self.error_log_titles()[0], SUBJECT)

    def test_new_doc_failing(self):
        frappe.new_doc.side_effect = RuntimeError("Notification Log is gone")

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(len(_Bot.sent), 1)
        self.assertEqual(self.error_log_titles()[0], SUBJECT)

    def test_one_notification_insert_failing_still_notifies_the_next_manager(self):
        self.insert_errors["abel@i8.com"] = RuntimeError("deadlock")

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(sorted(self.inserted_for()), ["abel@i8.com", "maria@i8.com"])
        self.assertEqual(len(_Bot.sent), 1)

    def test_the_telegram_import_failing(self):
        self.use_telegram_module(None)

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(sorted(self.inserted_for()), ["abel@i8.com", "maria@i8.com"])
        self.assertEqual(self.error_log_titles()[0], SUBJECT)

    def test_the_telegram_bot_failing_to_start(self):
        _Bot.init_error = RuntimeError("no telegram token")

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(sorted(self.inserted_for()), ["abel@i8.com", "maria@i8.com"])
        self.assertEqual(self.error_log_titles()[0], SUBJECT)

    def test_the_telegram_send_failing(self):
        _Bot.send_error = TimeoutError("telegram timed out")

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(sorted(self.inserted_for()), ["abel@i8.com", "maria@i8.com"])
        self.assertEqual(self.error_log_titles()[0], SUBJECT)

    def test_reading_the_chat_id_failing(self):
        self.patch(self.db, "get_single_value", MagicMock(side_effect=RuntimeError("settings are gone")))

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertEqual(_Bot.sent, [])
        self.assertEqual(sorted(self.inserted_for()), ["abel@i8.com", "maria@i8.com"])

    def test_the_link_failing_costs_only_the_link(self):
        self.patch(alerts, "get_url_to_form", MagicMock(side_effect=RuntimeError("no request, no host")))

        alert_operator(SUBJECT, MESSAGE, ORDER)

        self.assertIn(ORDER, _Bot.sent[0][1])
        self.assertIn(MESSAGE, _Bot.sent[0][1])
        self.assert_bell_and_telegram()

    def test_a_failed_channel_is_reported_in_the_error_log(self):
        _Bot.send_error = TimeoutError("telegram timed out")

        alert_operator(SUBJECT, MESSAGE, ORDER)

        titles = self.error_log_titles()
        self.assertEqual(len(titles), 2)
        self.assertIn("Telegram", titles[1])
        self.assertIn("telegram timed out", frappe.log_error.call_args_list[1].kwargs["message"])

    def test_everything_failing_at_once_still_does_not_raise(self):
        frappe.log_error.side_effect = RuntimeError("Error Log is gone")
        frappe.new_doc.side_effect = RuntimeError("Notification Log is gone")
        _Bot.send_error = RuntimeError("telegram is gone")
        self.patch(alerts, "get_url_to_form", MagicMock(side_effect=RuntimeError("no host")))

        with patch.object(alerts.sys, "stderr", MagicMock()):
            self.assertIsNone(alert_operator(SUBJECT, MESSAGE, ORDER))

    def test_arguments_that_are_not_strings_do_not_raise(self):
        self.assertIsNone(alert_operator(None, None, None))
        self.assertIsNone(alert_operator(RuntimeError("boom"), {"bank": "said no"}, 42))


if __name__ == "__main__":
    unittest.main()
