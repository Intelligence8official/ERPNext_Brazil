import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import frappe

import brazil_module.services.intelligence.channels.telegram_health as health_mod
from brazil_module.services.intelligence.channels.telegram_health import (
    MANY_PENDING,
    check,
    register_webhook,
    scheduled_check,
    webhook_url,
)

SITE = "https://erp.intelligence8.com"
EXPECTED_URL = f"{SITE}/api/method/brazil_module.api.telegram_webhook"


class _Telegram:
    """The Bot API, answering what the test says it answers."""

    def __init__(self, me=None, info=None, set_webhook=None):
        self.me = me if me is not None else {"ok": True, "result": {"username": "i8operator_bot"}}
        self.info = info if info is not None else {"ok": True, "result": {"url": EXPECTED_URL, "pending_update_count": 0}}
        self.set_webhook = set_webhook if set_webhook is not None else {"ok": True, "result": True}
        self.calls = []

    def __call__(self, url, **kwargs):
        method = url.rsplit("/", 1)[-1]
        self.calls.append({"method": method, **kwargs})
        answer = {"getMe": self.me, "getWebhookInfo": self.info, "setWebhook": self.set_webhook}[method]
        if isinstance(answer, Exception):
            raise answer
        return MagicMock(json=MagicMock(return_value=answer))


