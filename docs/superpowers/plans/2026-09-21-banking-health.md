# Banking Health Watchman Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell the owner when the channel to Banco Inter stops working, instead of reporting normality from a dead line.

**Architecture:** A read-only module of eight independent checks. `check()` runs them all and returns one verdict plus a stable state key; `scheduled_check()` runs daily and decides whether to interrupt (state change, or a weekly reminder); the verdict is recorded on `Banco Inter Settings` and read back by the daily briefing and by a button on the form. It never writes to the bank.

**Tech Stack:** Python 3.12, Frappe/ERPNext v15 (mocked in tests via `sys.modules["frappe"]`), pytest (`.venv/bin/python -m pytest`), node (for the JS behaviour harness that already exists).

**Spec:** `docs/superpowers/specs/2026-09-21-banking-health-design.md` — read it completely before your task. D1–D4 and section 3 are acceptance criteria, not background.

## Global Constraints

- Package root is `brazil_module/`. Branch is `fix/inter-payment-safety`.
- Tests: `.venv/bin/python -m pytest <path> -q` (the system python has no pytest). `ruff` is NOT installed and nothing may be installed: use `.venv/bin/python -m py_compile`.
- **D1: never write to Banco Inter.** The only bank call in this feature is `InterAPIClient.get_webhook()`. Task 4 adds a static tripwire that enforces it.
- Nothing in this feature may raise: `check()` answers a button as well as a job, and every briefing section must survive its own failure.
- Frappe writes go through `frappe.db.set_single_value` wrapped so a failed write never turns a check into a failure.
- Reuse, do not rebuild: `payment_alerts.alert_operator(subject, message, order_name=None)` is the outbound channel; `brazil_module/tests/_payment_fakes.py` (`FakeDB`, `FakeInterClient`, `install_frappe_mock`, `patch_frappe`) is the test kit.
- Operator-facing strings in this feature are Portuguese without accents, matching the surrounding briefing and Telegram code (`"Comunicacao bancaria"`, `"DESLIGADA ha 152 dias"`).
- Do not commit; the orchestrator owns git. Work in the current tree.

## File Structure

| File | Task | Responsibility |
|---|---|---|
| `brazil_module/bancos/doctype/banco_inter_settings/banco_inter_settings.json` | T1 | the five verdict fields |
| `brazil_module/services/banking/banking_health.py` (new) | T1, T2 | the eight checks, `check()`, `status()` (T1); `scheduled_check()` and the noise policy (T2) |
| `brazil_module/tests/test_banking_health.py` (new) | T1, T2 | |
| `brazil_module/hooks.py` | T2 | the daily entry |
| `brazil_module/services/intelligence/recurring/daily_briefing.py` | T3 | the new section; the balance and reconciliation fixes |
| `brazil_module/tests/test_banking_health_briefing.py` (new) | T3 | |
| `brazil_module/api/__init__.py`, `banco_inter_settings.js`, `brazil_module/tests/test_payment_invariants.py` | T4 | endpoint, button, intro fix, read-only tripwire |

Order: T1 → T2 → T3 → T4. T3 and T4 touch no file of each other's and may run in parallel once T2 is in.

---

### Task 1: The eight checks and the verdict

**Files:**
- Create: `brazil_module/services/banking/banking_health.py`
- Create: `brazil_module/tests/test_banking_health.py`
- Modify: `brazil_module/bancos/doctype/banco_inter_settings/banco_inter_settings.json`

**Interfaces — Produces:**

