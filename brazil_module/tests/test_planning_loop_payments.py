"""Tests for the weekly payment scheduler (spec 4.6).

The scheduler creates Inter Payment Orders and queues them. It never talks to the bank, never
writes a Payment Entry for a Pix or a boleto, and asks the same guard as every other entry point
whether an invoice may be paid. State matters, so the database is a ``FakeDB`` and the guard is
the real ``check_invoice_payable``; only the two ``payment_service`` entry points are replaced
(``autospec``: a call that does not fit their real signature fails here).
"""

import datetime
import inspect
import sqlite3
import sys
import unittest
from unittest.mock import MagicMock, patch

from brazil_module.tests._payment_fakes import DOCTYPE, FakeDB, install_frappe_mock

install_frappe_mock()

# Other test modules park MagicMock placeholders for these when they are imported first.
for _name in (
    "brazil_module.services.banking.auth_manager",
    "brazil_module.services.banking.inter_client",
    "brazil_module.services.banking.payment_service",
    "brazil_module.services.intelligence.recurring.planning_loop",
):
    if isinstance(sys.modules.get(_name), MagicMock):
        del sys.modules[_name]

import brazil_module.services.banking.payment_guards as _guards
import brazil_module.services.banking.payment_service as _ps_mod
import brazil_module.services.intelligence.recurring.planning_loop as _pl_mod

TODAY = datetime.date.today()
TOMORROW = TODAY + datetime.timedelta(days=1)
INVOICE = "ACC-PINV-2026-00040"
OTHER_INVOICE = "ACC-PINV-2026-00041"
SUPPLIER = "SUP-A"
COMPANY = "Intelligence8"
ACCOUNT = "Inter - I8"
BARCODE = "07797000000000000004501008460019310001802680"
ALL_STATUSES = (
    "Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
    "Needs Verification", "Completed", "Failed", "Cancelled",
)
_MISSING = object()


def _flt(value, precision=None):
    number = float(value or 0)
    return round(number, precision) if precision is not None else number


def _shadow_frappe(test_case, **attributes):
    """Replace attributes of every frappe mock the modules under test hold, until the test ends.

    The attribute is shadowed in the mock's ``__dict__`` (never ``patch.object`` on the shared
    mock - see ``_payment_fakes.patch_frappe``).
    """
    holders = {id(module.frappe): module.frappe for module in (_pl_mod, _guards)}
    for holder in holders.values():
        for attribute, value in attributes.items():
            previous = holder.__dict__.get(attribute, _MISSING)
            holder.__dict__[attribute] = value
            test_case.addCleanup(_unshadow, holder.__dict__, attribute, previous)


def _unshadow(namespace, attribute, previous):
    if previous is _MISSING:
        namespace.pop(attribute, None)
    else:
        namespace[attribute] = previous


def _start(test_case, patcher):
    started = patcher.start()
    test_case.addCleanup(patcher.stop)
    return started


def _invoice_row(name=INVOICE, amount=1500.0, due=TODAY, supplier=SUPPLIER):
    """A row as the weekly selection SQL returns it."""
    return {
        "name": name, "supplier": supplier, "supplier_name": "Fornecedor A Ltda",
        "outstanding_amount": amount, "due_date": due,
    }


