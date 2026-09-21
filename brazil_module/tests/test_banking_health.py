"""Tests for the banking-channel watchman.

Spec: docs/superpowers/specs/2026-09-21-banking-health-design.md

The point of this module is that nobody was told the integration had been off for five months, so
the tests that matter are the ones about *being told*: the state key that decides whether to
interrupt, the day count that must not become an interruption of its own, and the guarantee that
none of this ever writes to the bank.
"""

import datetime
import json
import os
import unittest
from unittest.mock import patch

from brazil_module.tests._payment_fakes import FakeDB, FakeInterClient, install_frappe_mock, patch_frappe

frappe = install_frappe_mock()

import brazil_module.services.banking.banking_health as _bh

NOW = datetime.datetime(2026, 9, 21, 3, 0, 0)
ACCOUNT = "Inter - I8"
SETTINGS = "Banco Inter Settings"
OUR_WEBHOOK = "https://erp.i8.test/api/method/brazil_module.api.webhook_receiver"


def _getdate(value=None):
    if value is None:
        return NOW.date()
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _get_datetime(value=None):
    if value is None:
        return NOW
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    return datetime.datetime.fromisoformat(str(value))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class HealthCase(unittest.TestCase):
    """An enabled integration, one healthy account, fresh jobs and a bank that answers."""

    def setUp(self):
        self.db = FakeDB(singles={SETTINGS: {"enabled": 1}}, now=lambda: NOW)
        self.db.add(
            "Inter Company Account", ACCOUNT, company="Intelligence8", sync_enabled=1,
            certificate_valid=1, certificate_expiry=datetime.date(2027, 1, 1),
            last_statement_sync=NOW - datetime.timedelta(hours=6),
        )
        for method in _bh.WATCHED_JOBS:
            self.db.add(
                "Scheduled Job Type", method, method=method, stopped=0,
                last_execution=NOW - datetime.timedelta(minutes=5),
            )
        self.client = FakeInterClient(self.db)
        self.client.responses["get_webhook"] = {"webhookUrl": OUR_WEBHOOK}
        patch_frappe(self, db=self.db, get_all=self.db.get_all)
        self.at(NOW)
        self._patch(
            _bh, getdate=_getdate, get_datetime=_get_datetime,
            InterAPIClient=lambda account: self.client, site_webhook_url=lambda: OUR_WEBHOOK,
        )

    def _patch(self, module, **attributes):
        for name, value in attributes.items():
            patcher = patch.object(module, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def at(self, moment):
        """Move the clock, keeping fresh whatever the fixture calls fresh.

        Without this, a test that advances time trips ``jobs`` and ``statement_sync`` as a side
        effect and stops saying anything about what it meant to say. The checks that are ABOUT
        staleness set their own timestamps after setUp and do not move the clock.
        """
        self._patch(_bh, now_datetime=lambda: moment)
        for method in _bh.WATCHED_JOBS:
            if method in self.db._working.get("Scheduled Job Type", {}):
                self.db.set_value(
                    "Scheduled Job Type", method, "last_execution", moment - datetime.timedelta(minutes=5)
                )
        if ACCOUNT in self.db._working.get("Inter Company Account", {}):
            self.db.set_value(
                "Inter Company Account", ACCOUNT, "last_statement_sync", moment - datetime.timedelta(hours=6)
            )

    def settings(self):
        return self.db.singles[SETTINGS]

    def problems(self):
        return _bh.check(record=False)["problems"]

    def names_with_problems(self):
        return sorted(item["name"] for item in self.problems())

    def problem_named(self, name):
        found = [item for item in self.problems() if item["name"] == name]
        self.assertEqual(len(found), 1, f"expected exactly one {name} problem, got {found}")
        return found[0]


class TestTheVerdict(HealthCase):
    def test_a_healthy_channel_reports_ok_and_nothing_else(self):
        verdict = _bh.check(record=False)

        self.assertTrue(verdict["healthy"])
        self.assertEqual(verdict["state"], "ok")
        self.assertEqual(verdict["problems"], [])
        self.assertEqual(len(verdict["checks"]), len(_bh.CHECKS))

    def test_the_state_is_the_sorted_names_of_what_is_wrong(self):
        self.settings()["enabled"] = 0
        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_valid", 0)

        verdict = _bh.check(record=False)

        self.assertFalse(verdict["healthy"])
        self.assertEqual(verdict["state"], "certificates,integration_enabled")

    def test_the_state_does_not_move_when_only_the_day_count_does(self):
        """The prose carries "ha N dias"; using it to detect change alerts every morning."""
        self.settings()["enabled"] = 0
        self.settings()["banking_health_since"] = NOW
        first = _bh.check(record=False)
        self.at(NOW + datetime.timedelta(days=40))

        later = _bh.check(record=False)

        self.assertEqual(first["state"], later["state"])
        self.assertNotEqual(first["summary"], later["summary"])

    def test_every_check_answers_with_the_same_shape(self):
        for item in _bh.check(record=False)["checks"]:
            with self.subTest(check=item["name"]):
                self.assertEqual(
                    set(item), {"name", "healthy", "problem", "fixable_by", "skipped"}
                )


class TestIntegrationEnabled(HealthCase):
    def test_the_kill_switch_is_the_news_not_the_reason_for_silence(self):
        self.settings()["enabled"] = 0

        problem = self.problem_named("integration_enabled")

        self.assertIn("DESLIGADA", problem["problem"])
        self.assertTrue(problem["fixable_by"])


class TestAccounts(HealthCase):
    def test_no_account_with_sync_enabled_is_a_problem(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "sync_enabled", 0)

        self.assertIn("accounts", self.names_with_problems())


class TestCertificates(HealthCase):
    def test_a_certificate_expiring_in_exactly_thirty_days_is_a_problem(self):
        self.db.set_value(
            "Inter Company Account", ACCOUNT, "certificate_expiry",
            (NOW + datetime.timedelta(days=_bh.CERT_WARNING_DAYS)).date(),
        )

        self.assertIn("certificates", self.names_with_problems())

    def test_thirty_one_days_is_not(self):
        self.db.set_value(
            "Inter Company Account", ACCOUNT, "certificate_expiry",
            (NOW + datetime.timedelta(days=_bh.CERT_WARNING_DAYS + 1)).date(),
        )

        self.assertEqual(self.names_with_problems(), [])

    def test_an_expired_certificate_is_a_problem(self):
        self.db.set_value(
            "Inter Company Account", ACCOUNT, "certificate_expiry", (NOW - datetime.timedelta(days=1)).date()
        )

        self.assertIn("certificates", self.names_with_problems())

    def test_a_missing_expiry_date_is_a_problem_of_its_own(self):
        """It is only recomputed when someone saves the account, so an empty date says nothing."""
        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_expiry", None)

        self.assertIn("certificates", self.names_with_problems())

    def test_an_invalid_certificate_is_a_problem(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_valid", 0)

        self.assertIn("certificates", self.names_with_problems())


class TestStatementSync(HealthCase):
    def test_a_sync_this_morning_is_fine(self):
        self.assertEqual(self.names_with_problems(), [])

    def test_exactly_two_days_is_stale(self):
        self.db.set_value(
            "Inter Company Account", ACCOUNT, "last_statement_sync",
            NOW - datetime.timedelta(days=_bh.SYNC_STALE_DAYS),
        )

        self.assertIn("statement_sync", self.names_with_problems())

    def test_never_synced_is_stale(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "last_statement_sync", None)

        self.assertIn("statement_sync", self.names_with_problems())


class TestApiErrors(HealthCase):
    def add_calls(self, ok: int, failed: int, hours_ago: float = 1):
        when = NOW - datetime.timedelta(hours=hours_ago)
        for index in range(ok + failed):
            self.db.add(
                "Inter API Log", f"LOG-{hours_ago}-{index}", timestamp=when,
                success=1 if index < ok else 0,
                response_code=200 if index < ok else 500, response_body="{}",
            )

    def test_half_the_calls_failing_is_a_problem(self):
        self.add_calls(ok=2, failed=2)

        self.assertIn("api_errors", self.names_with_problems())

    def test_two_failures_are_below_the_minimum_and_say_nothing(self):
        self.add_calls(ok=0, failed=2)

        self.assertEqual(self.names_with_problems(), [])

    def test_older_than_a_day_does_not_count(self):
        self.add_calls(ok=0, failed=10, hours_ago=25)

        self.assertEqual(self.names_with_problems(), [])

    def test_a_healthy_majority_says_nothing(self):
        self.add_calls(ok=9, failed=1)

        self.assertEqual(self.names_with_problems(), [])


class TestPixLimit(HealthCase):
    def add_rejections(self, count: int, detail: str = "Voce atingiu o limite diario. [PIXP30]"):
        for index in range(count):
            self.db.add(
                "Inter API Log", f"PIX-{index}", timestamp=NOW - datetime.timedelta(hours=2),
                success=0, response_code=422, response_body=json.dumps({"detail": detail}),
            )

    def test_seven_rejections_are_one_problem_not_seven(self):
        self.add_rejections(7)

        problem = self.problem_named("pix_limit")

        self.assertIn("7", problem["problem"])

    def test_another_422_is_not_the_daily_limit(self):
        self.add_rejections(1, detail="chave pix invalida")

        self.assertNotIn("pix_limit", self.names_with_problems())


class TestJobs(HealthCase):
    PAYMENTS = "brazil_module.services.banking.payment_service.scheduled_payment_status_check"

    def test_a_job_three_intervals_late_is_a_problem(self):
        self.db.set_value("Scheduled Job Type", self.PAYMENTS, "last_execution", NOW - datetime.timedelta(hours=4))

        self.assertIn("jobs", self.names_with_problems())

    def test_a_job_merely_late_is_not(self):
        self.db.set_value("Scheduled Job Type", self.PAYMENTS, "last_execution", NOW - datetime.timedelta(hours=2))

        self.assertEqual(self.names_with_problems(), [])

    def test_a_stopped_job_is_a_problem(self):
        self.db.set_value("Scheduled Job Type", self.PAYMENTS, "stopped", 1)

        self.assertIn("jobs", self.names_with_problems())

    def test_jobs_are_watched_even_with_the_integration_off(self):
        """The framework stamps last_execution whatever the method then decides, so a late job
        means the scheduler stopped - which is news at any time."""
        self.settings()["enabled"] = 0
        self.db.set_value("Scheduled Job Type", self.PAYMENTS, "last_execution", NOW - datetime.timedelta(hours=4))

        self.assertIn("jobs", self.names_with_problems())


class TestWebhook(HealthCase):
    def test_an_address_for_another_site_is_a_problem(self):
        self.client.responses["get_webhook"] = {"webhookUrl": "https://old.i8.test/api/method/x"}

        self.assertIn("webhook", self.names_with_problems())

    def test_nothing_registered_is_a_problem(self):
        self.client.responses["get_webhook"] = {}

        self.assertIn("webhook", self.names_with_problems())

    def test_a_bank_that_cannot_be_asked_says_so_instead_of_accusing(self):
        self.client.responses["get_webhook"] = RuntimeError("401 invalid_client")

        problem = self.problem_named("webhook")

        self.assertIn("nao foi possivel", problem["problem"].lower())


class TestSkipping(HealthCase):
    def test_with_the_integration_off_nothing_reaches_the_bank(self):
        self.settings()["enabled"] = 0

        verdict = _bh.check(record=False)

        self.assertEqual(self.client.calls, [])
        skipped = {item["name"] for item in verdict["checks"] if item["skipped"]}
        self.assertEqual(skipped, set(_bh.NEEDS_INTEGRATION))
        self.assertEqual(verdict["state"], "integration_enabled")

    def test_a_check_that_raises_becomes_a_problem_and_the_others_still_run(self):
        def _certificates():
            raise RuntimeError("boom")

        self._patch(
            _bh, CHECKS=tuple(_certificates if one.__name__ == "_certificates" else one for one in _bh.CHECKS)
        )

        verdict = _bh.check(record=False)

        self.assertIn("certificates", verdict["state"])
        self.assertEqual(len(verdict["checks"]), len(_bh.CHECKS))
        self.assertIn("boom", self.problem_named("certificates")["problem"])


class TestWhenItInterrupts(HealthCase):
    """Five months of silence on one side, 150 identical messages on the other. Neither."""

    def setUp(self):
        super().setUp()
        self.alerts = []
        self._patch(
            _bh, alert_operator=lambda subject, message, order_name=None: self.alerts.append((subject, message))
        )

    def run_at(self, moment):
        self.at(moment)
        _bh.scheduled_check()

    def test_a_healthy_channel_says_nothing(self):
        _bh.scheduled_check()

        self.assertEqual(self.alerts, [])

    def test_breaking_alerts_once_and_the_next_day_does_not(self):
        self.settings()["enabled"] = 0

        self.run_at(NOW)
        self.assertEqual(len(self.alerts), 1)

        self.run_at(NOW + datetime.timedelta(days=1))
        self.assertEqual(len(self.alerts), 1, "the same problem interrupted twice in two days")

    def test_a_week_later_it_reminds(self):
        self.settings()["enabled"] = 0

        self.run_at(NOW)
        self.run_at(NOW + datetime.timedelta(days=_bh.REMINDER_DAYS))

        self.assertEqual(len(self.alerts), 2)

    def test_recovering_is_worth_one_message_and_then_silence(self):
        self.settings()["enabled"] = 0
        self.run_at(NOW)

        self.settings()["enabled"] = 1
        self.run_at(NOW + datetime.timedelta(days=1))
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("voltou", self.alerts[-1][1].lower())

        self.run_at(NOW + datetime.timedelta(days=2))
        self.assertEqual(len(self.alerts), 2)

    def test_a_new_problem_on_top_of_an_old_one_interrupts(self):
        self.settings()["enabled"] = 0
        self.run_at(NOW)

        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_valid", 0)
        self.run_at(NOW + datetime.timedelta(days=1))

        self.assertEqual(len(self.alerts), 2)

    def test_the_problems_and_what_to_do_are_in_the_message(self):
        self.settings()["enabled"] = 0

        self.run_at(NOW)

        subject, message = self.alerts[0]
        self.assertIn("Banco Inter", subject)
        self.assertIn("DESLIGADA", message)
        self.assertIn(_bh.RECONNECT, message)

    def test_checked_on_moves_every_run_and_since_only_on_a_change(self):
        self.settings()["enabled"] = 0
        self.run_at(NOW)
        started = self.settings()[_bh.SINCE_FIELD]

        later = NOW + datetime.timedelta(days=1)
        self.run_at(later)
        self.assertEqual(self.settings()[_bh.SINCE_FIELD], started)
        self.assertEqual(
            self.settings()[_bh.CHECKED_FIELD], later,
            "the last-checked date is the honest 'it worked until here' and moves every run",
        )

        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_valid", 0)
        self.run_at(NOW + datetime.timedelta(days=2))
        self.assertNotEqual(self.settings()[_bh.SINCE_FIELD], started)

    def test_the_day_count_comes_from_since(self):
        self.settings()["enabled"] = 0
        self.run_at(NOW)

        self.run_at(NOW + datetime.timedelta(days=152))

        self.assertIn("152", self.settings()[_bh.STATUS_FIELD])

    def test_it_does_not_fall_silent_while_the_integration_is_off(self):
        """Its Telegram counterpart does, for good reasons that do not hold here: OFF is the news."""
        self.settings()["enabled"] = 0

        self.run_at(NOW)

        self.assertEqual(len(self.alerts), 1)


class TestTheDailyJob(unittest.TestCase):
    HOOKS_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(_bh.__file__)))), "hooks.py"
    )

    def test_the_watchman_runs_daily(self):
        source = _read(self.HOOKS_PATH)
        daily = source[source.index('"daily": ['):]
        daily = daily[:daily.index("]")]
        self.assertIn("banking_health.scheduled_check", daily)


class TestTheSettingsContract(unittest.TestCase):
    JSON_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(_bh.__file__)))),
        "bancos", "doctype", "banco_inter_settings", "banco_inter_settings.json",
    )

    def test_the_five_verdict_fields_exist_and_are_read_only(self):
        doctype = json.loads(_read(self.JSON_PATH))
        fields = {field["fieldname"]: field for field in doctype["fields"]}
        for name, fieldtype in (
            ("banking_health_state", "Data"),
            ("banking_health_status", "Small Text"),
            ("banking_health_checked_on", "Datetime"),
            ("banking_health_since", "Datetime"),
            ("banking_health_alerted_on", "Datetime"),
        ):
            with self.subTest(name=name):
                self.assertIn(name, fields)
                self.assertEqual(fields[name]["fieldtype"], fieldtype)
                self.assertEqual(fields[name].get("read_only"), 1)
                self.assertIn(name, doctype["field_order"])


if __name__ == "__main__":
    unittest.main()