```python
SETTINGS = "Banco Inter Settings"
ACCOUNT = "Inter Company Account"
API_LOG = "Inter API Log"
CERT_WARNING_DAYS = 30
SYNC_STALE_DAYS = 2
MIN_CALLS = 3
ERROR_RATE = 0.5
JOBS_LATE_FACTOR = 3
WATCHED_JOBS = {   # method path -> expected interval in hours
    "brazil_module.services.banking.payment_service.scheduled_payment_status_check": 1,
    "brazil_module.services.banking.statement_sync.scheduled_statement_sync": 6,
    "brazil_module.services.banking.boleto_service.scheduled_boleto_status_check": 0.5,
    "brazil_module.services.banking.pix_service.scheduled_pix_status_check": 0.25,
}
NEEDS_INTEGRATION = ("statement_sync", "api_errors", "pix_limit", "webhook")

def check(record: bool = True) -> dict
    # {"healthy": bool, "state": str, "summary": str,
    #  "problems": [check dict], "checks": [check dict]}
def status() -> dict
    # {"state": str, "summary": str, "checked_on": datetime|None, "since": datetime|None}
```

A check dict is `{"name": str, "healthy": bool, "problem": str, "fixable_by": str, "skipped": bool}`.

New fields on `Banco Inter Settings`, all `read_only: 1`, in a new section `Saude da conexao`:
`banking_health_state` (Data), `banking_health_status` (Small Text), `banking_health_checked_on` (Datetime), `banking_health_since` (Datetime), `banking_health_alerted_on` (Datetime).

- [ ] **Step 1: Write the failing test for the state key**

In `brazil_module/tests/test_banking_health.py`:

```python
"""Tests for the banking-channel watchman (spec 2026-09-21-banking-health-design.md)."""

import datetime
import unittest
from unittest.mock import MagicMock

from brazil_module.tests._payment_fakes import FakeDB, FakeInterClient, install_frappe_mock, patch_frappe

frappe = install_frappe_mock()

import brazil_module.services.banking.banking_health as _bh

NOW = datetime.datetime(2026, 9, 21, 3, 0, 0)
ACCOUNT = "Inter - I8"


class HealthCase(unittest.TestCase):
    """An enabled integration with one healthy account and a bank that answers."""

    def setUp(self):
        self.db = FakeDB(singles={"Banco Inter Settings": {"enabled": 1}}, now=lambda: NOW)
        self.db.add(
            "Inter Company Account", ACCOUNT, company="Intelligence8", sync_enabled=1,
            certificate_valid=1, certificate_expiry=datetime.date(2027, 1, 1),
            last_statement_sync=NOW - datetime.timedelta(hours=6),
        )
        for method, hours in _bh.WATCHED_JOBS.items():
            self.db.add(
                "Scheduled Job Type", method, method=method, stopped=0,
                last_execution=NOW - datetime.timedelta(minutes=5),
            )
        self.client = FakeInterClient(self.db)
        self.client.responses["get_webhook"] = {"webhookUrl": "https://erp.i8.test/api/method/brazil_module.api.webhook_receiver"}
        patch_frappe(self, db=self.db, get_all=self.db.get_all)
        self._patch(_bh, now_datetime=lambda: NOW, getdate=_getdate, InterAPIClient=lambda account: self.client,
                    site_webhook_url=lambda: "https://erp.i8.test/api/method/brazil_module.api.webhook_receiver")

    def _patch(self, module, **attributes):
        from unittest.mock import patch
        for name, value in attributes.items():
            patcher = patch.object(module, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def names_with_problems(self):
        return sorted(item["name"] for item in _bh.check(record=False)["problems"])


def _getdate(value=None):
    if value is None:
        return NOW.date()
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


class TestTheVerdict(HealthCase):
    def test_a_healthy_channel_reports_ok_and_nothing_else(self):
        verdict = _bh.check(record=False)

        self.assertTrue(verdict["healthy"])
        self.assertEqual(verdict["state"], "ok")
        self.assertEqual(verdict["problems"], [])
        self.assertEqual(len(verdict["checks"]), len(_bh.CHECKS))

    def test_the_state_is_the_sorted_names_of_what_is_wrong(self):
        self.db.singles["Banco Inter Settings"]["enabled"] = 0
        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_valid", 0)

        verdict = _bh.check(record=False)

        self.assertFalse(verdict["healthy"])
        self.assertEqual(verdict["state"], "certificates,integration_enabled")

    def test_the_state_does_not_move_when_only_the_day_count_does(self):
        """The prose carries "ha N dias"; using it for change detection alerts every morning."""
        self.db.singles["Banco Inter Settings"]["enabled"] = 0
        first = _bh.check(record=False)
        self._patch(_bh, now_datetime=lambda: NOW + datetime.timedelta(days=40))

        later = _bh.check(record=False)

        self.assertEqual(first["state"], later["state"])
        self.assertNotEqual(first["summary"], later["summary"])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health.py -q`