class SchedulerCase(unittest.TestCase):
    """Everything enabled, one Pix invoice due today, orders come out ``Approved``."""

    def setUp(self):
        self.db = FakeDB(singles={
            "I8 Agent Settings": {"enabled": 1, "auto_schedule_payments": 1, "telegram_chat_id": "chat-1"},
            "Banco Inter Settings": {"enabled": 1, "payment_approval_required": 0},
        })
        self.cache = MagicMock()
        self.cache.get_value.return_value = None
        self.log_error = MagicMock()
        _shadow_frappe(
            self, db=self.db, get_all=self.db.get_all, cache=self.cache, log_error=self.log_error,
            get_doc=MagicMock(), new_doc=MagicMock(),
        )
        for name, value in (("_", lambda text: text), ("flt", _flt)):
            _start(self, patch.object(_guards, name, value))
        self.messages = []
        _start(self, patch.object(_pl_mod, "_notify_telegram", side_effect=self.messages.append))
        self.created = []
        self.enqueued = []
        self.seen_by_worker = []
        self.order_status = "Approved"
        self.create_error = None
        self.create = _start(self, patch.object(
            _ps_mod, "create_payment_order_for_invoice", autospec=True, side_effect=self._create))
        self.enqueue = _start(self, patch.object(
            _ps_mod, "enqueue_payment_execution", autospec=True, side_effect=self._enqueue))
        self.add_invoice(INVOICE, mode="Pix")
        self.db.add("Supplier", SUPPLIER, on_hold=0, hold_type=None, pix_key="pix@fornecedor-a.com.br")

    # -- the two payment_service entry points ------------------------------------------

    def _create(self, invoice_name, payment_type, **details):
        self.created.append((invoice_name, payment_type, details))
        if self.create_error is not None:
            error, self.create_error = self.create_error, None
            self._insert_uncommitted(f"IPO-HALF-{len(self.created):05d}", invoice_name, "Draft", 0)
            raise error
        name = f"IPO-TEST-{len(self.created):05d}"
        self._insert_uncommitted(name, invoice_name, self.order_status, 1)
        self.db.events.append(("create_order", name))
        return name

    def _insert_uncommitted(self, name, invoice_name, status, docstatus):
        # Straight into the working copy: FakeDB.add() would also commit, and the point of these
        # tests is what another connection (the worker) can see, and what a rollback undoes.
        self.db._working.setdefault(DOCTYPE, {})[name] = {
            "name": name, "docstatus": docstatus, "status": status, "purchase_invoice": invoice_name,
            "payment_entry": None, "modified": datetime.datetime.now(),
        }

    def _enqueue(self, name):
        self.enqueued.append(name)
        self.db.events.append(("enqueue", name))
        try:
            committed = self.db.committed_row(DOCTYPE, name)
        except KeyError:
            committed = {}
        self.seen_by_worker.append((name, committed.get("status"), committed.get("docstatus")))
        return True

    # -- fixtures ----------------------------------------------------------------------

    def add_invoice(self, name, mode, amount=1500.0, due=TODAY, **fields):
        self.db.add(
            "Purchase Invoice", name, docstatus=1, on_hold=0, outstanding_amount=amount,
            supplier=SUPPLIER, company=COMPANY, due_date=due, credit_to="2.1.1 Fornecedores - I8", **fields,
        )
        self.db.add(
            "Payment Schedule", f"{name}-ps", parent=name, parenttype="Purchase Invoice", mode_of_payment=mode,
        )
        self.db.sql_result = [*self.db.sql_result, _invoice_row(name, amount, due)]

    def add_order(self, name, status, invoice=INVOICE, docstatus=1, **fields):
        self.db.add(DOCTYPE, name, status=status, docstatus=docstatus, purchase_invoice=invoice, **fields)

    def summary(self):
        self.assertEqual(len(self.messages), 1, self.messages)
        return self.messages[0]


