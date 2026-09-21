"""Tests for the payment-order section of the daily briefing (spec 4.6).

An alert fires once; the briefing repeats, every day, the orders that still need a human.
The rows live in a ``FakeDB`` so that the filters of the section are really evaluated.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from brazil_module.tests._payment_fakes import DOCTYPE, FakeDB, install_frappe_mock

frappe = install_frappe_mock()

import brazil_module.services.intelligence.recurring.daily_briefing as _db_mod

NOW = datetime(2026, 9, 20, 8, 0, 0)
INVOICE = "ACC-PINV-2026-00031"
_MISSING = object()


def _shadow_frappe(test_case, **attributes):
    """Shadow attributes of the frappe mock the module holds (never ``patch.object`` on it)."""
    namespace = _db_mod.frappe.__dict__
    for attribute, value in attributes.items():
        previous = namespace.get(attribute, _MISSING)
        namespace[attribute] = value
        test_case.addCleanup(_unshadow, namespace, attribute, previous)


def _unshadow(namespace, attribute, previous):
    if previous is _MISSING:
        namespace.pop(attribute, None)
    else:
        namespace[attribute] = previous


class SectionCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(now=lambda: NOW)
        self.log_error = MagicMock()
        _shadow_frappe(self, db=self.db, get_all=self.db.get_all, log_error=self.log_error)

    def add_order(self, name, status, *, age=timedelta(0), docstatus=1, amount=100.0, **fields):
        row = {
            "modified": NOW - age, "purchase_invoice": INVOICE, "bank_status": None,
            "payment_entry": None, "bank_request_at": None, **fields,
        }
        self.db.add(DOCTYPE, name, status=status, docstatus=docstatus, amount=amount, **row)

    def section(self):
        return _db_mod._payment_orders_section(now=NOW)


class TestNothingToReport(SectionCase):
    def test_empty_when_there_are_no_orders(self):
        self.assertEqual(self.section(), "")

    def test_empty_when_every_order_is_fine(self):
        self.add_order("IPO-1", "Completed", payment_entry="ACC-PAY-1", age=timedelta(days=9))
        self.add_order("IPO-2", "Cancelled", docstatus=2, age=timedelta(days=9))
        self.add_order("IPO-3", "Failed", age=timedelta(days=3))
        self.add_order("IPO-4", "Awaiting Bank", bank_request_at=NOW - timedelta(hours=3), age=timedelta(hours=3))
        self.add_order("IPO-5", "Approved", age=timedelta(hours=2))
        self.add_order("IPO-6", "Processing", bank_request_at=NOW - timedelta(minutes=1))
        self.assertEqual(self.section(), "")


class TestNeedsVerification(SectionCase):
    def test_lists_name_amount_and_days(self):
        self.add_order(
            "IPO-2026-00001", "Needs Verification", amount=16800.0,
            bank_request_at=NOW - timedelta(days=174, hours=5), age=timedelta(days=1),
        )
        text = self.section()
        self.assertIn("Verificacao necessaria", text)
        self.assertIn("IPO-2026-00001", text)
        self.assertIn("16,800.00", text)
        self.assertIn("174 dias", text)
        self.assertIn(INVOICE, text)

    def test_without_a_request_time_the_days_come_from_modified(self):
        self.add_order("IPO-OLD", "Needs Verification", bank_request_at=None, age=timedelta(days=3, hours=1))
        self.assertIn("3 dias", self.section())

    def test_is_listed_from_the_first_day(self):
        self.add_order("IPO-NEW", "Needs Verification", bank_request_at=NOW - timedelta(minutes=20))
        text = self.section()
        self.assertIn("IPO-NEW", text)
        self.assertIn("hoje", text)

    def test_a_cancelled_document_is_not_listed(self):
        self.add_order("IPO-X", "Needs Verification", docstatus=2)
        self.assertEqual(self.section(), "")


class TestAwaitingBank(SectionCase):
    def test_older_than_24h_is_listed_with_the_bank_status(self):
        self.add_order(
            "IPO-WAIT", "Awaiting Bank", amount=500.0, bank_status="AGUARDANDO_APROVACAO",
            bank_request_at=NOW - timedelta(days=2, hours=1), age=timedelta(minutes=5),
        )
        text = self.section()
        self.assertIn("Aguardando o banco", text)
        self.assertIn("IPO-WAIT", text)
        self.assertIn("AGUARDANDO_APROVACAO", text)
        self.assertIn("2 dias", text)

    def test_the_age_is_the_request_time_not_the_last_poll(self):
        """Every poll bumps ``modified``: an order polled a minute ago may be waiting for days."""
        self.add_order("IPO-POLLED", "Awaiting Bank", bank_request_at=NOW - timedelta(days=5), age=timedelta(minutes=1))
        self.assertIn("IPO-POLLED", self.section())

    def test_younger_than_24h_is_not_listed(self):
        self.add_order("IPO-FRESH", "Awaiting Bank", bank_request_at=NOW - timedelta(hours=23), age=timedelta(hours=23))
        self.assertEqual(self.section(), "")

    def test_without_a_request_time_modified_decides(self):
        self.add_order("IPO-NO-TIME", "Awaiting Bank", bank_request_at=None, age=timedelta(days=2))
        self.assertIn("IPO-NO-TIME", self.section())


class TestFailed(SectionCase):
    def test_failed_in_the_last_24h_is_listed(self):
        self.add_order("IPO-FAILED", "Failed", amount=250.0, age=timedelta(hours=2))
        text = self.section()
        self.assertIn("Falharam nas ultimas 24h", text)
        self.assertIn("IPO-FAILED", text)
        self.assertIn("250.00", text)

    def test_failed_earlier_is_not_repeated(self):
        self.add_order("IPO-FAILED-OLD", "Failed", age=timedelta(hours=25))
        self.assertEqual(self.section(), "")


class TestCompletedWithoutPaymentEntry(SectionCase):
    def test_is_listed_whatever_its_age(self):
        self.add_order("IPO-NO-PE", "Completed", payment_entry=None, age=timedelta(days=12))
        self.add_order("IPO-EMPTY-PE", "Completed", payment_entry="", age=timedelta(minutes=5))
        text = self.section()
        self.assertIn("sem Payment Entry", text)
        self.assertIn("IPO-NO-PE", text)
        self.assertIn("IPO-EMPTY-PE", text)

    def test_with_a_payment_entry_is_not_listed(self):
        self.add_order("IPO-DONE", "Completed", payment_entry="ACC-PAY-2026-00001", age=timedelta(days=1))
        self.assertEqual(self.section(), "")


class TestIdleOrders(SectionCase):
    def test_draft_pending_and_approved_idle_for_more_than_24h_are_listed(self):
        self.add_order("IPO-DRAFT", "Draft", docstatus=0, age=timedelta(days=2))
        self.add_order("IPO-PENDING", "Pending Approval", age=timedelta(days=3))
        self.add_order("IPO-APPROVED", "Approved", age=timedelta(hours=25))
        text = self.section()
        self.assertIn("bloqueiam a fatura", text)
        for fragment in ("IPO-DRAFT", "Draft", "IPO-PENDING", "Pending Approval", "IPO-APPROVED", "Approved"):
            self.assertIn(fragment, text)

    def test_younger_than_24h_is_not_listed(self):
        self.add_order("IPO-DRAFT", "Draft", docstatus=0, age=timedelta(hours=23))
        self.add_order("IPO-APPROVED", "Approved", age=timedelta(hours=1))
        self.assertEqual(self.section(), "")


    def test_a_cancelled_document_is_not_listed_even_with_a_stale_status(self):
        self.add_order("IPO-GONE", "Approved", docstatus=2, age=timedelta(days=5))
        self.assertEqual(self.section(), "")


class TestLayout(SectionCase):
    def test_groups_come_most_urgent_first_under_one_title(self):
        self.add_order("IPO-A", "Approved", age=timedelta(days=2))
        self.add_order("IPO-C", "Completed", age=timedelta(days=2))
        self.add_order("IPO-F", "Failed", age=timedelta(hours=1))
        self.add_order("IPO-W", "Awaiting Bank", bank_request_at=NOW - timedelta(days=2))
        self.add_order("IPO-V", "Needs Verification", bank_request_at=NOW - timedelta(days=2))
        text = self.section()
        self.assertTrue(text.startswith(_db_mod.PAYMENT_ORDERS_TITLE))
        self.assertEqual(text.count(_db_mod.PAYMENT_ORDERS_TITLE), 1)
        positions = [text.find(name) for name in ("IPO-V", "IPO-W", "IPO-F", "IPO-C", "IPO-A")]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_a_long_group_is_capped_and_says_how_many_were_left_out(self):
        total = _db_mod.PAYMENT_ORDERS_PER_GROUP + 3
        for index in range(total):
            requested = NOW - timedelta(days=index + 1)
            self.add_order(f"IPO-NV-{index:02d}", "Needs Verification", bank_request_at=requested)
        text = self.section()
        self.assertIn(f"Verificacao necessaria (o banco pode ter o pagamento): {total}", text)
        self.assertIn("e mais 3", text)
        # the oldest come first: they are the ones at risk of leaving the bank's 90-day query window
        self.assertIn(f"IPO-NV-{total - 1:02d}", text)
        self.assertNotIn("IPO-NV-00", text)

    def test_the_orders_are_read_in_a_few_queries_not_one_per_order(self):
        for index in range(5):
            self.add_order(f"IPO-NV-{index}", "Needs Verification", bank_request_at=NOW - timedelta(days=2))
        calls = []
        _shadow_frappe(self, get_all=lambda *a, **kw: calls.append(a) or self.db.get_all(*a, **kw))
        self.section()
        self.assertLessEqual(len(calls), 5)


class TestNeverRaises(SectionCase):
    def test_a_database_error_is_reported_in_the_section_and_logged(self):
        _shadow_frappe(self, get_all=MagicMock(side_effect=RuntimeError("Unknown column 'bank_status'")))
        text = self.section()
        self.assertIn(_db_mod.PAYMENT_ORDERS_TITLE, text)
        self.assertIn("Nao foi possivel", text)
        self.log_error.assert_called_once()

    def test_a_failing_error_log_does_not_raise_either(self):
        _shadow_frappe(self, get_all=MagicMock(side_effect=RuntimeError("db is gone")))
        self.log_error.side_effect = RuntimeError("db is gone")
        self.assertIn("Nao foi possivel", self.section())

    def test_a_row_with_garbage_in_it_does_not_raise(self):
        self.add_order("IPO-BAD", "Needs Verification", amount=None, bank_request_at="not a date", modified=None)
        text = self.section()
        self.assertIn("IPO-BAD", text)

    def test_the_clock_is_frappes_when_none_is_given(self):
        self.add_order("IPO-V", "Needs Verification", bank_request_at=NOW - timedelta(days=4))
        _shadow_frappe(self, utils=MagicMock(now_datetime=MagicMock(return_value=NOW)))
        self.assertIn("4 dias", _db_mod._payment_orders_section())


class TestBriefingCarriesTheSection(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.side_effect = None
        frappe.get_all.return_value = []
        frappe.utils.now_datetime.side_effect = None
        frappe.utils.now_datetime.return_value = NOW
        # reset_mock() keeps side_effect, and other test modules leave some behind
        frappe.db.count.side_effect = None
        frappe.db.count.return_value = 0
        frappe.db.sql.side_effect = None
        frappe.db.sql.return_value = []
        frappe.log_error.side_effect = None

    def test_build_briefing_includes_the_section_after_the_payables(self):
        marker = f"{_db_mod.PAYMENT_ORDERS_TITLE}\n  - IPO-2026-00001"
        with patch.object(_db_mod, "_payment_orders_section", return_value=marker):
            text = _db_mod.build_briefing()
        self.assertIn(marker, text)
        self.assertLess(text.find("*Contas a Pagar:*"), text.find(marker))
        self.assertLess(text.find(marker), text.find("*Pendencias:*"))

    def test_build_briefing_has_no_payment_section_when_there_is_nothing(self):
        self.assertNotIn(_db_mod.PAYMENT_ORDERS_TITLE, _db_mod.build_briefing())

    def test_the_formatter_is_told_to_keep_every_order(self):
        self.assertIn("Pagamentos Inter", _db_mod.JARVIS_PERSONALITY)


class TestTheFormatterCannotDropAnOrder(SectionCase):
    """The briefing is rewritten by an LLM before it reaches Telegram. The prompt asks it to keep
    every order; these tests cover the part that does not depend on it obeying."""

    def setUp(self):
        super().setUp()
        self.db.singles["I8 Agent Settings"] = {"enabled": 1, "briefing_enabled": 1}
        self.cache = MagicMock()
        _shadow_frappe(self, cache=self.cache, utils=MagicMock(now_datetime=MagicMock(return_value=NOW)))
        self.add_order(
            "IPO-2026-00001", "Needs Verification", amount=16800.0, bank_request_at=NOW - timedelta(days=174),
        )
        self.add_order("IPO-2026-00007", "Needs Verification", amount=250.0, bank_request_at=NOW - timedelta(hours=2))
        self.raw_section = self.section()
        self.formatted = "Bom dia, Abel!"
        self.send = MagicMock(return_value=True)
        replacements = {
            "_is_briefing_time": MagicMock(return_value=True),
            "build_briefing": MagicMock(side_effect=lambda: f"*Daily Briefing*\n{_db_mod._payment_orders_section()}"),
            "_get_user_first_name": MagicMock(return_value="Abel"),
            "_build_briefing_buttons": MagicMock(return_value=None),
            "_format_with_jarvis": MagicMock(side_effect=lambda *args: self.formatted),
            "_send_via_telegram": self.send,
        }
        for name, replacement in replacements.items():
            patcher = patch.object(_db_mod, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def sent_messages(self):
        return [call.args[0] for call in self.send.call_args_list]

    def marked_as_sent(self):
        return [c for c in self.cache.set_value.call_args_list if c.args[0] == _db_mod._BRIEFING_SENT_KEY]

    def test_every_order_named_means_one_message(self):
        self.formatted = "Bom dia, Abel! Verifique IPO-2026-00001 (R$ 16.800,00) e `IPO-2026-00007`."
        _db_mod.scheduled_briefing()
        self.assertEqual(self.sent_messages(), [self.formatted])

    def test_an_order_the_formatter_dropped_is_sent_verbatim(self):
        self.formatted = "Bom dia, Abel! Ha uma ordem para verificar: IPO-2026-00001."
        _db_mod.scheduled_briefing()
        self.assertEqual(self.sent_messages(), [self.formatted, self.raw_section])
        self.assertIn("IPO-2026-00007", self.raw_section)
        self.assertIn("16,800.00", self.raw_section)

    def test_a_longer_name_does_not_stand_in_for_a_shorter_one(self):
        self.formatted = "Verifique IPO-2026-000011 e IPO-2026-00007."
        _db_mod.scheduled_briefing()
        self.assertEqual(len(self.sent_messages()), 2)

    def test_a_name_with_a_space_is_checked_too(self):
        """The naming series is the operator's to change: no assumption about what a name looks like."""
        self.add_order("PAG 2026 0009", "Needs Verification", bank_request_at=NOW - timedelta(days=1))
        self.formatted = "Verifique IPO-2026-00001 e IPO-2026-00007."
        _db_mod.scheduled_briefing()
        self.assertEqual(len(self.sent_messages()), 2)
        self.assertIn("PAG 2026 0009", self.sent_messages()[1])

    def test_a_formatter_that_failed_sends_the_raw_data_once(self):
        self.formatted = None
        _db_mod.scheduled_briefing()
        messages = self.sent_messages()
        self.assertEqual(len(messages), 1)
        self.assertIn(self.raw_section, messages[0])

    def test_nothing_is_added_when_the_briefing_itself_was_not_sent(self):
        self.send.return_value = False
        _db_mod.scheduled_briefing()
        self.assertEqual(len(self.sent_messages()), 1)
        self.assertEqual(self.marked_as_sent(), [])

    def test_no_orders_means_no_second_message(self):
        for name in ("IPO-2026-00001", "IPO-2026-00007"):
            self.db.set_value(DOCTYPE, name, "status", "Cancelled")
        _db_mod.scheduled_briefing()
        self.assertEqual(self.sent_messages(), [self.formatted])

    def test_a_failing_second_send_never_raises_and_the_day_stays_marked(self):
        self.send.side_effect = [True, RuntimeError("telegram down")]
        _db_mod.scheduled_briefing()
        self.assertEqual(len(self.sent_messages()), 2)
        self.assertEqual(len(self.marked_as_sent()), 1)
        self.log_error.assert_called()

    def test_the_section_that_could_not_be_read_is_sent_too(self):
        """"Could not read the orders" is not something the formatter may turn into silence."""
        self.formatted = "Bom dia, Abel! Tudo em ordem."
        _shadow_frappe(self, get_all=MagicMock(side_effect=RuntimeError("Unknown column 'bank_status'")))
        _db_mod.scheduled_briefing()
        messages = self.sent_messages()
        self.assertEqual(len(messages), 2)
        self.assertIn("Nao foi possivel", messages[1])


if __name__ == "__main__":
    unittest.main()