Expected: FAIL — `ModuleNotFoundError: brazil_module.services.banking.banking_health`.

- [ ] **Step 3: Write the module skeleton and the first two checks**

Create `brazil_module/services/banking/banking_health.py` with the module docstring, the constants of the Interfaces block, `site_webhook_url()`, `_integration_enabled()`, `_accounts()`, `check()`, `status()` and the recording helpers. `check()` shape:

```python
def check(record: bool = True) -> dict:
    """Ask every question about the channel. Never raises: this answers a button too."""
    enabled = _is_enabled()
    results = [_run(one, enabled) for one in CHECKS]
    problems = [item for item in results if not item["healthy"] and not item["skipped"]]
    verdict = {
        "healthy": not problems,
        "state": "ok" if not problems else ",".join(sorted(item["name"] for item in problems)),
        "summary": _summary(problems),
        "problems": problems,
        "checks": results,
    }
    if record:
        _record(verdict)
    return verdict


def _run(one, enabled: bool) -> dict:
    name = one.__name__.lstrip("_")
    if name in NEEDS_INTEGRATION and not enabled:
        return _result(name, True, skipped=True)
    try:
        return one()
    except Exception as error:
        return _result(name, False, f"A verificacao {name} falhou: {error}")
```

- [ ] **Step 4: Run the three tests and see them pass**

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing tests for the remaining six checks**

Add to `test_banking_health.py`, one class per check. Each has a healthy case, an unhealthy case and the edge the spec names:

```python
class TestCertificates(HealthCase):
    def test_a_certificate_expiring_in_exactly_thirty_days_is_a_problem(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_expiry",
                          (NOW + datetime.timedelta(days=30)).date())
        self.assertIn("certificates", self.names_with_problems())

    def test_thirty_one_days_is_not(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_expiry",
                          (NOW + datetime.timedelta(days=31)).date())
        self.assertEqual(self.names_with_problems(), [])

    def test_a_missing_expiry_date_is_a_problem_of_its_own(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_expiry", None)
        self.assertIn("certificates", self.names_with_problems())

    def test_an_invalid_certificate_is_a_problem(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_valid", 0)
        self.assertIn("certificates", self.names_with_problems())


class TestStatementSync(HealthCase):
    def test_exactly_two_days_is_stale(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "last_statement_sync",
                          NOW - datetime.timedelta(days=2))
        self.assertIn("statement_sync", self.names_with_problems())

    def test_a_sync_this_morning_is_fine(self):
        self.assertEqual(self.names_with_problems(), [])

    def test_never_synced_is_stale(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "last_statement_sync", None)
        self.assertIn("statement_sync", self.names_with_problems())


class TestApiErrors(HealthCase):
    def add_calls(self, ok: int, failed: int, hours_ago: float = 1):
        when = NOW - datetime.timedelta(hours=hours_ago)
        for index in range(ok + failed):
            self.db.add("Inter API Log", f"LOG-{hours_ago}-{index}", timestamp=when,
                        success=1 if index < ok else 0, response_code=200 if index < ok else 500,
                        response_body="{}")

    def test_half_the_calls_failing_is_a_problem(self):
        self.add_calls(ok=2, failed=2)
        self.assertIn("api_errors", self.names_with_problems())

    def test_two_failures_are_below_the_minimum_and_say_nothing(self):
        self.add_calls(ok=0, failed=2)
        self.assertEqual(self.names_with_problems(), [])

    def test_older_than_a_day_does_not_count(self):
        self.add_calls(ok=0, failed=10, hours_ago=25)
        self.assertEqual(self.names_with_problems(), [])


class TestPixLimit(HealthCase):
    def test_seven_rejections_are_one_problem_not_seven(self):
        for index in range(7):
            self.db.add("Inter API Log", f"PIX-{index}", timestamp=NOW - datetime.timedelta(hours=2),
                        success=0, response_code=422,
                        response_body='{"title": "Limite excedido", "detail": "... [PIXP30]"}')

        problems = _bh.check(record=False)["problems"]
        limit = [item for item in problems if item["name"] == "pix_limit"]
        self.assertEqual(len(limit), 1)
        self.assertIn("7", limit[0]["problem"])

    def test_another_422_is_not_the_daily_limit(self):
        self.db.add("Inter API Log", "OTHER", timestamp=NOW, success=0, response_code=422,
                    response_body='{"detail": "chave pix invalida"}')
        self.assertNotIn("pix_limit", self.names_with_problems())


class TestJobs(HealthCase):
    def test_a_job_three_intervals_late_is_a_problem(self):
        method = "brazil_module.services.banking.payment_service.scheduled_payment_status_check"
        self.db.set_value("Scheduled Job Type", method, "last_execution", NOW - datetime.timedelta(hours=4))
        self.assertIn("jobs", self.names_with_problems())

    def test_a_stopped_job_is_a_problem(self):
        method = "brazil_module.services.banking.statement_sync.scheduled_statement_sync"
        self.db.set_value("Scheduled Job Type", method, "stopped", 1)
        self.assertIn("jobs", self.names_with_problems())

    def test_jobs_are_watched_even_with_the_integration_off(self):
        """The framework stamps last_execution whatever the method decides: a late job means the
        scheduler stopped, which is news at any time."""
        self.db.singles["Banco Inter Settings"]["enabled"] = 0
        method = "brazil_module.services.banking.pix_service.scheduled_pix_status_check"
        self.db.set_value("Scheduled Job Type", method, "last_execution", NOW - datetime.timedelta(hours=4))
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
        problem = [p for p in _bh.check(record=False)["problems"] if p["name"] == "webhook"][0]
        self.assertIn("nao foi possivel", problem["problem"].lower())


class TestSkipping(HealthCase):
    def test_with_the_integration_off_nothing_reaches_the_bank(self):
        self.db.singles["Banco Inter Settings"]["enabled"] = 0

        verdict = _bh.check(record=False)

        self.assertEqual(self.client.calls, [])
        skipped = {item["name"] for item in verdict["checks"] if item["skipped"]}
        self.assertEqual(skipped, set(_bh.NEEDS_INTEGRATION))
        self.assertEqual(verdict["state"], "integration_enabled")

    def test_a_check_that_raises_becomes_a_problem_and_the_others_still_run(self):
        def explode():
            raise RuntimeError("boom")
        explode.__name__ = "_certificates"
        self._patch(_bh, CHECKS=tuple(explode if one.__name__ == "_certificates" else one for one in _bh.CHECKS))

        verdict = _bh.check(record=False)

        self.assertIn("certificates", verdict["state"])
        self.assertEqual(len(verdict["checks"]), len(_bh.CHECKS))
```

- [ ] **Step 6: Run them and watch them fail**

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health.py -q`
Expected: FAIL — the six checks do not exist yet.

- [ ] **Step 7: Implement the six remaining checks**

Each returns `_result(name, healthy, problem="", fixable_by="")`. `_webhook()` builds the client from the first enabled account and compares `get_webhook().get("webhookUrl")` with `site_webhook_url()`; any exception from the bank becomes `"Nao foi possivel perguntar ao banco pelo webhook: {error}"`. `_pix_limit()` counts `Inter API Log` rows of the last 24h with `response_code == 422` and `"PIXP30" in (response_body or "")`, and reports the count in one sentence.

- [ ] **Step 8: Run the file and see it pass**

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health.py -q`
Expected: PASS.