class TestTheSchedulerNeverTalksToTheBank(unittest.TestCase):
    """I1 - static tripwires on the module's source."""

    def setUp(self):
        self.source = inspect.getsource(_pl_mod)

    def test_no_client_and_no_send(self):
        for forbidden in ("InterAPIClient", "inter_client", "send_pix", "pay_barcode", "send_ted"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_no_request_payload_is_built_here(self):
        for forbidden in ("dataAgendamento", "dataPagamento", "codBarraLinhaDigitavel", "valorPagar"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_the_per_invoice_cache_lock_is_gone_and_the_daily_lock_stays(self):
        self.assertNotIn("i8:payment_lock", self.source)
        self.assertIn("i8:payment_scheduling:lock", self.source)

    def test_no_filter_on_a_field_that_does_not_exist(self):
        self.assertNotIn('"enabled": 1', self.source)

    # Older than this change and not part of the payment path: left as they are.
    LONG_BEFORE_THIS_CHANGE = ("run_reconciliation", "process_pending_nfs")

    def test_functions_stay_under_fifty_lines(self):
        for name, function in inspect.getmembers(_pl_mod, inspect.isfunction):
            if function.__module__ != _pl_mod.__name__ or name in self.LONG_BEFORE_THIS_CHANGE:
                continue
            with self.subTest(function=name):
                self.assertLessEqual(len(inspect.getsource(function).splitlines()), 50)


class TestSwitches(SchedulerCase):
    def assert_nothing_happened(self):
        self.assertEqual(self.created, [])
        self.assertEqual(self.enqueued, [])
        self.assertEqual(self.db.sql_calls, [])
        self.assertEqual(self.messages, [])
        self.cache.set_value.assert_not_called()
        self.assertEqual(self.db.get_all(DOCTYPE), [])

    def test_kill_switch_off_creates_nothing(self):
        self.db.singles["Banco Inter Settings"]["enabled"] = 0
        _pl_mod.schedule_weekly_payments()
        self.assert_nothing_happened()

    def test_auto_schedule_payments_off_creates_nothing(self):
        self.db.singles["I8 Agent Settings"]["auto_schedule_payments"] = 0
        _pl_mod.schedule_weekly_payments()
        self.assert_nothing_happened()

    def test_agent_disabled_creates_nothing(self):
        self.db.singles["I8 Agent Settings"]["enabled"] = 0
        _pl_mod.schedule_weekly_payments()
        self.assert_nothing_happened()

    def test_the_daily_lock_still_stops_a_second_run(self):
        self.cache.get_value.return_value = 1
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.created, [])
        self.assertEqual(self.db.sql_calls, [])

    def test_positive_control_everything_on_creates_one_order(self):
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(len(self.created), 1)
        self.cache.set_value.assert_called_once()
        self.assertIn("i8:payment_scheduling:lock", self.cache.set_value.call_args.args[0])


class TestPixInvoice(SchedulerCase):
    def test_creates_the_order_from_the_invoice_then_queues_it(self):
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(
            self.created,
            [(INVOICE, "PIX", {"pix_key": "pix@fornecedor-a.com.br", "scheduled_date": TODAY})],
        )
        self.assertEqual(self.enqueued, ["IPO-TEST-00001"])

    def test_the_order_is_committed_before_anything_is_enqueued(self):
        """frappe.enqueue is immediate (enqueue_after_commit=False): a worker that picks the job up
        before the scheduler commits would not find an Approved row and would skip it for good."""
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.seen_by_worker, [("IPO-TEST-00001", "Approved", 1)])
        events = [event for event in self.db.events if event[0] in ("create_order", "commit", "enqueue")]
        self.assertEqual(events[:3], [("create_order", "IPO-TEST-00001"), ("commit",), ("enqueue", "IPO-TEST-00001")])

    def test_pending_approval_is_not_enqueued_and_is_listed_as_waiting(self):
        self.order_status = "Pending Approval"
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.enqueued, [])
        self.assertEqual(self.db.committed_row(DOCTYPE, "IPO-TEST-00001")["status"], "Pending Approval")
        summary = self.summary()
        self.assertIn("Aguardando aprovacao no ERP", summary)
        self.assertIn("IPO-TEST-00001", summary)
        self.assertNotIn("Na fila de execucao", summary)

    def test_any_status_other_than_approved_is_never_enqueued(self):
        for status in (s for s in ALL_STATUSES if s != "Approved"):
            with self.subTest(status=status):
                self.order_status = status
                result = _pl_mod._schedule_single_payment(_invoice_row())
                self.assertEqual(self.enqueued, [])
                self.assertEqual(result["status"], "pending_approval")
                self.assertEqual(result["order_status"], status)
                # free the invoice for the next status
                self.db.set_value(DOCTYPE, result["payment_order"], "status", "Failed")
                self.db.commit()

    def test_supplier_without_pix_key_is_an_error_and_nothing_is_created(self):
        self.db.set_value("Supplier", SUPPLIER, "pix_key", None)
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.created, [])
        self.assertEqual(self.enqueued, [])
        summary = self.summary()
        self.assertIn("Erros: 1", summary)
        self.assertIn(INVOICE, summary)
        self.assertIn("PIX key", summary)

    def test_queued_section_names_the_order_and_does_not_claim_a_payment(self):
        _pl_mod.schedule_weekly_payments()
        summary = self.summary()
        self.assertIn("Na fila de execucao: 1", summary)
        self.assertIn("IPO-TEST-00001", summary)
        self.assertIn(INVOICE, summary)
        self.assertIn("1,500.00", summary)
        for claim in ("Agendados", "Pagos", "pago"):
            self.assertNotIn(claim, summary)

    def test_a_queue_that_refuses_the_job_names_the_order_to_execute_by_hand(self):
        self.enqueue.side_effect = ConnectionError("redis is down")
        _pl_mod.schedule_weekly_payments()
        # the order exists and keeps protecting its invoice; the operator is told which one it is
        self.assertEqual(self.db.committed_row(DOCTYPE, "IPO-TEST-00001")["status"], "Approved")
        summary = self.summary()
        self.assertIn("Erros: 1", summary)
        self.assertIn("IPO-TEST-00001", summary)
        self.assertIn("redis is down", summary)


    def test_any_failure_after_the_order_is_committed_names_the_order(self):
        real_get_value = self.db.get_value

        def get_value(doctype, *args, **kwargs):
            if doctype == DOCTYPE:
                raise RuntimeError("MySQL server has gone away")
            return real_get_value(doctype, *args, **kwargs)

        with patch.object(self.db, "get_value", side_effect=get_value):
            with patch.object(_pl_mod, "check_invoice_payable", return_value=None):
                _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.enqueued, [])
        self.assertEqual(self.db.committed_row(DOCTYPE, "IPO-TEST-00001")["status"], "Approved")
        summary = self.summary()
        self.assertIn("Erros: 1", summary)
        self.assertIn("IPO-TEST-00001", summary)

    def test_a_job_that_is_already_queued_still_counts_as_queued(self):
        """``enqueue_payment_execution`` answers False when a job for the order already exists."""
        self.enqueue.side_effect = None
        self.enqueue.return_value = False
        _pl_mod.schedule_weekly_payments()
        self.assertIn("Na fila de execucao: 1", self.summary())