class HealthCase(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None
        frappe.db.get_single_value.return_value = "chat-1"
        frappe.db.set_single_value.side_effect = None
        frappe.utils.get_url.return_value = SITE
        frappe.utils.now_datetime.return_value = datetime(2026, 9, 20, 8, 0, 0)
        health_mod._token = lambda: "bot-token"
        health_mod._secret = lambda: "current-secret"
        self.telegram = _Telegram()
        health_mod.requests = MagicMock(post=self.telegram, get=self.telegram)
        self.told = []
        health_mod._tell_operator = lambda message: self.told.append(message)


class TestCheck(HealthCase):
    def test_a_healthy_bot_reports_its_name_and_address(self):
        status = check()

        self.assertTrue(status["healthy"])
        self.assertEqual(status["bot"], "i8operator_bot")
        self.assertEqual(status["url"], EXPECTED_URL)
        self.assertEqual(status["pending"], 0)

    def test_the_expected_address_is_this_site(self):
        self.assertEqual(webhook_url(), EXPECTED_URL)

    def test_a_webhook_nobody_registered_is_broken_and_fixable(self):
        self.telegram.info = {"ok": True, "result": {"url": "", "pending_update_count": 0}}

        status = check()

        self.assertFalse(status["healthy"])
        self.assertTrue(status["fixable"])
        self.assertIn("nenhum webhook", status["problem"].lower())

    def test_a_webhook_pointing_somewhere_else_is_broken_and_fixable(self):
        # The site moved and nobody told Telegram: the bot goes quiet and
        # nothing in the ERP says why.
        self.telegram.info = {
            "ok": True,
            "result": {"url": "https://old.intelligence8.com/api/method/x", "pending_update_count": 0},
        }

        status = check()

        self.assertFalse(status["healthy"])
        self.assertTrue(status["fixable"])
        self.assertIn("old.intelligence8.com", status["problem"])

    def test_a_delivery_error_is_reported_but_not_fixable(self):
        # Registering again does not fix a 500 on our side.
        self.telegram.info = {
            "ok": True,
            "result": {
                "url": EXPECTED_URL,
                "pending_update_count": 3,
                "last_error_date": 1789000000,
                "last_error_message": "Wrong response from the webhook: 500",
            },
        }

        status = check()

        self.assertFalse(status["healthy"])
        self.assertFalse(status["fixable"])
        self.assertIn("500", status["problem"])

    def test_a_queue_piling_up_is_reported_but_not_fixable(self):
        self.telegram.info = {
            "ok": True,
            "result": {"url": EXPECTED_URL, "pending_update_count": MANY_PENDING + 1},
        }

        status = check()

        self.assertFalse(status["healthy"])
        self.assertFalse(status["fixable"])
        self.assertIn("fila", status["problem"].lower())

    def test_a_refused_token_says_so(self):
        self.telegram.me = {"ok": False, "description": "Unauthorized"}

        status = check()

        self.assertFalse(status["healthy"])
        self.assertFalse(status["fixable"])
        self.assertIn("Unauthorized", status["problem"])

    def test_telegram_being_unreachable_is_a_status_not_a_crash(self):
        self.telegram.me = ConnectionError("no route to host")

        status = check()

        self.assertFalse(status["healthy"])
        self.assertIn("no route to host", status["problem"])

    def test_writes_down_what_it_saw_and_when(self):
        # There is no expiry to show; what the screen can honestly show is
        # the last verdict and its date.
        check()

        written = {call.args[1]: call.args[2] for call in frappe.db.set_single_value.call_args_list}
        self.assertIn("telegram_webhook_status", written)
        self.assertEqual(written["telegram_webhook_checked_on"], datetime(2026, 9, 20, 8, 0, 0))


class TestRegisterWebhook(HealthCase):
    def test_points_telegram_at_this_site(self):
        register_webhook()

        sent = [call for call in self.telegram.calls if call["method"] == "setWebhook"][0]
        self.assertEqual(sent["json"]["url"], EXPECTED_URL)

    def test_a_new_secret_is_only_stored_after_telegram_accepts_it(self):
        # Stored first and refused there, the two sides hold different
        # secrets and every update is rejected as unauthorized.
        self.telegram.set_webhook = {"ok": False, "description": "Bad Request"}

        result = register_webhook(rotate_secret=True)

        self.assertFalse(result["ok"])
        frappe.db.set_single_value.assert_not_any_call = None
        written = [call.args[1] for call in frappe.db.set_single_value.call_args_list]
        self.assertNotIn("telegram_webhook_secret", written)

    def test_stores_the_new_secret_when_telegram_accepts(self):
        result = register_webhook(rotate_secret=True)

        self.assertTrue(result["ok"])
        written = {call.args[1]: call.args[2] for call in frappe.db.set_single_value.call_args_list}
        self.assertTrue(written["telegram_webhook_secret"])
        self.assertNotEqual(written["telegram_webhook_secret"], "current-secret")
        sent = [call for call in self.telegram.calls if call["method"] == "setWebhook"][0]
        self.assertEqual(sent["json"]["secret_token"], written["telegram_webhook_secret"])

    def test_puts_the_old_secret_back_when_it_cannot_be_stored(self):
        # Telegram already accepted the new one; if we cannot keep it, the
        # only way back is to tell Telegram the old one again.
        frappe.db.set_single_value.side_effect = [Exception("db down"), None, None]

        register_webhook(rotate_secret=True)

        secrets = [
            call["json"]["secret_token"]
            for call in self.telegram.calls
            if call["method"] == "setWebhook"
        ]
        self.assertEqual(secrets[-1], "current-secret")

    def test_keeps_the_secret_when_not_asked_to_rotate(self):
        register_webhook()

        sent = [call for call in self.telegram.calls if call["method"] == "setWebhook"][0]
        self.assertEqual(sent["json"]["secret_token"], "current-secret")


class TestScheduledCheck(HealthCase):
    def test_leaves_a_disabled_agent_alone(self):
        # With the agent off, a message that arrives goes nowhere anyway, and
        # a nightly warning about it is pure noise.
        frappe.db.get_single_value.side_effect = lambda doctype, field: False if field == "enabled" else "chat-1"

        scheduled_check()

        self.assertEqual(self.telegram.calls, [])
        self.assertEqual(self.told, [])

    def test_says_nothing_when_everything_works(self):
        scheduled_check()

        self.assertEqual(self.told, [])
        self.assertEqual([c["method"] for c in self.telegram.calls if c["method"] == "setWebhook"], [])

    def test_registers_again_and_says_what_was_wrong(self):
        self.telegram.info = {"ok": True, "result": {"url": "", "pending_update_count": 0}}

        scheduled_check()

        self.assertTrue([c for c in self.telegram.calls if c["method"] == "setWebhook"])
        self.assertEqual(len(self.told), 1)
        self.assertIn("webhook", self.told[0].lower())

    def test_does_not_rotate_the_secret_while_fixing(self):
        # A silent rotation in the middle of the night is one more thing to
        # go wrong, and the secret was not the problem.
        self.telegram.info = {"ok": True, "result": {"url": "", "pending_update_count": 0}}

        scheduled_check()

        sent = [c for c in self.telegram.calls if c["method"] == "setWebhook"][0]
        self.assertEqual(sent["json"]["secret_token"], "current-secret")

    def test_only_warns_about_what_registering_cannot_fix(self):
        self.telegram.info = {
            "ok": True,
            "result": {
                "url": EXPECTED_URL,
                "pending_update_count": 2,
                "last_error_date": 1789000000,
                "last_error_message": "Wrong response from the webhook: 500",
            },
        }

        scheduled_check()

        self.assertEqual([c for c in self.telegram.calls if c["method"] == "setWebhook"], [])
        self.assertEqual(len(self.told), 1)
        self.assertIn("500", self.told[0])

    def test_a_failure_to_tell_you_does_not_break_the_job(self):
        self.telegram.info = {"ok": True, "result": {"url": "", "pending_update_count": 0}}
        health_mod._tell_operator = MagicMock(side_effect=Exception("telegram down"))

        scheduled_check()

        self.assertTrue([c for c in self.telegram.calls if c["method"] == "setWebhook"])


if __name__ == "__main__":
    unittest.main()