- [ ] **Step 9: Add the five fields to the DocType JSON**

Add to `banco_inter_settings.json`, in `fields` and in `field_order`, after the last existing field, preceded by a `Section Break` with `label: "Saude da conexao"`. Every one of the five carries `"read_only": 1`.

- [ ] **Step 10: Write and run the JSON contract test**

```python
class TestTheSettingsContract(unittest.TestCase):
    def test_the_five_verdict_fields_exist_and_are_read_only(self):
        import json, os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(_bh.__file__))),
                            "..", "bancos", "doctype", "banco_inter_settings", "banco_inter_settings.json")
        doctype = json.load(open(os.path.normpath(path)))
        fields = {f["fieldname"]: f for f in doctype["fields"]}
        for name, fieldtype in (
            ("banking_health_state", "Data"), ("banking_health_status", "Small Text"),
            ("banking_health_checked_on", "Datetime"), ("banking_health_since", "Datetime"),
            ("banking_health_alerted_on", "Datetime"),
        ):
            with self.subTest(name=name):
                self.assertIn(name, fields)
                self.assertEqual(fields[name]["fieldtype"], fieldtype)
                self.assertEqual(fields[name].get("read_only"), 1)
                self.assertIn(name, doctype["field_order"])
```

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health.py -q` — PASS.

- [ ] **Step 11: Run the whole suite**

Run: `.venv/bin/python -m pytest brazil_module/tests -q`
Expected: the previous count plus the new tests, 0 failed.

---

### Task 2: When it interrupts

**Files:**
- Modify: `brazil_module/services/banking/banking_health.py`
- Modify: `brazil_module/tests/test_banking_health.py`
- Modify: `brazil_module/hooks.py` (the `"daily"` list only)

**Interfaces — Consumes:** `check()`, `status()` from Task 1; `payment_alerts.alert_operator(subject, message, order_name=None)`.
**Interfaces — Produces:** `scheduled_check() -> None`, `REMINDER_DAYS = 7`.

- [ ] **Step 1: Write the failing tests for the noise policy**

```python
class TestWhenItInterrupts(HealthCase):
    def setUp(self):
        super().setUp()
        self.alerts = []
        self._patch(_bh, alert_operator=lambda subject, message, order_name=None:
                    self.alerts.append((subject, message)))

    def settings(self):
        return self.db.singles["Banco Inter Settings"]

    def run_at(self, moment):
        self._patch(_bh, now_datetime=lambda: moment)
        _bh.scheduled_check()

    def test_a_healthy_channel_says_nothing(self):
        _bh.scheduled_check()
        self.assertEqual(self.alerts, [])

    def test_breaking_alerts_once_and_the_next_day_does_not(self):
        self.settings()["enabled"] = 0

        self.run_at(NOW)
        self.assertEqual(len(self.alerts), 1)

        self.run_at(NOW + datetime.timedelta(days=1))
        self.assertEqual(len(self.alerts), 1, "the same problem alerted twice in two days")

    def test_a_week_later_it_reminds(self):
        self.settings()["enabled"] = 0
        self.run_at(NOW)
        self.run_at(NOW + datetime.timedelta(days=7))
        self.assertEqual(len(self.alerts), 2)

    def test_recovering_is_worth_one_message(self):
        self.settings()["enabled"] = 0
        self.run_at(NOW)
        self.settings()["enabled"] = 1
        self.run_at(NOW + datetime.timedelta(days=1))

        self.assertEqual(len(self.alerts), 2)
        self.assertIn("voltou", self.alerts[-1][1].lower())

    def test_a_new_problem_on_top_of_an_old_one_alerts(self):
        self.settings()["enabled"] = 0
        self.run_at(NOW)
        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_valid", 0)
        self.run_at(NOW + datetime.timedelta(days=1))
        self.assertEqual(len(self.alerts), 2)

    def test_since_is_kept_while_the_state_holds_and_moves_when_it_changes(self):
        self.settings()["enabled"] = 0
        self.run_at(NOW)
        started = self.settings()["banking_health_since"]

        self.run_at(NOW + datetime.timedelta(days=1))
        self.assertEqual(self.settings()["banking_health_since"], started)
        self.assertEqual(self.settings()["banking_health_checked_on"], NOW + datetime.timedelta(days=1),
                         "the last-checked date is the honest 'it worked until here' and moves every run")

        self.db.set_value("Inter Company Account", ACCOUNT, "certificate_valid", 0)
        self.run_at(NOW + datetime.timedelta(days=2))
        self.assertNotEqual(self.settings()["banking_health_since"], started)

    def test_the_day_count_comes_from_since(self):
        self.settings()["enabled"] = 0
        self.run_at(NOW)
        self.run_at(NOW + datetime.timedelta(days=152))
        self.assertIn("152", self.settings()["banking_health_status"])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health.py -k WhenItInterrupts -q`
Expected: FAIL — `scheduled_check` does not exist.

- [ ] **Step 3: Implement `scheduled_check` and the recording rules**

```python
REMINDER_DAYS = 7