class TestBoletoInvoice(SchedulerCase):
    def setUp(self):
        super().setUp()
        self.db.set_value("Payment Schedule", f"{INVOICE}-ps", "mode_of_payment", "Boleto")
        self.db.set_value("Purchase Invoice", INVOICE, "boleto_barcode", BARCODE)
        self.db.commit()

    def test_creates_a_boleto_order_with_barcode_and_due_date(self):
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(
            self.created,
            [(INVOICE, "Boleto Payment", {"barcode": BARCODE, "boleto_due_date": TODAY, "scheduled_date": TODAY})],
        )
        self.assertEqual(self.enqueued, ["IPO-TEST-00001"])
        self.assertEqual(self.seen_by_worker, [("IPO-TEST-00001", "Approved", 1)])

    def test_missing_barcode_is_an_error_and_nothing_is_created(self):
        self.db.set_value("Purchase Invoice", INVOICE, "boleto_barcode", "")
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.created, [])
        self.assertEqual(self.enqueued, [])
        self.assertIn("Erros: 1", self.summary())
        self.assertIn("boleto barcode", self.summary())


class TestOtherModes(SchedulerCase):
    def test_ted_is_an_error_because_the_api_has_no_ted_endpoint(self):
        self.db.set_value("Payment Schedule", f"{INVOICE}-ps", "mode_of_payment", "TED")
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.created, [])
        self.assertIn("Erros: 1", self.summary())
        self.assertIn("TED", self.summary())

    def test_unknown_mode_is_skipped_with_the_reason(self):
        self.db.set_value("Payment Schedule", f"{INVOICE}-ps", "mode_of_payment", "Cheque")
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.created, [])
        summary = self.summary()
        self.assertIn("Ignorados: 1", summary)
        self.assertIn("Cheque", summary)


class TestTheSharedGuard(SchedulerCase):
    """``check_invoice_payable`` (the real one) replaces both status-blind pre-checks."""

    def test_an_order_in_flight_skips_the_invoice_and_the_summary_says_why(self):
        self.add_order("IPO-2026-00002", "Awaiting Bank")
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.created, [])
        self.assertEqual(self.enqueued, [])
        summary = self.summary()
        self.assertIn("Ignorados: 1", summary)
        self.assertIn(INVOICE, summary)
        self.assertIn("IPO-2026-00002", summary)
        self.assertIn("Awaiting Bank", summary)

    def test_every_blocking_status_skips(self):
        for status in ("Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
                       "Needs Verification", "Completed"):
            with self.subTest(status=status):
                db_before = len(self.created)
                name = f"IPO-BLOCK-{status}"
                self.add_order(name, status)
                result = _pl_mod._schedule_single_payment(_invoice_row())
                self.assertEqual(result["status"], "skipped")
                self.assertIn(name, result["reason"])
                self.assertEqual(len(self.created), db_before)
                self.db.set_value(DOCTYPE, name, "status", "Failed")
                self.db.commit()

    def test_a_failed_order_does_not_block_the_next_run(self):
        self.add_order("IPO-2026-00002", "Failed")
        self.add_order("IPO-2026-00003", "Cancelled", docstatus=2)
        _pl_mod.schedule_weekly_payments()
        self.assertEqual([call[0] for call in self.created], [INVOICE])
        self.assertEqual(self.enqueued, ["IPO-TEST-00001"])

    def test_a_completed_order_with_payment_entry_does_not_block(self):
        self.add_order("IPO-2026-00002", "Completed", payment_entry="ACC-PAY-2026-00001")
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(len(self.created), 1)

    def test_a_draft_payment_entry_skips_the_invoice(self):
        self.db.add("Payment Entry", "ACC-PAY-2026-00009", docstatus=0, inter_payment_order=None)
        self.db.add(
            "Payment Entry Reference", "ACC-PAY-2026-00009-ref", parent="ACC-PAY-2026-00009",
            parenttype="Payment Entry", reference_doctype="Purchase Invoice", reference_name=INVOICE,
        )
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.created, [])
        self.assertIn("ACC-PAY-2026-00009", self.summary())

    def test_an_invoice_on_hold_is_skipped(self):
        self.db.set_value("Purchase Invoice", INVOICE, "on_hold", 1)
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.created, [])
        self.assertIn("on hold", self.summary())

    def test_the_guard_is_asked_with_the_outstanding_amount(self):
        with patch.object(_pl_mod, "check_invoice_payable", return_value=None) as guard:
            _pl_mod.schedule_weekly_payments()
        guard.assert_called_once_with(INVOICE, 1500.0)

    def test_no_status_blind_lookup_is_left(self):
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(len(self.db.sql_calls), 1, "only the weekly selection may use raw SQL")
        self.cache.delete_value.assert_not_called()


