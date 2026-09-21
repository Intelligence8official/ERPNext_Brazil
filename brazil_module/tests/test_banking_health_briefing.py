"""What the briefing says about the banking channel (spec 2026-09-21, section 5).

Two of these tests are about a section that did not exist. The other two are about sections that
did, and that spent five months reporting normality produced by a dead line: an April balance
printed as today's, and "Em dia (100% conciliado)" counted over transactions nobody had refreshed.
"""

import datetime
import unittest
from unittest.mock import MagicMock

from brazil_module.tests._payment_fakes import FakeDB, install_frappe_mock

frappe = install_frappe_mock()

import brazil_module.services.banking.banking_health as _bh
import brazil_module.services.intelligence.recurring.daily_briefing as _db_mod

NOW = datetime.datetime(2026, 9, 21, 8, 0, 0)
TODAY = NOW.date()
ACCOUNT = "Inter - I8"
SETTINGS = "Banco Inter Settings"
_MISSING = object()


def _shadow_frappe(test_case, module, **attributes):
    namespace = module.frappe.__dict__
    for attribute, value in attributes.items():
        previous = namespace.get(attribute, _MISSING)
        namespace[attribute] = value
        test_case.addCleanup(_unshadow, namespace, attribute, previous)


def _unshadow(namespace, attribute, previous):
    if previous is _MISSING:
        namespace.pop(attribute, None)
    else:
        namespace[attribute] = previous


def _getdate(value=None):
    if value is None:
        return TODAY
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


class BriefingCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(singles={SETTINGS: {}}, now=lambda: NOW)
        self.log_error = MagicMock()
        for module in (_db_mod, _bh):
            _shadow_frappe(
                self, module, db=self.db, get_all=self.db.get_all, log_error=self.log_error,
            )
        self._patch(_bh, now_datetime=lambda: NOW, getdate=_getdate, get_datetime=_get_datetime)
        self._patch(_db_mod, now_datetime=lambda: NOW)
        # The sections wrap their bodies in try/except, so a MagicMock date helper does not fail
        # loudly - it makes the section come back empty and the test say nothing.
        from types import SimpleNamespace
        for module in (_db_mod, _bh):
            _shadow_frappe(self, module, utils=SimpleNamespace(
                getdate=_getdate, get_datetime=_get_datetime, now_datetime=lambda: NOW,
                get_url=lambda: "https://erp.i8.test",
                format_datetime=lambda value, fmt=None: _get_datetime(value).strftime("%d/%m %H:%M"),
            ))

    def _patch(self, module, **attributes):
        from unittest.mock import patch
        for name, value in attributes.items():
            patcher = patch.object(module, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def recorded(self, state: str, summary: str, checked_on=NOW, since=NOW):
        self.db.singles[SETTINGS].update({
            _bh.STATE_FIELD: state, _bh.STATUS_FIELD: summary,
            _bh.CHECKED_FIELD: checked_on, _bh.SINCE_FIELD: since,
        })

    def account(self, **fields):
        row = {"company": "Intelligence8", "sync_enabled": 1, "current_balance": 0.0,
               "balance_date": TODAY, "last_statement_sync": NOW}
        row.update(fields)
        if ACCOUNT in self.db._working.get("Inter Company Account", {}):
            self.db.set_value("Inter Company Account", ACCOUNT, row)
        else:
            self.db.add("Inter Company Account", ACCOUNT, **row)

    def transactions(self, total: int, unreconciled: int):
        for index in range(total):
            self.db.add(
                "Bank Transaction", f"BT-{index}", docstatus=1,
                unallocated_amount=10.0 if index < unreconciled else 0.0,
            )


class TestBankingHealthSection(BriefingCase):
    def test_a_healthy_channel_adds_nothing(self):
        self.recorded(state="ok", summary="OK")

        self.assertEqual(_db_mod._banking_health_section(), "")

    def test_nothing_recorded_yet_adds_nothing(self):
        self.assertEqual(_db_mod._banking_health_section(), "")

    def test_a_problem_is_named_with_its_age_and_when_it_was_checked(self):
        self.recorded(
            state="integration_enabled", summary="DESLIGADA ha 152 dias",
            checked_on=NOW - datetime.timedelta(hours=5),
        )

        section = _db_mod._banking_health_section()

        self.assertIn("Comunicacao bancaria", section)
        self.assertIn("152", section)
        self.assertIn("verificado", section.lower())

    def test_a_failure_is_reported_inside_the_section(self):
        """A section that vanishes reads as "nothing to worry about"."""
        def explode():
            raise RuntimeError("boom")

        self._patch(_db_mod, banking_health_status=explode)

        self.assertIn("Comunicacao bancaria", _db_mod._banking_health_section())

    def test_it_comes_before_the_balance(self):
        """Whether the channel is alive decides how to read the number below it."""
        source = open(_db_mod.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
        block = source[source.index("section_funcs = ["):source.index("if is_monday:")]

        self.assertLess(block.index("_banking_health_section"), block.index("_bank_balance_section"))


class TestTheBalanceStopsLying(BriefingCase):
    def test_a_balance_from_today_prints_without_an_age(self):
        self.account(current_balance=12345.67, balance_date=TODAY)

        line = _db_mod._bank_balance_section()

        self.assertIn("12,345.67", line)
        self.assertNotIn("ha ", line)

    def test_an_old_balance_says_how_old_it_is(self):
        self.account(current_balance=12345.67, balance_date=TODAY - datetime.timedelta(days=152))

        line = _db_mod._bank_balance_section()

        self.assertIn("12,345.67", line)
        self.assertIn("152", line)

    def test_an_unreadable_date_still_shows_the_balance(self):
        """The age is an annotation; losing it must not lose the number."""
        self.account(current_balance=12345.67, balance_date="not a date at all")

        self.assertIn("12,345.67", _db_mod._bank_balance_section())

    def test_a_balance_without_a_date_says_so(self):
        self.account(current_balance=12345.67, balance_date=None)

        self.assertIn("sem data", _db_mod._bank_balance_section().lower())


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

    def test_pending_transactions_are_still_counted_the_same_way(self):
        self.transactions(total=10, unreconciled=3)
        self.account(last_statement_sync=NOW - datetime.timedelta(hours=6))

        section = _db_mod._reconciliation_status_section()

        self.assertIn("7/10", section)
        self.assertIn("3", section)


if __name__ == "__main__":
    unittest.main()