def scheduled_check() -> None:
    """Daily. Say it when the state changes, and once a week while it stays wrong."""
    previous = status()
    verdict = check(record=True)
    if verdict["state"] == previous.get("state") and verdict["healthy"]:
        return
    if verdict["state"] != previous.get("state") or _reminder_is_due(previous):
        _interrupt(verdict)
```

`_record(verdict)` writes `state`, `status` and `checked_on` on every run, and `since` only when `state` differs from what is stored. `_interrupt` composes the subject and body, calls `alert_operator(...)` and stamps `banking_health_alerted_on`. A recovery (`state == "ok"` after something else) sends `"A comunicacao com o Banco Inter voltou ao normal."`.

- [ ] **Step 4: Run them and see them pass**

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health.py -q` — PASS.

- [ ] **Step 5: Register the daily job**

In `brazil_module/hooks.py`, inside `scheduler_events["daily"]`, after the `telegram_health` line:

```python
        # Nobody said the banking integration had been off for five months; this is what asks.
        "brazil_module.services.banking.banking_health.scheduled_check",
```

- [ ] **Step 6: Test the registration and run the suite**

```python
class TestTheDailyJob(unittest.TestCase):
    def test_the_watchman_runs_daily(self):
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(_bh.__file__))), "..", "hooks.py")
        source = open(os.path.normpath(path)).read()
        daily = source[source.index('"daily": ['):]
        daily = daily[:daily.index("]")]
        self.assertIn("banking_health.scheduled_check", daily)
```

Run: `.venv/bin/python -m pytest brazil_module/tests -q` — 0 failed.

---

### Task 3: What the briefing says

**Files:**
- Modify: `brazil_module/services/intelligence/recurring/daily_briefing.py` (`section_funcs` at :132, `_bank_balance_section` at :164, `_reconciliation_status_section` at :602, plus the new section)
- Create: `brazil_module/tests/test_banking_health_briefing.py`

**Interfaces — Consumes:** `banking_health.status() -> {"state", "summary", "checked_on", "since"}`.
**Interfaces — Produces:** `_banking_health_section() -> str`.

- [ ] **Step 1: Write the failing tests**

In `brazil_module/tests/test_banking_health_briefing.py`, covering all three changes:

```python
class TestBankingHealthSection(BriefingCase):
    def test_a_healthy_channel_adds_nothing(self):
        self.recorded(state="ok", summary="OK")
        self.assertEqual(_db_mod._banking_health_section(), "")

    def test_a_problem_is_named_with_its_age_and_when_it_was_checked(self):
        self.recorded(state="integration_enabled", summary="DESLIGADA ha 152 dias", checked_on=NOW)
        section = _db_mod._banking_health_section()
        self.assertIn("Comunicacao bancaria", section)
        self.assertIn("152", section)
        self.assertIn("verificado", section.lower())

    def test_a_failure_is_reported_inside_the_section(self):
        """A section that vanishes reads as 'nothing to worry about'."""
        self._patch(_db_mod, banking_health_status=_raise)
        self.assertIn("Comunicacao bancaria", _db_mod._banking_health_section())

    def test_it_comes_before_the_balance(self):
        source = _read(BRIEFING_PATH)
        block = source[source.index("section_funcs = ["):source.index("if is_monday:")]
        self.assertLess(block.index("_banking_health_section"), block.index("_bank_balance_section"))


class TestTheBalanceStopsLying(BriefingCase):
    def test_a_balance_from_today_prints_without_an_age(self):
        self.account(balance=12345.67, balance_date=TODAY)
        line = _db_mod._bank_balance_section()
        self.assertIn("12.345,67", line)
        self.assertNotIn("ha ", line)

    def test_an_old_balance_says_how_old_it_is(self):
        self.account(balance=12345.67, balance_date=TODAY - datetime.timedelta(days=152))
        line = _db_mod._bank_balance_section()
        self.assertIn("152", line)


class TestReconciliationStopsLying(BriefingCase):
    def test_no_recent_statement_means_it_does_not_say_em_dia(self):
        self.transactions(total=10, unreconciled=0)
        self.account(last_statement_sync=NOW - datetime.timedelta(days=152))
        section = _db_mod._reconciliation_status_section()
        self.assertNotIn("Em dia", section)
        self.assertIn("152", section)

    def test_with_a_fresh_statement_em_dia_is_the_truth_again(self):
        self.transactions(total=10, unreconciled=0)
        self.account(last_statement_sync=NOW - datetime.timedelta(hours=6))
        self.assertIn("Em dia", _db_mod._reconciliation_status_section())
```

`BriefingCase` builds a `FakeDB` and patches `daily_briefing`'s frappe, mirroring the setup in `brazil_module/tests/test_daily_briefing_payments.py`; `_raise` is a function that raises `RuntimeError("boom")`.

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health_briefing.py -q`
Expected: FAIL — `_banking_health_section` does not exist; the balance and reconciliation assertions fail against the current text.

- [ ] **Step 3: Implement the section and the two fixes**

`_banking_health_section()` reads `banking_health.status()`, returns `""` when `state == "ok"`, and otherwise prints the title, one line per problem sentence, and `(verificado <quando>)`. Its `except` returns the title plus `"Nao foi possivel ler a saude da conexao (ver Error Log)"`.
`_bank_balance_section` appends `, ha N dias` when `balance_date` is not today.
`_reconciliation_status_section` only prints `"Em dia (100% conciliado)"` when the newest `last_statement_sync` is within `SYNC_STALE_DAYS`; otherwise it says there is no new statement and names the date of the last one.
Insert `_banking_health_section` into `section_funcs` immediately after the header lambda.

- [ ] **Step 4: Run and see them pass**

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health_briefing.py brazil_module/tests/test_daily_briefing_payments.py -q` — PASS.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest brazil_module/tests -q` — 0 failed.

---

### Task 4: The button, and the guarantee that it only reads

**Files:**
- Modify: `brazil_module/api/__init__.py` (add one endpoint next to `i8_check_telegram_webhook`)
- Modify: `brazil_module/bancos/doctype/banco_inter_settings/banco_inter_settings.js`
- Modify: `brazil_module/tests/test_payment_invariants.py`
- Modify: `brazil_module/tests/test_banking_health.py`

**Interfaces — Consumes:** `banking_health.check()`.
**Interfaces — Produces:** `api.check_banking_health() -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
class TestTheEndpoint(HealthCase):
    def test_it_returns_the_verdict(self):
        from brazil_module import api
        self.assertEqual(api.check_banking_health()["state"], _bh.check(record=False)["state"])