class TestFailuresDoNotStopTheRun(SchedulerCase):
    def setUp(self):
        super().setUp()
        self.add_invoice(OTHER_INVOICE, mode="Pix", amount=800.0, due=TOMORROW)

    def test_a_creation_error_is_reported_and_the_loop_goes_on(self):
        self.create_error = RuntimeError("Duplicate entry 'ACC-PINV-2026-00040' for key 'invoice_lock'")
        _pl_mod.schedule_weekly_payments()
        self.assertEqual([call[0] for call in self.created], [INVOICE, OTHER_INVOICE])
        self.assertEqual(self.enqueued, ["IPO-TEST-00002"])
        summary = self.summary()
        self.assertIn("Erros: 1", summary)
        self.assertIn("invoice_lock", summary)
        self.assertIn("Na fila de execucao: 1", summary)

    def test_a_half_created_order_is_rolled_back_not_committed_by_the_next_invoice(self):
        self.create_error = RuntimeError("submit failed after insert")
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.db.get_all(DOCTYPE, pluck="name"), ["IPO-TEST-00002"])
        self.assertIn(("rollback",), self.db.events)

    def test_a_creation_error_is_written_to_the_error_log(self):
        self.create_error = RuntimeError("boom")
        _pl_mod.schedule_weekly_payments()
        self.log_error.assert_called()
        self.assertTrue(any(INVOICE in str(call) for call in self.log_error.call_args_list))

    def test_a_job_timeout_is_raised_again_not_swallowed(self):
        self.create_error = _pl_mod.JobTimeoutException("Task exceeded maximum timeout value (1500 seconds)")
        with self.assertRaises(_pl_mod.JobTimeoutException):
            _pl_mod.schedule_weekly_payments()
        self.assertEqual([call[0] for call in self.created], [INVOICE])
        self.assertEqual(self.enqueued, [])

    def test_a_job_timeout_while_queueing_is_raised_again_too(self):
        self.enqueue.side_effect = _pl_mod.JobTimeoutException("Task exceeded maximum timeout value")
        with self.assertRaises(_pl_mod.JobTimeoutException):
            _pl_mod.schedule_weekly_payments()
        self.assertEqual([call[0] for call in self.created], [INVOICE])
        # the order it had just created is committed and keeps protecting its invoice
        self.assertEqual(self.db.committed_row(DOCTYPE, "IPO-TEST-00001")["status"], "Approved")

    def test_a_job_timeout_in_the_credit_card_branch_is_raised_again_too(self):
        self.db.set_value("Payment Schedule", f"{INVOICE}-ps", "mode_of_payment", "Credit Card")
        _guards.frappe.new_doc.side_effect = _pl_mod.JobTimeoutException("timeout")
        with self.assertRaises(_pl_mod.JobTimeoutException):
            _pl_mod.schedule_weekly_payments()

    def test_the_timeout_class_is_rqs_when_rq_is_installed(self):
        try:
            from rq.timeouts import JobTimeoutException
        except ImportError:
            self.assertTrue(issubclass(_pl_mod.JobTimeoutException, Exception))
        else:
            self.assertIs(_pl_mod.JobTimeoutException, JobTimeoutException)


class FakeEntry:
    """A Payment Entry as the credit-card branch builds it."""

    def __init__(self, log):
        self.log = log
        self.name = None
        self.references = []

    def append(self, table, row):
        getattr(self, table).append(row)

    def insert(self, ignore_permissions=False):
        self.name = "ACC-PAY-2026-00077"
        self.log.append(("insert", self.name))

    def submit(self):
        self.log.append(("submit", self.name))


class TestCreditCardBranch(SchedulerCase):
    def setUp(self):
        super().setUp()
        self.db.set_value("Payment Schedule", f"{INVOICE}-ps", "mode_of_payment", "Credit Card")
        self.db.add("Inter Company Account", ACCOUNT, company=COMPANY, sync_enabled=1, bank_account="BA-INTER")
        self.db.add("Bank Account", "BA-INTER", account="1.1.1 Banco Inter - I8")
        self.db.commit()
        self.entry_log = []
        self.entry = FakeEntry(self.entry_log)
        invoice_doc = MagicMock(company=COMPANY, supplier=SUPPLIER, credit_to="2.1.1 Fornecedores - I8")
        docs = {"Purchase Invoice": invoice_doc, "Payment Entry": self.entry}
        _shadow_frappe(
            self,
            new_doc=MagicMock(return_value=self.entry),
            get_doc=MagicMock(side_effect=lambda doctype, name: docs[doctype]),
        )

    def test_draft_entry_then_submit_and_no_order(self):
        _pl_mod.schedule_weekly_payments()
        self.assertEqual(self.entry_log, [("insert", "ACC-PAY-2026-00077"), ("submit", "ACC-PAY-2026-00077")])
        self.assertEqual(self.created, [])
        self.assertEqual(self.enqueued, [])
        self.assertEqual(self.entry.mode_of_payment, "Credit Card")
        self.assertEqual(self.entry.paid_amount, 1500.0)
        self.assertEqual(self.entry.references[0]["reference_name"], INVOICE)
        self.assertIn("Cartao de credito: 1", self.summary())

    def test_the_account_comes_from_the_company_lookup(self):
        real_lookup = _guards.get_inter_account_for_company
        with patch.object(_pl_mod, "get_inter_account_for_company", wraps=real_lookup) as lookup:
            _pl_mod.schedule_weekly_payments()
        lookup.assert_called_once_with(COMPANY)
        self.assertEqual(self.entry.paid_from, "1.1.1 Banco Inter - I8")

    def test_an_account_of_another_company_is_not_used(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "company", "Outra Empresa")
        _pl_mod.schedule_weekly_payments()
        self.assertFalse(hasattr(self.entry, "paid_from"))


class TestSummary(unittest.TestCase):
    def setUp(self):
        self.messages = []
        _start(self, patch.object(_pl_mod, "_notify_telegram", side_effect=self.messages.append))

    def results(self):
        return [
            {"status": "queued", "invoice": "PINV-1", "supplier": "Fornecedor A", "amount": 1500.0,
             "due_date": "2026-09-22", "method": "PIX", "payment_order": "IPO-1"},
            {"status": "pending_approval", "invoice": "PINV-2", "supplier": "Fornecedor B", "amount": 800.0,
             "due_date": "2026-09-23", "method": "Boleto", "payment_order": "IPO-2",
             "order_status": "Pending Approval"},
            {"status": "credit_card", "invoice": "PINV-3", "supplier": "Fornecedor C", "amount": 99.9,
             "payment_entry": "ACC-PAY-1"},
            {"status": "skipped", "invoice": "PINV-4", "reason": "Purchase Invoice PINV-4 is on hold"},
            {"status": "error", "invoice": "PINV-5", "error": "Supplier has no PIX key"},
        ]

    def test_the_five_sections_in_order(self):
        _pl_mod._send_payment_summary(self.results())
        text = self.messages[0]
        titles = ["Na fila de execucao: 1", "Aguardando aprovacao no ERP: 1", "Cartao de credito: 1",
                  "Ignorados: 1", "Erros: 1"]
        positions = [text.find(title) for title in titles]
        self.assertNotIn(-1, positions, text)
        self.assertEqual(positions, sorted(positions))
        for fragment in ("PINV-1", "IPO-1", "PINV-2", "IPO-2", "Pending Approval", "PINV-3", "PINV-4",
                         "is on hold", "PINV-5", "Supplier has no PIX key"):
            self.assertIn(fragment, text)

    def test_empty_sections_are_left_out(self):
        _pl_mod._send_payment_summary(self.results()[:1])
        text = self.messages[0]
        for title in ("Aguardando aprovacao", "Cartao de credito", "Ignorados", "Erros"):
            self.assertNotIn(title, text)

    def test_nothing_at_all_says_so(self):
        _pl_mod._send_payment_summary([])
        self.assertIn("Nenhum pagamento", self.messages[0])

    def test_a_long_reason_keeps_the_document_that_blocks(self):
        reason = "Inter Payment Order IPO-2026-00002 (Needs Verification) already covers Purchase Invoice PINV-4"
        _pl_mod._send_payment_summary([{"status": "skipped", "invoice": "PINV-4", "reason": reason}])
        self.assertIn("IPO-2026-00002 (Needs Verification)", self.messages[0])