class TestTheModuleOnlyReads(unittest.TestCase):
    """D1: no unattended job may ever write to Banco Inter again."""

    WRITES = ("send_pix", "pay_barcode", "send_ted", "register_webhook", "delete_webhook",
              "create_boleto", "cancel_boleto", "create_pix_charge", "create_pix_charge_with_due_date")

    def test_it_names_no_write_method_of_the_client(self):
        source = _read(_bh.__file__)
        for method in self.WRITES:
            with self.subTest(method=method):
                self.assertNotIn(f".{method}(", source)

    def test_the_only_doctype_it_writes_is_the_settings(self):
        source = _read(_bh.__file__)
        self.assertNotIn("set_value(", source.replace("set_single_value(", ""))
```

Add the two `TestTheModuleOnlyReads` tests to `test_payment_invariants.py` instead if that file is where the other repo-wide tripwires live; keep the endpoint test in `test_banking_health.py`.

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/bin/python -m pytest brazil_module/tests/test_banking_health.py brazil_module/tests/test_payment_invariants.py -q`
Expected: FAIL — the endpoint does not exist.

- [ ] **Step 3: Add the endpoint**

In `brazil_module/api/__init__.py`, beside the Telegram pair:

```python
@frappe.whitelist()
def check_banking_health() -> dict:
    """Ask whether the channel to Banco Inter is working. Reads only."""
    from brazil_module.services.banking.banking_health import check

    return check()
```

- [ ] **Step 4: Fix the form and add the button**

Replace the body of `banco_inter_settings.js`'s `refresh` so the two intros do not overwrite each other (`set_intro` replaces, it does not accumulate): build a list of notices, prepend the recorded health verdict when it is not `ok`, and call `set_intro` once with them joined. Add a *Verificar conexao* button calling `brazil_module.api.check_banking_health`, freezing with `__("Perguntando ao banco...")`, showing `msgprint` with one line per problem and `frm.reload_doc()` after — the same shape as *Test Telegram Connection* in `i8_agent_settings.js:23`.

- [ ] **Step 5: Run everything**

Run: `.venv/bin/python -m pytest brazil_module/tests -q` — 0 failed.
Run: `node --check brazil_module/bancos/doctype/banco_inter_settings/banco_inter_settings.js`
Run: `.venv/bin/python -m py_compile brazil_module/services/banking/banking_health.py brazil_module/api/__init__.py`

- [ ] **Step 6: Mutation pass**

Copy `brazil_module`, `pyproject.toml`, `CLAUDE.md` and `docs` to a scratch directory (all four — without `CLAUDE.md` and `docs` four unrelated tests fail and mask the result). Apply one at a time and confirm each kills at least one test: change detection uses `summary` instead of `state`; `_record` writes `since` on every run; `REMINDER_DAYS` becomes 700; `NEEDS_INTEGRATION` becomes empty; `_pix_limit` returns one problem per row; `MIN_CALLS` becomes 0; the reconciliation section ignores the statement date. Report any survivor and add the test that kills it.

---

## Self-Review

- **Spec coverage:** §4 module and checks → T1; §4 "when it interrupts" and the five fields → T1 (fields) + T2 (policy); §5 briefing and the two corrected sections → T3; §5 form and endpoint → T4; §6 testing → distributed, with the mutation pass in T4 step 6; §3 limits are documented, not implemented, by design; §7 is out of scope and has no task.
- **Placeholders:** none — every step carries its test code or the exact edit.
- **Type consistency:** `check()`, `status()`, `scheduled_check()`, `site_webhook_url()`, the check-dict keys (`name`, `healthy`, `problem`, `fixable_by`, `skipped`) and the five field names are spelled the same in every task and match the spec.