# ---------------------------------------------------------------------------
# The three NOT EXISTS guards, executed for real on sqlite
# ---------------------------------------------------------------------------

class SqliteDB(FakeDB):
    """``frappe.db.sql`` served by an in-memory sqlite with the four tables the guards read."""

    SCHEMA = """
        CREATE TABLE `tabPurchase Invoice`(name, supplier, supplier_name, docstatus, outstanding_amount, due_date);
        CREATE TABLE `tabPayment Entry`(name, docstatus);
        CREATE TABLE `tabPayment Entry Reference`(parent, reference_doctype, reference_name);
        CREATE TABLE `tabInter Payment Order`(name, purchase_invoice, docstatus, status, payment_entry);
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.lite = sqlite3.connect(":memory:")
        self.lite.row_factory = sqlite3.Row
        self.lite.executescript(self.SCHEMA)

    def sql(self, query, values=None, as_dict=False, **kw):
        self.sql_calls.append(((query, values), {"as_dict": as_dict, **kw}))
        if values is None:
            values = ()
        elif not isinstance(values, (tuple, list)):
            values = (values,)
        rows = self.lite.execute(query.replace("%s", "?"), tuple(values)).fetchall()
        return [dict(row) for row in rows] if as_dict else [tuple(row) for row in rows]

    def invoice(self, name, due=TODAY, docstatus=1, outstanding=100.0):
        self.lite.execute(
            "INSERT INTO `tabPurchase Invoice` VALUES (?, ?, ?, ?, ?, ?)",
            (name, SUPPLIER, "Fornecedor A", docstatus, outstanding, due.isoformat()),
        )

    def order(self, invoice, status, docstatus=1, payment_entry=None):
        self.lite.execute(
            "INSERT INTO `tabInter Payment Order` VALUES (?, ?, ?, ?, ?)",
            (f"IPO-{invoice}-{status}", invoice, docstatus, status, payment_entry),
        )

    def payment_entry(self, invoice, docstatus, reference_doctype="Purchase Invoice"):
        name = f"PE-{invoice}"
        self.lite.execute("INSERT INTO `tabPayment Entry` VALUES (?, ?)", (name, docstatus))
        self.lite.execute(
            "INSERT INTO `tabPayment Entry Reference` VALUES (?, ?, ?)", (name, reference_doctype, invoice)
        )


FREE = ("PI-FREE", "PI-FAILED", "PI-CANCELLED", "PI-COMPLETED-WITH-ENTRY", "PI-CANCELLED-ENTRY")
BLOCKED = (
    "PI-DRAFT-ORDER", "PI-PENDING", "PI-APPROVED", "PI-PROCESSING", "PI-AWAITING", "PI-NEEDS-VERIFICATION",
    "PI-COMPLETED-NO-ENTRY", "PI-COMPLETED-EMPTY-ENTRY", "PI-FAILED-THEN-AWAITING", "PI-NULL-STATUS",
    "PI-DRAFT-ENTRY",
)


def _populate(db: SqliteDB) -> None:
    for name in FREE + BLOCKED:
        db.invoice(name)
    db.order("PI-FAILED", "Failed")
    db.order("PI-CANCELLED", "Cancelled", docstatus=2)
    db.order("PI-COMPLETED-WITH-ENTRY", "Completed", payment_entry="ACC-PAY-1")
    db.payment_entry("PI-CANCELLED-ENTRY", docstatus=2)
    db.order("PI-DRAFT-ORDER", "Draft", docstatus=0)
    db.order("PI-PENDING", "Pending Approval")
    db.order("PI-APPROVED", "Approved")
    db.order("PI-PROCESSING", "Processing")
    db.order("PI-AWAITING", "Awaiting Bank")
    db.order("PI-NEEDS-VERIFICATION", "Needs Verification")
    db.order("PI-COMPLETED-NO-ENTRY", "Completed", payment_entry=None)
    db.order("PI-COMPLETED-EMPTY-ENTRY", "Completed", payment_entry="")
    db.order("PI-FAILED-THEN-AWAITING", "Failed")
    db.order("PI-FAILED-THEN-AWAITING", "Awaiting Bank")
    db.order("PI-NULL-STATUS", None)
    db.payment_entry("PI-DRAFT-ENTRY", docstatus=0)


class SqlGuardCase(unittest.TestCase):
    def setUp(self):
        self.db = SqliteDB(singles={
            "I8 Agent Settings": {"enabled": 1, "auto_schedule_payments": 1},
            "Banco Inter Settings": {"enabled": 1},
        })
        _populate(self.db)
        cache = MagicMock()
        cache.get_value.return_value = None
        _shadow_frappe(self, db=self.db, get_all=self.db.get_all, cache=cache, log_error=MagicMock())
        self.messages = []
        _start(self, patch.object(_pl_mod, "_notify_telegram", side_effect=self.messages.append))
        _start(self, patch.dict(sys.modules, {"brazil_module.services.intelligence.notifications": MagicMock()}))

    def assert_free_and_blocked(self, selected):
        self.assertEqual(sorted(selected), sorted(FREE))
        self.assertEqual(set(selected) & set(BLOCKED), set())

    def statement(self):
        self.assertEqual(len(self.db.sql_calls), 1)
        return self.db.sql_calls[0][0][0]

    def assert_uses_the_blocking_rule(self, statement):
        self.assertIn("NOT IN ('Failed', 'Cancelled')", statement)
        self.assertIn("'Completed'", statement)
        self.assertIn("payment_entry", statement)


class TestWeeklySelectionSql(SqlGuardCase):
    def test_selects_exactly_the_invoices_no_blocking_order_protects(self):
        picked = []

        def pick(inv):
            picked.append(inv["name"])
            return {"status": "skipped", "invoice": inv["name"], "reason": "test"}

        with patch.object(_pl_mod, "_schedule_single_payment", side_effect=pick):
            _pl_mod.schedule_weekly_payments()
        self.assert_free_and_blocked(picked)
        self.assert_uses_the_blocking_rule(self.statement())


class TestOverdueAlertSql(SqlGuardCase):
    def test_a_failed_order_does_not_silence_the_alert_and_an_order_in_flight_does(self):
        _pl_mod.check_overdue_payments()
        self.assertEqual(len(self.messages), 1)
        mentioned = [name for name in FREE + BLOCKED if f"- {name}:" in self.messages[0]]
        self.assert_free_and_blocked(mentioned)
        self.assert_uses_the_blocking_rule(self.statement())


class TestUrgentAlertSql(SqlGuardCase):
    def test_a_failed_order_does_not_silence_the_alert_and_an_order_in_flight_does(self):
        _pl_mod.check_urgent_payments()
        self.assertEqual(len(self.messages), 1)
        mentioned = [name for name in FREE + BLOCKED if f"- {name}:" in self.messages[0]]
        self.assert_free_and_blocked(mentioned)
        self.assert_uses_the_blocking_rule(self.statement())


class TestSqlGuardAgreesWithIsBlocking(unittest.TestCase):
    """The SQL fragment and ``payment_guards.is_blocking`` are one rule written twice."""

    def test_every_status_with_and_without_a_payment_entry(self):
        for status in (*ALL_STATUSES, None, "Something New"):
            for payment_entry in (None, "", "ACC-PAY-1"):
                with self.subTest(status=status, payment_entry=payment_entry):
                    db = SqliteDB()
                    db.invoice("PI-X")
                    db.order("PI-X", status, docstatus=1, payment_entry=payment_entry)
                    rows = db.sql(
                        "SELECT pi.name FROM `tabPurchase Invoice` pi WHERE 1 = 1 " + _pl_mod._NO_PAYMENT_UNDER_WAY_SQL
                    )
                    self.assertEqual(rows == [], _guards.is_blocking(status, payment_entry))

    def test_only_a_reference_to_a_purchase_invoice_counts(self):
        """Names are unique per doctype, not across them: an Expense Claim called ACC-PINV-... is
        a different document, and must not make the invoice look like it is already being paid."""
        db = SqliteDB()
        db.invoice("PI-X")
        db.payment_entry("PI-X", docstatus=1, reference_doctype="Expense Claim")

        rows = db.sql(
            "SELECT pi.name FROM `tabPurchase Invoice` pi WHERE 1 = 1 " + _pl_mod._NO_PAYMENT_UNDER_WAY_SQL
        )

        self.assertEqual(rows, [("PI-X",)], "the invoice is still free to pay")

    def test_the_fragment_is_built_from_the_shared_constant(self):
        quoted = ", ".join(f"'{status}'" for status in _guards.NON_BLOCKING_STATUSES)
        self.assertIn(f"NOT IN ({quoted})", _pl_mod._NO_PAYMENT_UNDER_WAY_SQL)


if __name__ == "__main__":
    unittest.main()
