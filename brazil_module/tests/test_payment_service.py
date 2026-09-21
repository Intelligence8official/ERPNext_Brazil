"""Tests for the single outbound payment path (spec 4.4, invariants I1-I9).

State matters here, so nothing runs on a bare ``MagicMock``: the database is a ``FakeDB`` (with a
committed copy), the bank is a ``FakeInterClient`` (it asserts I2 on every send) and every loaded
document is a ``FakeSubmittedDoc`` (its ``save`` raises where Frappe v15's does).
"""

import datetime
import inspect
import json
import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from brazil_module.tests._payment_fakes import (
    DOCTYPE,
    FakeDB,
    FakeInterClient,
    FakeSubmittedDoc,
    install_frappe_mock,
)

install_frappe_mock()

# test_webhook_handler.py parks MagicMock placeholders for these modules when it is imported first;
# the exception classes must be the real ones, or no ``except`` clause of the service can match.
for _name in (
    "brazil_module.services.banking.auth_manager",
    "brazil_module.services.banking.inter_client",
    "brazil_module.services.banking.payment_service",
):
    if isinstance(sys.modules.get(_name), MagicMock):
        del sys.modules[_name]

import brazil_module.hooks as _hooks
import brazil_module.services.banking.inter_client as _ic_mod
import brazil_module.services.banking.payment_guards as _guards
import brazil_module.services.banking.payment_service as _ps_mod

NOW = datetime.datetime(2026, 9, 20, 10, 0, 0)
TODAY = NOW.date()
ORDER = "IPO-2026-00002"
INVOICE = "ACC-PINV-2026-00031"
COMPANY = "Intelligence8"
ACCOUNT = "Inter - I8"
BANK_GL = "1.1.1 Banco Inter - I8"
PAYABLE_GL = "2.1.1 Fornecedores - I8"
PIX_ID = "c42f0787-02cb-4b31-827e-459ec9d7ece1"
BOLETO_ID = "8bbdede4-35db-4ec9-b652-e176841e62c8"
BARCODE = "07797000000000000004501008460019310001802680"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ALL_STATUSES = (
    "Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
    "Needs Verification", "Completed", "Failed", "Cancelled",
)
PIX_STATUSES = {
    "Completed": ("PAGO", "PIX_PAGO"),
    "Failed": ("REPROVADO", "EXPIRADO", "CANCELADO", "CANCELADO_SEM_SALDO", "AGENDAMENTO_CANCELADO"),
    "Needs Verification": ("FALHA", "NAO_DEBITADO"),
    "Awaiting Bank": (
        "CRIADO", "AGUARDANDO_APROVACAO", "APROVADO", "AGENDADO", "ENVIADO", "DEBITADO",
        "PARCIALMENTE_DEBITADO", "PARCIALMENTE_PAGO", "TRANSACAO_CRIADA", "PIX_ENVIADO", "SOMETHING_NEW",
    ),
}
BOLETO_STATUSES = {
    "Completed": ("REALIZADO", "PAGO", "AGENDADO_REALIZADO"),
    "Failed": (
        "CANCELADO", "AGENDADO_CANCELADO", "ERRO", "ERRO_PAGAMENTO", "APROVACAO_EXPIRADA",
        "REPROVADO", "NAO_COMPENSADO", "AGENDADO_NAO_REALIZADO",
    ),
    "Awaiting Bank": ("EMPROCESSAMENTO", "AGUARDANDO_APROVACAO", "APROVADO", "AGENDADO", "SOMETHING_NEW"),
}
_MISSING = object()


class Thrown(Exception):
    """What ``frappe.throw`` raises here (on the bare mock it would return and the code go on)."""


class FakeJobTimeout(Exception):
    """``rq.timeouts.JobTimeoutException`` is a plain ``Exception`` subclass too."""


def _throw(message, *args, **kwargs):
    raise Thrown(str(message))


def _flt(value, precision=None):
    number = float(value or 0)
    return round(number, precision) if precision is not None else number


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
        return datetime.datetime.combine(value, datetime.time())
    return datetime.datetime.fromisoformat(str(value))


def _shadow_frappe(test_case, **attributes):
    """``patch_frappe`` for every frappe mock the modules under test hold (normally one)."""
    holders = {id(module.frappe): module.frappe for module in (_ps_mod, _guards)}
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


class RecordingDoc(FakeSubmittedDoc):
    """A loaded document whose timeline comments land in the shared event log."""

    def add_comment(self, comment_type="Comment", text=None, **kw):
        super().add_comment(comment_type, text, **kw)
        self._db.events.append(("comment", self.name, text))

    def notify_update(self):
        super().notify_update()
        self._db.events.append(("notify", self.name))


class FakePaymentEntry:
    """A Payment Entry as the service builds it; ``insert`` and ``submit`` write to the FakeDB."""

    def __init__(self, db, name=None, fail_on=None):
        self._db = db
        self._fail_on = fail_on
        self.name = name
        self.flags = SimpleNamespace(ignore_permissions=False)
        self.references = []
        self.inter_payment_order = None

    def append(self, table, row):
        getattr(self, table).append(row)

    def insert(self, ignore_permissions=False):
        self._maybe_fail("insert")
        self.flags.ignore_permissions = ignore_permissions
        self.name = f"ACC-PAY-2026-{len(self._db.get_all('Payment Entry')) + 1:05d}"
        # Straight into the working copy: FakeDB.add() would also commit, and a rollback must undo this.
        self._db._working.setdefault("Payment Entry", {})[self.name] = {
            "name": self.name, "docstatus": 0, "inter_payment_order": self.inter_payment_order,
        }
        self._db.events.append(("insert", "Payment Entry", self.name))
        return self

    def submit(self):
        self._maybe_fail("submit")
        self._db.row("Payment Entry", self.name)["docstatus"] = 1
        self._db.events.append(("submit", "Payment Entry", self.name))

    def _maybe_fail(self, step):
        if self._fail_on == step:
            raise RuntimeError(f"Payment Entry {step} refused: accounting period is closed")


def _fixture_lock(row: dict, docstatus: int):
    """Which invoice a fixture order holds, restated here on purpose.

    Deriving it from ``payment_guards.is_blocking`` would make the fixture move with the very rule
    the tests exist to pin: break the rule and the fixture would break the same way, in silence.
    """
    if docstatus >= 2 or row["status"] in ("Failed", "Cancelled"):
        return None
    if row["status"] == "Completed" and row["payment_entry"]:
        return None
    return row["purchase_invoice"]


class ServiceCase(unittest.TestCase):
    """A payable invoice, an enabled integration, an Inter account and a fake bank."""

    payment_type = "PIX"

    def setUp(self):
        self.db = FakeDB(singles={"Banco Inter Settings": {"enabled": 1}}, now=lambda: NOW)
        self.add_invoice(INVOICE)
        self.db.add("Supplier", "SUP-1", on_hold=0, hold_type=None, supplier_name="Fornecedor Ltda", tax_id="1")
        self.db.add("Inter Company Account", ACCOUNT, company=COMPANY, sync_enabled=1, bank_account="Inter - BA")
        self.db.add("Bank Account", "Inter - BA", account=BANK_GL)
        self.client = FakeInterClient(self.db, doctype_row=(DOCTYPE, ORDER))
        self.client_accounts = []
        self.alerts = []
        self.entries = []
        self.entry_fails_on = None
        self.enqueue = MagicMock(name="frappe.enqueue")
        self.logger = MagicMock(name="frappe.logger()")
        self.log_error = MagicMock(name="frappe.log_error")
        _shadow_frappe(
            self, db=self.db, get_all=self.db.get_all, get_doc=self._get_doc, new_doc=self._new_doc,
            throw=_throw, enqueue=self.enqueue, log_error=self.log_error,
            logger=MagicMock(return_value=self.logger), session=SimpleNamespace(user="abel@intelligence8.com"),
            # Real date helpers behind ``frappe.utils.<name>`` too: the old hourly re-send computed the
            # age of an order through them, and the cron matrix must be able to catch that defect.
            utils=SimpleNamespace(get_datetime=_get_datetime, getdate=_getdate, flt=_flt, now_datetime=lambda: NOW),
        )
        self._patch(_guards, _=lambda text: text, flt=_flt)
        # Safety net: no code path of this file may ever build the real HTTP client.
        self._patch(_ic_mod, InterAPIClient=self._client_for)
        self._patch(
            _ps_mod, _=lambda text: text, flt=_flt, getdate=_getdate, get_datetime=_get_datetime,
            now_datetime=lambda: NOW, alert_operator=self._alert, InterAPIClient=self._client_for,
        )

    # -- plumbing -------------------------------------------------------------------

    def _patch(self, module, **attributes):
        for name, value in attributes.items():
            patcher = patch.object(module, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _alert(self, subject, message, order_name=None):
        self.alerts.append((subject, message, order_name))
        self.db.events.append(("alert", order_name, subject))

    def _client_for(self, account):
        self.client_accounts.append(account)
        return self.client

    def _get_doc(self, doctype, name=None):
        if doctype == "Payment Entry":
            entry = FakePaymentEntry(self.db, name=name, fail_on=self.entry_fails_on)
            entry.inter_payment_order = self.db.row("Payment Entry", name).get("inter_payment_order")
            self.entries.append(entry)
            return entry
        return RecordingDoc(self.db, name, doctype)

    def _new_doc(self, doctype):
        if doctype != "Payment Entry":
            raise AssertionError(f"the service is not expected to create a {doctype}")
        entry = FakePaymentEntry(self.db, fail_on=self.entry_fails_on)
        self.entries.append(entry)
        return entry

    # -- fixtures -------------------------------------------------------------------

    def add_invoice(self, name, outstanding=16800.0, **fields):
        row = {
            "docstatus": 1, "on_hold": 0, "outstanding_amount": outstanding, "supplier": "SUP-1",
            "company": COMPANY, "credit_to": PAYABLE_GL, "due_date": datetime.date(2026, 9, 25),
        }
        self.db.add("Purchase Invoice", name, **{**row, **fields})

    def add_order(self, status="Approved", name=ORDER, docstatus=1, **fields):
        row = {
            "status": status, "docstatus": docstatus, "payment_type": self.payment_type, "company": COMPANY,
            "inter_company_account": ACCOUNT, "purchase_invoice": INVOICE, "party_type": "Supplier",
            "party": "SUP-1", "amount": 16800.0, "pix_key": "pix@fornecedor.com.br", "barcode": BARCODE,
            "boleto_due_date": datetime.date(2026, 9, 25), "scheduled_date": None, "idempotency_key": None,
            "bank_status": None, "bank_request_at": None, "transaction_id": None, "approval_code": None,
            "execution_date": None, "inter_response": None, "payment_entry": None,
        }
        row.update(fields)
        if "invoice_lock" not in row:
            row["invoice_lock"] = _fixture_lock(row, docstatus)
        self.db.add(DOCTYPE, name, **row)

    def add_in_flight(self, status, name=ORDER, minutes_ago=1, **fields):
        bank_id = PIX_ID if self.payment_type == "PIX" else BOLETO_ID
        defaults = {
            "idempotency_key": "0f8fad5b-d9cb-469f-a165-70867728950e",
            "bank_request_at": NOW - datetime.timedelta(minutes=minutes_ago),
            "modified": NOW - datetime.timedelta(minutes=minutes_ago),
            "approval_code": bank_id if status == "Awaiting Bank" else None,
        }
        self.add_order(status, name=name, **{**defaults, **fields})

    # -- inspection -----------------------------------------------------------------

    def order(self, name=ORDER):
        return self.db.committed_row(DOCTYPE, name)

    def events(self, *kinds):
        return [event for event in self.db.events if event[0] in kinds]

    def sends(self):
        return [event for event in self.events("bank") if event[1] in ("send_pix", "pay_barcode")]

    def bank_calls(self, method):
        return [event for event in self.events("bank") if event[1] == method]

    def order_writes(self, name=ORDER):
        return [event for event in self.events("set_value") if event[1] == DOCTYPE and event[2] == name]

    def comments(self, name=ORDER):
        return [event[2] for event in self.events("comment") if event[1] == name]

    def snapshot(self, name=ORDER):
        return dict(self.db.row(DOCTYPE, name))

    def execute(self, **kwargs):
        return _ps_mod.execute_payment_order(ORDER, **kwargs)


class BoletoCase(ServiceCase):
    payment_type = "Boleto Payment"


# ---------------------------------------------------------------------------
# Compare-and-set core (I5, I9)
# ---------------------------------------------------------------------------

class TestCompareAndSet(ServiceCase):
    def _marks(self):
        return {
            "awaiting": lambda: _ps_mod.mark_awaiting_bank(
                ORDER, expected_from=("Processing",), bank_id=PIX_ID, bank_status="AGUARDANDO_APROVACAO",
            ),
            "completed": lambda: _ps_mod.mark_completed(ORDER, expected_from=("Processing",), transaction_id="E2E"),
            "failed": lambda: _ps_mod.mark_failed(ORDER, "rejected by the bank", expected_from=("Processing",)),
            "verify": lambda: _ps_mod.mark_needs_verification(ORDER, "read timeout", expected_from=("Processing",)),
        }

    def test_mark_failed_on_a_completed_order_writes_nothing(self):
        self.add_order("Completed")

        self.assertIs(_ps_mod.mark_failed(ORDER, "too late", expected_from=("Processing",)), False)

        self.assertEqual(self.events("set_value", "comment", "alert"), [])
        self.assertEqual(self.order()["status"], "Completed")
        self.assertEqual(self.order()["invoice_lock"], INVOICE)
        self.assertEqual(self.db.events[-1], ("commit",))  # only the Error Log of the lost transition
        self.assertIn(("rollback",), self.db.events)  # the row lock is released

    def test_mark_completed_expecting_awaiting_bank_refuses_needs_verification(self):
        self.add_in_flight("Needs Verification")

        wrote = _ps_mod.mark_completed(ORDER, expected_from=("Awaiting Bank",), transaction_id="E2E")

        self.assertIs(wrote, False)
        self.assertEqual(self.events("set_value", "insert"), [])
        self.assertEqual(self.order()["status"], "Needs Verification")

    def test_a_lost_transition_is_recorded_in_the_error_log(self):
        self.add_order("Completed")

        _ps_mod.mark_needs_verification(ORDER, "read timeout", expected_from=("Processing",))

        self.log_error.assert_called_once()
        self.assertIn(ORDER, str(self.log_error.call_args))
        self.assertIn("Completed", str(self.log_error.call_args))

    def test_an_order_that_is_not_submitted_is_never_transitioned(self):
        for docstatus in (0, 2):
            with self.subTest(docstatus=docstatus):
                name = f"IPO-DOCSTATUS-{docstatus}"
                self.add_order("Processing", name=name, docstatus=docstatus, invoice_lock=None)

                self.assertIs(_ps_mod.mark_failed(name, "x", expected_from=("Processing",)), False)
                self.assertEqual(self.order(name)["status"], "Processing")

    def test_a_missing_order_is_not_a_transition(self):
        self.assertIs(_ps_mod.mark_failed("IPO-404", "x", expected_from=("Processing",)), False)
        self.assertEqual(self.events("set_value"), [])

    def test_every_mark_reads_the_row_under_a_lock(self):
        for label, mark in self._marks().items():
            with self.subTest(mark=label):
                self.setUp()
                self.add_in_flight("Processing")
                mark()
                lock = self.db.events.index(("get_value", DOCTYPE, ORDER, True))
                self.assertLess(lock, self.db.events.index(self.order_writes()[0]))
                read = next(r for r in self.db.reads if r["doctype"] == DOCTYPE and r["for_update"])
                self.assertEqual(read["fields"][:3], ["status", "docstatus", "payment_entry"])

    def test_the_state_is_committed_before_anything_else_and_nothing_is_left_pending(self):
        for label, mark in self._marks().items():
            with self.subTest(mark=label):
                self.setUp()
                self.add_in_flight("Processing", purchase_invoice=None)  # no Payment Entry work in the way
                self.assertIs(mark(), True)
                events = self.db.events
                write = events.index(self.order_writes()[0])
                self.assertEqual(events[write + 1], ("commit",), "the state must be committed at once")
                effects = [i for i, e in enumerate(events) if e[0] in ("comment", "alert", "notify")]
                self.assertTrue(effects, "a transition leaves a timeline comment")
                self.assertGreater(min(effects), write + 1)
                self.assertIn(("commit",), events[max(effects) + 1:], "a final commit leaves nothing pending")

    def test_what_each_mark_writes(self):
        self.add_in_flight("Processing")
        _ps_mod.mark_awaiting_bank(
            ORDER, expected_from=("Processing",), bank_id=PIX_ID, bank_status="AGUARDANDO_APROVACAO",
            response={"tipoRetorno": "APROVACAO", "codigoSolicitacao": PIX_ID},
        )
        row = self.order()
        self.assertEqual((row["status"], row["approval_code"], row["bank_status"]),
                         ("Awaiting Bank", PIX_ID, "AGUARDANDO_APROVACAO"))
        self.assertEqual(json.loads(row["inter_response"])["codigoSolicitacao"], PIX_ID)
        self.assertEqual(row["invoice_lock"], INVOICE)
        self.assertEqual(self.alerts, [], "waiting for the bank is not an incident")

    def test_mark_failed_frees_the_invoice_and_alerts(self):
        self.add_in_flight("Processing")

        _ps_mod.mark_failed(ORDER, "HTTP 422 saldo insuficiente", expected_from=("Processing",),
                            bank_status="REPROVADO", response={"title": "saldo insuficiente"})

        row = self.order()
        self.assertEqual((row["status"], row["invoice_lock"], row["bank_status"]), ("Failed", None, "REPROVADO"))
        self.assertIn("saldo insuficiente", row["inter_response"])
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("HTTP 422 saldo insuficiente", self.alerts[0][1])
        self.assertEqual(self.alerts[0][2], ORDER)

    def test_mark_failed_without_a_bank_answer_keeps_the_reason(self):
        self.add_in_flight("Processing")

        _ps_mod.mark_failed(ORDER, "Purchase Invoice is on hold", expected_from=("Processing",))

        self.assertIn("Purchase Invoice is on hold", self.order()["inter_response"])

    def test_mark_needs_verification_keeps_protecting_the_invoice_and_alerts(self):
        self.add_in_flight("Processing")

        _ps_mod.mark_needs_verification(ORDER, "read timeout", expected_from=("Processing",))

        row = self.order()
        self.assertEqual((row["status"], row["invoice_lock"]), ("Needs Verification", INVOICE))
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("read timeout", self.alerts[0][1])
        self.assertIn("read timeout", self.comments()[0])

    def test_mark_completed_stamps_the_execution_and_settles_the_invoice(self):
        self.add_in_flight("Awaiting Bank")

        _ps_mod.mark_completed(ORDER, expected_from=("Awaiting Bank",), transaction_id="E2E-1", bank_status="PAGO")

        row = self.order()
        self.assertEqual((row["status"], row["transaction_id"], row["bank_status"]), ("Completed", "E2E-1", "PAGO"))
        self.assertEqual(row["execution_date"], NOW)
        self.assertEqual(row["approval_code"], PIX_ID, "the bank id is never overwritten")
        self.assertEqual(row["payment_entry"], self.entries[0].name)

    def test_modified_is_always_bumped(self):
        self.add_in_flight("Processing")
        before = self.order()["modified"]

        _ps_mod.mark_needs_verification(ORDER, "read timeout", expected_from=("Processing",))

        self.assertGreater(self.order()["modified"], before)  # I5: check_if_latest protects cancel
        self.assertNotIn("update_modified", inspect.getsource(_ps_mod))

    def test_a_failing_side_effect_never_undoes_or_hides_the_transition(self):
        self.add_in_flight("Processing")
        _shadow_frappe(self, get_doc=MagicMock(side_effect=RuntimeError("comment table is locked")))
        self._patch(_ps_mod, alert_operator=MagicMock(side_effect=RuntimeError("telegram is down")))

        wrote = _ps_mod.mark_needs_verification(ORDER, "read timeout", expected_from=("Processing",))

        self.assertIs(wrote, True)
        self.assertEqual(self.order()["status"], "Needs Verification")


# ---------------------------------------------------------------------------
# Claim (I2, I3), kill switch (I8), stale requests
# ---------------------------------------------------------------------------

class TestClaim(ServiceCase):
    def test_an_approved_order_is_sent_exactly_once_after_the_committed_claim(self):
        self.add_order("Approved")

        result = self.execute()

        self.assertEqual(result["status"], "awaiting_bank")
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(self.client.i2_violations, [])
        events = self.db.events
        lock = events.index(("get_value", DOCTYPE, ORDER, True))
        claim = next(i for i, e in enumerate(events) if e[0] == "set_value" and e[3].get("status") == "Processing")
        send = events.index(self.sends()[0])
        self.assertLess(lock, claim)
        self.assertIn(("commit",), events[claim + 1:send], "I2: the claim is committed before the request")

    def test_the_claim_reads_the_new_columns_and_writes_the_intent(self):
        self.add_order("Approved", transaction_id="stale", approval_code="stale", bank_status="stale",
                       execution_date=NOW, inter_response="{}")

        self.execute()

        read = next(r for r in self.db.reads if r["doctype"] == DOCTYPE and r["for_update"])
        for column in ("status", "docstatus", "idempotency_key", "bank_request_at", "bank_status", "invoice_lock"):
            self.assertIn(column, read["fields"])  # an un-migrated schema fails here, before any send
        claim = next(e[3] for e in self.order_writes() if e[3].get("status") == "Processing")
        self.assertRegex(claim["idempotency_key"], UUID_RE)
        self.assertEqual(claim["bank_request_at"], NOW)
        for cleared in ("transaction_id", "approval_code", "bank_status", "execution_date", "inter_response"):
            self.assertIsNone(claim[cleared])
        self.assertEqual(self.client.calls[0][2], claim["idempotency_key"])

    def test_a_second_execution_of_the_same_order_sends_nothing(self):
        self.add_order("Approved")
        self.execute()

        second = self.execute()

        self.assertEqual(second["status"], "skipped")
        self.assertEqual(len(self.sends()), 1)

    def test_only_a_submitted_approved_order_can_be_claimed(self):
        """The rows carry NO idempotency key: status and docstatus alone must refuse the claim."""
        cases = [(status, 1) for status in ALL_STATUSES if status != "Approved"] + [("Approved", 0), ("Approved", 2)]
        for status, docstatus in cases:
            with self.subTest(status=status, docstatus=docstatus):
                self.setUp()
                self.add_order(status, docstatus=docstatus, invoice_lock=None)
                before = self.snapshot()

                result = self.execute()

                self.assertEqual(result["status"], "skipped")
                self.assertEqual(self.events("bank", "set_value"), [])
                self.assertEqual(self.snapshot(), before)
                self.assertIn(("rollback",), self.db.events)  # the row lock is released

    def test_a_missing_order_is_skipped(self):
        self.assertEqual(_ps_mod.execute_payment_order("IPO-404")["status"], "skipped")
        self.assertEqual(self.events("bank", "set_value"), [])

    def test_an_approved_order_that_was_already_claimed_once_is_never_sent_again(self):
        """I3: nothing moves an order back to Approved - if something did, the old key gives it away."""
        self.add_order("Approved", idempotency_key="0f8fad5b-d9cb-469f-a165-70867728950e")

        result = self.execute()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self.events("bank", "set_value"), [])
        self.assertEqual(len(self.alerts), 1)

    def test_the_kill_switch_blocks_the_execution_without_touching_the_order(self):
        self.add_order("Approved")
        self.db.singles["Banco Inter Settings"]["enabled"] = 0

        result = self.execute()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.events("bank", "set_value", "commit"), [])
        self.assertEqual(self.order()["status"], "Approved")

    def test_a_request_that_waited_more_than_15_minutes_is_not_executed(self):
        self.add_order("Approved")
        requested_at = str(NOW - datetime.timedelta(minutes=16))

        result = self.execute(requested_at=requested_at)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self.events("bank", "set_value"), [])
        self.assertEqual(self.order()["status"], "Approved")
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0][2], ORDER)

    def test_a_recent_request_is_executed(self):
        self.add_order("Approved")

        result = self.execute(requested_at=str(NOW - datetime.timedelta(minutes=14)))

        self.assertEqual(result["status"], "awaiting_bank")
        self.assertEqual(len(self.sends()), 1)

    def test_an_unreadable_request_time_is_treated_as_stale(self):
        self.add_order("Approved")

        self.assertEqual(self.execute(requested_at="yesterday-ish")["status"], "skipped")
        self.assertEqual(self.events("bank", "set_value"), [])


# ---------------------------------------------------------------------------
# Pre-send guards: nothing was sent, so Failed is the truth
# ---------------------------------------------------------------------------

class TestPreSendGuards(ServiceCase):
    def assert_failed_without_a_send(self, result, reason_part):
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.events("bank"), [])
        row = self.order()
        self.assertEqual((row["status"], row["invoice_lock"]), ("Failed", None))
        self.assertIn(reason_part, row["inter_response"])
        self.assertIn(reason_part, result["message"])
        self.assertEqual(len(self.alerts), 1)

    def test_an_invoice_that_is_no_longer_payable(self):
        self.add_order("Approved")
        self.db.set_value("Purchase Invoice", INVOICE, "on_hold", 1)
        self.db.commit()

        self.assert_failed_without_a_send(self.execute(), "is on hold")

    def test_another_blocking_order_for_the_same_invoice(self):
        self.add_order("Approved", invoice_lock=None)
        self.add_in_flight("Needs Verification", name="IPO-2026-00001")

        self.assert_failed_without_a_send(self.execute(), "IPO-2026-00001")

    def test_a_certificate_that_cannot_be_read(self):
        """A missing certificate opens no socket, so it must fail - not freeze the invoice.

        The client resolves it inside the request, where a plain OSError would land in the
        "the request may be at the bank" branch and leave a person checking a statement for a
        payment that was never attempted.
        """
        self.add_order("Approved")
        self.client.responses["get_cert_paths"] = FileNotFoundError(
            "Could not resolve file path: /private/files/inter.crt"
        )

        self.assert_failed_without_a_send(self.execute(), "certificate")

    def test_a_pre_send_failure_does_not_overwrite_an_order_that_left_processing(self):
        """The compare-and-set earns its keep here: stamping Failed would free a live invoice.

        Another worker (or the operator resolving the verification) can move the row while this job
        is between its claim and its guards. Writing ``Failed`` blindly would also clear
        ``invoice_lock`` - on an order the bank may be holding a payment for.
        """
        self.add_order("Approved")

        def moved_then_refused(*args, **kwargs):
            self.db.set_value(DOCTYPE, ORDER, {"status": "Awaiting Bank"})
            self.db.commit()
            return "Purchase Invoice ACC-PINV-2026-00031 is on hold"

        self._patch(_ps_mod, check_invoice_payable=moved_then_refused)

        result = self.execute()

        self.assertEqual(result["status"], "skipped")
        row = self.order()
        self.assertEqual((row["status"], row["invoice_lock"]), ("Awaiting Bank", INVOICE))
        self.assertEqual(self.events("bank"), [])
        locked = [read for read in self.db.reads if read["doctype"] == DOCTYPE and read["for_update"]]
        self.assertTrue(locked, "the refusal must come from a locked re-read, not from memory")

    def test_an_order_without_an_invoice_skips_the_invoice_guard(self):
        self.add_order("Approved", purchase_invoice=None)

        self.assertEqual(self.execute()["status"], "awaiting_bank")

    def test_a_missing_inter_account(self):
        self.add_order("Approved", inter_company_account="Inter - Gone")

        self.assert_failed_without_a_send(self.execute(), "Inter - Gone")

    def test_an_inter_account_of_another_company(self):
        self.add_order("Approved")
        self.db.set_value("Inter Company Account", ACCOUNT, "company", "Other Co")
        self.db.commit()

        self.assert_failed_without_a_send(self.execute(), "Other Co")

    def test_ted_has_no_endpoint(self):
        self.add_order("Approved", payment_type="TED")

        self.assert_failed_without_a_send(self.execute(), "no TED endpoint")

    def test_an_unknown_payment_type(self):
        self.add_order("Approved", payment_type="DARF")

        self.assert_failed_without_a_send(self.execute(), "Unknown payment type")

    def test_a_pix_without_a_key(self):
        self.add_order("Approved", pix_key="")

        self.assert_failed_without_a_send(self.execute(), "PIX key")

    def test_an_amount_that_is_not_positive(self):
        self.add_order("Approved", purchase_invoice=None, amount=0)

        self.assert_failed_without_a_send(self.execute(), "amount")

    def test_a_client_that_cannot_be_built(self):
        self.add_order("Approved")
        self._patch(_ps_mod, InterAPIClient=MagicMock(side_effect=RuntimeError("certificate file is missing")))

        self.assert_failed_without_a_send(self.execute(), "certificate file is missing")


class TestBoletoPreSendGuards(BoletoCase):
    def test_a_boleto_without_a_due_date_or_with_a_broken_barcode(self):
        for label, fields, reason in (
            ("due date", {"boleto_due_date": None}, "due date"),
            ("barcode", {"barcode": "0779.7000"}, "barcode"),
            ("no barcode", {"barcode": None}, "barcode"),
        ):
            with self.subTest(label):
                self.setUp()
                self.add_order("Approved", **fields)

                result = self.execute()

                self.assertEqual(result["status"], "failed")
                self.assertIn(reason, result["message"])
                self.assertEqual(self.events("bank"), [])
                self.assertEqual((self.order()["status"], self.order()["invoice_lock"]), ("Failed", None))


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

class TestPixPayload(ServiceCase):
    def payload(self, **fields):
        self.add_order("Approved", **fields)
        self.execute()
        return self.client.calls[0][1]

    def test_shape(self):
        payload = self.payload(purchase_invoice=None, amount=1234.567)

        self.assertIsInstance(payload["valor"], float)
        self.assertEqual(payload["valor"], 1234.57)
        self.assertEqual(payload["destinatario"], {"tipo": "CHAVE", "chave": "pix@fornecedor.com.br"})
        self.assertNotIn("dataAgendamento", payload)
        self.assertNotIn("dataPagamento", payload)

    def test_the_description_never_exceeds_140_characters(self):
        long_invoice = "ACC-PINV-" + "9" * 200
        self.add_invoice(long_invoice)

        payload = self.payload(purchase_invoice=long_invoice, invoice_lock=long_invoice)

        self.assertEqual(len(payload["descricao"]), 140)

    def test_the_payment_date_is_only_sent_for_a_future_schedule(self):
        for scheduled, expected in (
            (None, None),
            (TODAY - datetime.timedelta(days=1), None),
            (TODAY, None),
            (TODAY + datetime.timedelta(days=3), "2026-09-23"),
            ("2026-09-23", "2026-09-23"),
        ):
            with self.subTest(scheduled=scheduled):
                self.setUp()
                payload = self.payload(scheduled_date=scheduled)
                self.assertEqual(payload.get("dataPagamento"), expected)
                self.assertNotIn("dataAgendamento", payload)


class TestBoletoPayload(BoletoCase):
    def payload(self, **fields):
        self.add_order("Approved", **fields)
        self.execute()
        return self.client.calls[0][1]

    def test_shape(self):
        payload = self.payload(barcode="0779 7000.000-" + BARCODE[11:])

        self.assertEqual(payload["codBarraLinhaDigitavel"], BARCODE)
        self.assertEqual(payload["valorPagar"], "16800.00")
        self.assertEqual(payload["dataVencimento"], "2026-09-25")
        self.assertNotIn("dataPagamento", payload)
        self.assertNotIn("dataAgendamento", payload)

    def test_the_payment_date_is_only_sent_for_a_future_schedule(self):
        self.assertNotIn("dataPagamento", self.payload(scheduled_date=TODAY))
        self.setUp()
        self.assertEqual(self.payload(scheduled_date="2026-09-24")["dataPagamento"], "2026-09-24")


# ---------------------------------------------------------------------------
# Classification of the send (I4)
# ---------------------------------------------------------------------------

class TestSendClassification(ServiceCase):
    def test_an_ambiguous_outcome_needs_verification(self):
        self.add_order("Approved")
        self.client.responses["send_pix"] = _ps_mod.InterAmbiguousResultError("read timeout on POST /banking/v2/pix")

        result = self.execute()

        self.assertEqual(result["status"], "needs_verification")
        row = self.order()
        self.assertEqual((row["status"], row["invoice_lock"]), ("Needs Verification", INVOICE))
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("read timeout", self.alerts[0][1])

    def _moved_then(self, error, status="Completed"):
        """The bank answers only after someone else has already moved the order on."""
        def answer(*args, **kwargs):
            self.db.set_value(DOCTYPE, ORDER, {"status": status})
            self.db.commit()
            raise error
        return answer

    def test_a_definitive_rejection_does_not_overwrite_an_order_that_left_processing(self):
        """Writing Failed blindly here would clear invoice_lock on an order the bank may hold."""
        self.add_order("Approved")
        self.client.responses["send_pix"] = self._moved_then(
            _ps_mod.InterAPIError("API error (HTTP 422)", status_code=422, response_body={}),
        )

        result = self.execute()

        self.assertEqual(result["status"], "skipped")
        row = self.order()
        self.assertEqual((row["status"], row["invoice_lock"]), ("Completed", INVOICE))
        self.assertEqual(self.alerts, [])

    def test_an_ambiguous_outcome_does_not_overwrite_an_order_that_left_processing(self):
        self.add_order("Approved")
        self.client.responses["send_pix"] = self._moved_then(
            _ps_mod.InterAmbiguousResultError("read timeout on POST /banking/v2/pix"),
        )

        result = self.execute()

        self.assertEqual(result["status"], "skipped")
        row = self.order()
        self.assertEqual((row["status"], row["invoice_lock"]), ("Completed", INVOICE))

    def test_a_definitive_rejection_is_failed_with_the_bank_message(self):
        self.add_order("Approved")
        self.client.responses["send_pix"] = _ps_mod.InterAPIError(
            "API error (HTTP 422)", status_code=422, response_body={"title": "Limite excedido [PIXP30]"},
        )

        result = self.execute()

        self.assertEqual(result["status"], "failed")
        row = self.order()
        self.assertEqual((row["status"], row["invoice_lock"]), ("Failed", None))
        self.assertIn("Limite excedido [PIXP30]", row["inter_response"])
        self.assertIn("422", row["inter_response"])
        self.assertEqual(len(self.sends()), 1)

    def test_an_authentication_error_is_failed_because_nothing_was_sent(self):
        self.add_order("Approved")
        self.client.responses["send_pix"] = _ps_mod.InterAuthError("token endpoint refused the certificate")

        self.assertEqual(self.execute()["status"], "failed")
        self.assertEqual(self.order()["status"], "Failed")
        self.assertIn("token endpoint refused", self.order()["inter_response"])

    def test_any_other_exception_needs_verification_and_is_raised_again(self):
        for error in (RuntimeError("worker lost its mind"), FakeJobTimeout("job exceeded 600s"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.add_order("Approved")
                self.client.responses["send_pix"] = error

                with self.assertRaises(type(error)):
                    self.execute()

                row = self.order()  # committed: the job runner rolls back after the exception
                self.assertEqual((row["status"], row["invoice_lock"]), ("Needs Verification", INVOICE))
                self.assertEqual(len(self.sends()), 1)
                self.assertEqual(len(self.alerts), 1)

    def test_a_failure_while_recording_the_answer_needs_verification_too(self):
        self.add_order("Approved")
        self._patch(_ps_mod, mark_awaiting_bank=MagicMock(side_effect=RuntimeError("deadlock")))

        with self.assertRaises(RuntimeError):
            self.execute()

        self.assertEqual(self.order()["status"], "Needs Verification")
        self.assertIn("fake-codigo-solicitacao", self.alerts[0][1], "the bank id must reach the operator")


class TestBoletoSendClassification(BoletoCase):
    def test_http_406_tells_the_operator_the_bill_is_already_paid(self):
        self.add_order("Approved")
        self.client.responses["pay_barcode"] = _ps_mod.InterAPIError(
            "API error (HTTP 406)", status_code=406, response_body={"title": "Título já liquidado"},
        )

        result = self.execute()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.order()["status"], "Failed")
        self.assertIn("already paid", self.alerts[0][1])
        self.assertIn("already paid", self.comments()[0])

    def test_an_ambiguous_boleto_needs_verification(self):
        self.add_order("Approved")
        self.client.responses["pay_barcode"] = _ps_mod.InterAmbiguousResultError("HTTP 409")

        self.assertEqual(self.execute()["status"], "needs_verification")
        self.assertEqual(self.order()["status"], "Needs Verification")
        self.assertEqual(len(self.sends()), 1)


# ---------------------------------------------------------------------------
# 2xx mapping (spec 3)
# ---------------------------------------------------------------------------

class TestPixAccepted(ServiceCase):
    def test_aprovacao_waits_for_the_bank_and_polls_once(self):
        self.add_order("Approved")
        self.client.responses["send_pix"] = {"tipoRetorno": "APROVACAO", "codigoSolicitacao": PIX_ID}

        result = self.execute()

        self.assertEqual(result["status"], "awaiting_bank")
        row = self.order()
        self.assertEqual((row["status"], row["approval_code"], row["bank_status"]),
                         ("Awaiting Bank", PIX_ID, "AGUARDANDO_APROVACAO"))
        self.assertEqual(row["invoice_lock"], INVOICE)
        self.assertIsNone(row["payment_entry"])
        self.assertEqual(self.bank_calls("get_pix_payment"), [("bank", "get_pix_payment", PIX_ID)])
        self.assertEqual(self.entries, [], "I7: nothing is settled before the bank says it is paid")

    def test_processado_is_not_proof_of_settlement(self):
        self.add_order("Approved")
        self.client.responses["send_pix"] = {"tipoRetorno": "PROCESSADO", "codigoSolicitacao": PIX_ID}
        self.client.responses["get_pix_payment"] = _ps_mod.InterAPIError("not yet", status_code=404)

        result = self.execute()

        self.assertEqual(result["status"], "awaiting_bank")
        self.assertEqual((self.order()["status"], self.order()["bank_status"]), ("Awaiting Bank", "PROCESSADO"))
        self.assertEqual(self.entries, [])

    def test_the_immediate_poll_may_already_complete_the_order(self):
        self.add_order("Approved")
        self.client.responses["send_pix"] = {"tipoRetorno": "PROCESSADO", "codigoSolicitacao": PIX_ID}
        self.client.responses["get_pix_payment"] = {"transacaoPix": {"status": "PAGO", "endToEnd": "E0041"}}

        result = self.execute()

        self.assertEqual(result["status"], "completed")
        row = self.order()
        self.assertEqual((row["status"], row["transaction_id"], row["approval_code"]), ("Completed", "E0041", PIX_ID))
        self.assertEqual(row["payment_entry"], self.entries[0].name)
        self.assertIsNone(row["invoice_lock"])

    def test_an_accepted_answer_without_an_id_needs_verification(self):
        self.add_order("Approved")
        self.client.responses["send_pix"] = {"tipoRetorno": "APROVACAO"}

        result = self.execute()

        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.order()["status"], "Needs Verification")
        self.assertEqual(self.bank_calls("get_pix_payment"), [])

    def test_a_bank_id_that_cannot_be_recorded_reaches_the_operator(self):
        """The cron flagged the order while the request was still open: the id must not be lost."""
        self.add_order("Approved")

        def answer_late(payment_data, idempotency_key):
            self.db.set_value(DOCTYPE, ORDER, "status", "Needs Verification")
            self.db.commit()
            return {"tipoRetorno": "APROVACAO", "codigoSolicitacao": PIX_ID}

        self.client.responses["send_pix"] = answer_late

        result = self.execute()

        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.order()["status"], "Needs Verification")
        self.assertTrue(any(PIX_ID in alert[1] for alert in self.alerts))


class TestBoletoAccepted(BoletoCase):
    def run_with(self, response):
        self.add_order("Approved")
        self.client.responses["pay_barcode"] = response
        return self.execute()

    def test_realizado_completes_and_settles(self):
        result = self.run_with({"statusPagamento": "REALIZADO", "codigoTransacao": BOLETO_ID})

        self.assertEqual(result["status"], "completed")
        row = self.order()
        self.assertEqual((row["status"], row["approval_code"], row["bank_status"]),
                         ("Completed", BOLETO_ID, "REALIZADO"))
        self.assertEqual(row["payment_entry"], self.entries[0].name)

    def test_aguardando_aprovacao_waits_for_the_bank(self):
        result = self.run_with({"statusPagamento": "AGUARDANDO_APROVACAO", "codigoTransacao": BOLETO_ID})

        self.assertEqual(result["status"], "awaiting_bank")
        row = self.order()
        self.assertEqual((row["status"], row["approval_code"], row["bank_status"]),
                         ("Awaiting Bank", BOLETO_ID, "AGUARDANDO_APROVACAO"))
        self.assertEqual(self.entries, [])

    def test_erro_is_failed(self):
        result = self.run_with({"statusPagamento": "ERRO", "codigoTransacao": BOLETO_ID})

        self.assertEqual(result["status"], "failed")
        self.assertEqual((self.order()["status"], self.order()["invoice_lock"]), ("Failed", None))

    def test_an_accepted_answer_without_an_id_needs_verification(self):
        result = self.run_with({"statusPagamento": "REALIZADO"})

        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.order()["status"], "Needs Verification")
        self.assertEqual(self.entries, [])


# ---------------------------------------------------------------------------
# The regression: a submitted order is never saved (I5), one path to the bank (I1)
# ---------------------------------------------------------------------------

class TestTheRegression(ServiceCase):
    def test_the_flow_ends_awaiting_bank_without_saving_the_submitted_order(self):
        self.add_order("Approved")

        self.execute()

        self.assertEqual(self.order()["status"], "Awaiting Bank")
        self.assertEqual(self.events("save"), [])

    def test_the_module_never_saves_a_document(self):
        self.assertNotIn(".save(", inspect.getsource(_ps_mod))

    def test_the_module_never_asks_for_a_ted(self):
        self.assertNotIn("send_ted", inspect.getsource(_ps_mod))

    def test_the_client_is_a_module_level_name_so_tests_can_replace_it(self):
        self.assertIn("InterAPIClient", vars(_ps_mod))
        self.assertIs(_ps_mod.InterAPIError, _ic_mod.InterAPIError)


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

class TestPixPoll(ServiceCase):
    def test_a_status_that_arrives_in_another_case_or_padded_still_decides(self):
        """Every vocabulary the bank publishes is upper snake case, but "unknown" here means the
        order keeps its invoice locked forever - so do not let a stray space cause that."""
        for spelling in (" pago ", "Pago", "PAGO\n"):
            with self.subTest(spelling=spelling):
                self.setUp()
                self.add_in_flight("Awaiting Bank")
                self.client.responses["get_pix_payment"] = {
                    "transacaoPix": {"status": spelling, "endToEnd": "E0041"}
                }

                _ps_mod.poll_bank_status(ORDER)

                self.assertEqual(self.order()["status"], "Completed")
                self.assertEqual(self.order()["bank_status"], spelling, "the audit keeps the bank's own text")

    def test_every_bank_status_lands_in_the_right_state(self):
        for expected, bank_statuses in PIX_STATUSES.items():
            for bank_status in bank_statuses:
                with self.subTest(bank_status=bank_status):
                    self.setUp()
                    self.add_in_flight("Awaiting Bank")
                    self.client.responses["get_pix_payment"] = {
                        "transacaoPix": {"status": bank_status, "endToEnd": "E0041", "erros": [{"codigo": "AB03"}]},
                    }

                    _ps_mod.poll_bank_status(ORDER)

                    row = self.order()
                    self.assertEqual((row["status"], row["bank_status"]), (expected, bank_status))
                    self.assertEqual(self.sends(), [])

    def test_paid_records_the_end_to_end_id_and_settles(self):
        self.add_in_flight("Awaiting Bank")
        self.client.responses["get_pix_payment"] = {"transacaoPix": {"status": "PAGO", "endToEnd": "E0041"}}

        result = _ps_mod.poll_bank_status(ORDER)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.order()["transaction_id"], "E0041")
        self.assertEqual(self.order()["payment_entry"], self.entries[0].name)

    def test_a_rejection_frees_the_invoice(self):
        self.add_in_flight("Awaiting Bank")
        self.client.responses["get_pix_payment"] = {"transacaoPix": {"status": "EXPIRADO"}}

        self.assertEqual(_ps_mod.poll_bank_status(ORDER)["status"], "failed")
        self.assertIsNone(self.order()["invoice_lock"])

    def test_falha_goes_to_a_human_with_the_errors_of_the_bank(self):
        self.add_in_flight("Awaiting Bank")
        self.client.responses["get_pix_payment"] = {
            "transacaoPix": {"status": "FALHA", "erros": [{"codigo": "AB03", "descricao": "timeout no SPI"}]},
        }

        result = _ps_mod.poll_bank_status(ORDER)

        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.order()["invoice_lock"], INVOICE)
        self.assertIn("timeout no SPI", self.alerts[0][1])

    def test_absence_and_errors_never_change_the_order(self):
        answers = {
            "no transaction": {},
            "no status": {"transacaoPix": {"endToEnd": "E0041"}},
            "another payment": {"transacaoPix": {"status": "PAGO", "codigoSolicitacao": "someone-else"}},
            "404": _ps_mod.InterAPIError("not found", status_code=404),
            "403": _ps_mod.InterAPIError("forbidden", status_code=403),
            "timeout": _ic_mod.InterTimeoutError("read timeout"),
            "anything": RuntimeError("boom"),
        }
        for label, answer in answers.items():
            with self.subTest(label):
                self.setUp()
                self.add_in_flight("Awaiting Bank", bank_status="AGUARDANDO_APROVACAO")
                before = self.snapshot()
                self.client.responses["get_pix_payment"] = answer

                result = _ps_mod.poll_bank_status(ORDER)

                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.order_writes(), [])
                self.assertIn(result["status"], ("unchanged", "error"))
                self.assertEqual(len(self.alerts), 1 if label == "403" else 0)

    def test_an_unchanged_bank_status_does_not_touch_the_row(self):
        self.add_in_flight("Awaiting Bank", bank_status="AGUARDANDO_APROVACAO")
        before = self.snapshot()

        result = _ps_mod.poll_bank_status(ORDER)  # the fake answers AGUARDANDO_APROVACAO

        self.assertEqual(result["status"], "awaiting_bank")
        self.assertEqual(self.snapshot(), before)  # an open form stays valid (modified untouched)

    def test_no_bank_id_no_poll(self):
        self.add_in_flight("Awaiting Bank", approval_code=None)

        self.assertEqual(_ps_mod.poll_bank_status(ORDER), {"status": "no_bank_id"})
        self.assertEqual(self.events("bank"), [])

    def test_only_an_order_awaiting_the_bank_is_polled(self):
        for status in ALL_STATUSES:
            if status == "Awaiting Bank":
                continue
            with self.subTest(status=status):
                self.setUp()
                self.add_in_flight(status, approval_code=PIX_ID, invoice_lock=None)

                result = _ps_mod.poll_bank_status(ORDER)

                self.assertEqual(result["status"], "skipped")
                self.assertEqual(self.events("bank", "set_value"), [])

    def test_the_kill_switch_blocks_the_poll(self):
        self.add_in_flight("Awaiting Bank")
        self.db.singles["Banco Inter Settings"]["enabled"] = 0

        self.assertEqual(_ps_mod.poll_bank_status(ORDER)["status"], "blocked")
        self.assertEqual(self.events("bank"), [])

    def test_a_job_timeout_is_not_swallowed(self):
        self.add_in_flight("Awaiting Bank")
        self.client.responses["get_pix_payment"] = _ps_mod.JobTimeoutException("job exceeded its timeout")

        with self.assertRaises(_ps_mod.JobTimeoutException):
            _ps_mod.poll_bank_status(ORDER)

        self.assertEqual(self.order()["status"], "Awaiting Bank")

    def test_the_client_is_built_for_the_account_of_the_order(self):
        self.add_in_flight("Awaiting Bank")

        _ps_mod.poll_bank_status(ORDER)

        self.assertEqual(self.client_accounts, [ACCOUNT])


class TestBoletoPoll(BoletoCase):
    def test_every_bank_status_lands_in_the_right_state(self):
        for expected, bank_statuses in BOLETO_STATUSES.items():
            for bank_status in bank_statuses:
                with self.subTest(bank_status=bank_status):
                    self.setUp()
                    self.add_in_flight("Awaiting Bank")
                    self.client.responses["find_barcode_payments"] = [
                        {"codigoTransacao": "another-one", "statusPagamento": "REALIZADO"},
                        {"codigoTransacao": BOLETO_ID, "statusPagamento": bank_status},
                    ]

                    _ps_mod.poll_bank_status(ORDER)

                    row = self.order()
                    self.assertEqual((row["status"], row["bank_status"]), (expected, bank_status))
                    self.assertEqual(self.sends(), [])

    def test_the_query_names_the_transaction_and_a_window_around_the_request(self):
        self.add_in_flight("Awaiting Bank", bank_request_at=datetime.datetime(2026, 9, 18, 23, 50))

        _ps_mod.poll_bank_status(ORDER)

        self.assertEqual(self.client.calls, [("find_barcode_payments", {
            "codigo_transacao": BOLETO_ID, "filter_date_by": "INCLUSAO",
            "start_date": datetime.date(2026, 9, 17), "end_date": datetime.date(2026, 9, 19),
            "max_retries": None,  # the cron may wait; the desk passes 0
        })])

    def test_the_desk_asks_once_instead_of_sleeping_in_the_worker(self):
        self.add_in_flight("Awaiting Bank")

        _ps_mod.poll_bank_status(ORDER, interactive=True)

        self.assertEqual(self.client.calls[0][1]["max_retries"], 0)

    def test_an_empty_list_or_a_list_without_our_payment_changes_nothing(self):
        for label, answer in (
            ("empty", []),
            ("someone else's", [{"codigoTransacao": "another-one", "statusPagamento": "REALIZADO"}]),
            ("403", _ps_mod.InterAPIError("forbidden", status_code=403)),
        ):
            with self.subTest(label):
                self.setUp()
                self.add_in_flight("Awaiting Bank")
                before = self.snapshot()
                self.client.responses["find_barcode_payments"] = answer

                _ps_mod.poll_bank_status(ORDER)

                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.entries, [])


# ---------------------------------------------------------------------------
# resolve_verification
# ---------------------------------------------------------------------------

class TestResolveVerification(ServiceCase):
    def resolve(self, outcome, **kwargs):
        return _ps_mod.resolve_verification(ORDER, outcome, **kwargs)

    def assert_refused(self, outcome, **kwargs):
        before = self.snapshot()
        with self.assertRaises(Thrown):
            self.resolve(outcome, **kwargs)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.order_writes(), [])

    def test_only_an_order_that_needs_verification_can_be_resolved(self):
        for status in ALL_STATUSES:
            if status == "Needs Verification":
                continue
            with self.subTest(status=status):
                self.setUp()
                self.add_in_flight(status, invoice_lock=None)
                self.assert_refused("not_paid", note="checked the statement")
                self.assert_refused("paid", bank_reference="E0041", paid_on="2026-09-19")
                self.assert_refused("at_bank", bank_reference=PIX_ID)
                # Refused up front, not merely by the compare-and-set: the bank is not even asked.
                self.assertEqual(self.events("bank"), [])

    def test_an_unknown_outcome_is_refused(self):
        self.add_in_flight("Needs Verification")
        self.assert_refused("maybe")

    # at_bank ---------------------------------------------------------------------

    def test_at_bank_needs_a_reference(self):
        self.add_in_flight("Needs Verification")
        self.assert_refused("at_bank")
        self.assertEqual(self.events("bank"), [])

    def test_at_bank_refuses_a_reference_another_order_carries(self):
        self.add_in_flight("Needs Verification")
        self.add_in_flight("Awaiting Bank", name="IPO-2026-00009", approval_code=PIX_ID, invoice_lock=None)

        self.assert_refused("at_bank", bank_reference=PIX_ID)
        self.assertEqual(self.events("bank"), [])

    def test_at_bank_refuses_a_reference_other_than_the_id_the_bank_gave_us(self):
        self.add_in_flight("Needs Verification", approval_code=PIX_ID)
        self.assert_refused("at_bank", bank_reference="some-other-id")

    def test_at_bank_falls_back_to_the_id_the_order_already_carries(self):
        self.add_in_flight("Needs Verification", approval_code=PIX_ID)

        self.resolve("at_bank")

        self.assertEqual(self.bank_calls("get_pix_payment"), [("bank", "get_pix_payment", PIX_ID)])
        self.assertEqual(self.order()["status"], "Awaiting Bank")

    def test_at_bank_refuses_what_the_bank_does_not_confirm(self):
        for label, answer in (
            ("404", _ps_mod.InterAPIError("not found", status_code=404)),
            ("no transaction", {}),
            ("timeout", _ic_mod.InterTimeoutError("read timeout")),
        ):
            with self.subTest(label):
                self.setUp()
                self.add_in_flight("Needs Verification")
                self.client.responses["get_pix_payment"] = answer
                self.assert_refused("at_bank", bank_reference=PIX_ID)

    def test_at_bank_cannot_ask_the_bank_with_the_kill_switch_off(self):
        self.add_in_flight("Needs Verification")
        self.db.singles["Banco Inter Settings"]["enabled"] = 0

        self.assert_refused("at_bank", bank_reference=PIX_ID)
        self.assertEqual(self.events("bank"), [])

    def test_at_bank_sets_the_id_and_maps_the_bank_status(self):
        for bank_status, expected, outcome in (
            ("AGUARDANDO_APROVACAO", "Awaiting Bank", "awaiting_bank"),
            ("PAGO", "Completed", "completed"),
            ("REPROVADO", "Failed", "failed"),
        ):
            with self.subTest(bank_status=bank_status):
                self.setUp()
                self.add_in_flight("Needs Verification")
                self.client.responses["get_pix_payment"] = {"transacaoPix": {"status": bank_status, "endToEnd": "E1"}}

                result = self.resolve("at_bank", bank_reference=f"  {PIX_ID} ")

                row = self.order()
                self.assertEqual(result["status"], outcome)
                self.assertEqual((row["status"], row["approval_code"], row["bank_status"]),
                                 (expected, PIX_ID, bank_status))
                self.assertEqual(self.sends(), [])
                self.assertEqual(len(self.bank_calls("get_pix_payment")), 1)

    # paid ------------------------------------------------------------------------

    def test_paid_needs_a_reference_and_a_date(self):
        self.add_in_flight("Needs Verification")
        self.assert_refused("paid", paid_on="2026-09-19")
        self.assert_refused("paid", bank_reference="E0041")
        self.assert_refused("paid", bank_reference="E0041", paid_on="2026-09-21")  # tomorrow
        self.assert_refused("paid", bank_reference="E0041", paid_on="the other day")

    def test_paid_refuses_a_reference_another_order_carries(self):
        self.add_in_flight("Needs Verification")
        self.add_in_flight("Completed", name="IPO-2026-00009", transaction_id="E0041", invoice_lock=None,
                           payment_entry="ACC-PAY-1")

        self.assert_refused("paid", bank_reference="E0041", paid_on="2026-09-19")

    def test_paid_completes_and_dates_the_payment_entry(self):
        self.add_in_flight("Needs Verification")

        result = self.resolve("paid", bank_reference="E0041", paid_on="2026-03-30", note="statement line 12")

        row = self.order()
        self.assertEqual(result["status"], "completed")
        self.assertEqual((row["status"], row["transaction_id"]), ("Completed", "E0041"))
        entry = self.entries[0]
        self.assertEqual(str(entry.posting_date), "2026-03-30")
        self.assertEqual(str(entry.reference_date), "2026-03-30")
        self.assertEqual(row["payment_entry"], entry.name)
        self.assertEqual(result["payment_entry"], entry.name)
        attested = [text for text in self.comments() if "statement line 12" in text]
        self.assertTrue(attested and "abel@intelligence8.com" in attested[0])
        self.assertEqual(self.events("bank"), [])

    def test_paid_works_with_the_kill_switch_off(self):
        """Deploy step 6 resolves IPO-2026-00001 while the integration is still disabled."""
        self.add_in_flight("Needs Verification")
        self.db.singles["Banco Inter Settings"]["enabled"] = 0

        self.assertEqual(self.resolve("paid", bank_reference="E0041", paid_on="2026-03-30")["status"], "completed")

    # not_paid --------------------------------------------------------------------

    def test_not_paid_is_refused_when_the_bank_gave_an_id(self):
        self.add_in_flight("Needs Verification", approval_code=PIX_ID)
        self.assert_refused("not_paid", note="not in the statement")

    def test_not_paid_needs_a_note(self):
        self.add_in_flight("Needs Verification")
        self.assert_refused("not_paid")
        self.assert_refused("not_paid", note="   ")

    def test_not_paid_fails_the_order_and_frees_the_invoice(self):
        self.add_in_flight("Needs Verification")

        result = self.resolve("not_paid", note="not in the statement, the queue or the schedule")

        row = self.order()
        self.assertEqual(result["status"], "failed")
        self.assertEqual((row["status"], row["invoice_lock"]), ("Failed", None))
        self.assertIn("not in the statement", self.comments()[0])
        self.assertIn("abel@intelligence8.com", self.comments()[0])
        self.assertEqual(self.alerts, [], "the operator is the one acting: no alert")


# ---------------------------------------------------------------------------
# Payment Entry (I7)
# ---------------------------------------------------------------------------

class TestPaymentEntry(ServiceCase):
    def create(self, **kwargs):
        return _ps_mod.create_payment_entry_for_order(ORDER, **kwargs)

    def completed(self, **fields):
        defaults = {"transaction_id": "E0041", "execution_date": datetime.datetime(2026, 9, 19, 15, 30)}
        self.add_in_flight("Completed", approval_code=PIX_ID, **{**defaults, **fields})

    def test_it_is_built_from_the_invoice_and_submitted(self):
        self.completed(party="SOMEONE-ELSE", company="Forged Co")

        name = self.create()

        entry = self.entries[0]
        self.assertEqual(name, entry.name)
        self.assertEqual((entry.payment_type, entry.company), ("Pay", COMPANY))
        self.assertEqual((entry.party_type, entry.party), ("Supplier", "SUP-1"))
        self.assertEqual((entry.paid_from, entry.paid_to), (BANK_GL, PAYABLE_GL))
        self.assertEqual((entry.paid_amount, entry.received_amount), (16800.0, 16800.0))
        self.assertEqual((entry.reference_no, entry.inter_payment_order), ("E0041", ORDER))
        self.assertEqual(str(entry.posting_date), "2026-09-19")
        self.assertEqual(str(entry.reference_date), "2026-09-19")
        self.assertEqual(entry.references, [{
            "reference_doctype": "Purchase Invoice", "reference_name": INVOICE, "allocated_amount": 16800.0,
        }])
        self.assertEqual(self.events("insert", "submit"),
                         [("insert", "Payment Entry", name), ("submit", "Payment Entry", name)])

    def test_success_links_the_entry_frees_the_invoice_and_commits(self):
        self.completed()

        name = self.create()

        row = self.order()  # committed
        self.assertEqual((row["payment_entry"], row["invoice_lock"], row["status"]), (name, None, "Completed"))

    def test_the_allocation_never_exceeds_what_is_outstanding(self):
        self.completed()
        self.db.set_value("Purchase Invoice", INVOICE, "outstanding_amount", 10000.0)
        self.db.commit()

        self.create()

        self.assertEqual(self.entries[0].references[0]["allocated_amount"], 10000.0)
        self.assertEqual(self.entries[0].paid_amount, 16800.0)

    def test_paid_on_wins_over_the_execution_date(self):
        self.completed()

        self.create(paid_on="2026-03-30")

        self.assertEqual(str(self.entries[0].posting_date), "2026-03-30")

    def test_the_order_row_is_locked_while_the_entry_is_created(self):
        self.completed()

        self.create()

        lock = self.db.events.index(("get_value", DOCTYPE, ORDER, True))
        self.assertLess(lock, self.db.events.index(self.events("insert")[0]))

    def test_a_submitted_entry_of_this_order_is_adopted(self):
        self.completed()
        self.db.add("Payment Entry", "ACC-PAY-MANUAL", docstatus=1, inter_payment_order=ORDER)

        name = self.create()

        self.assertEqual(name, "ACC-PAY-MANUAL")
        self.assertEqual(self.events("insert", "submit"), [])
        self.assertEqual((self.order()["payment_entry"], self.order()["invoice_lock"]), ("ACC-PAY-MANUAL", None))

    def test_a_draft_entry_of_this_order_is_submitted_and_adopted(self):
        self.completed()
        self.db.add("Payment Entry", "ACC-PAY-DRAFT", docstatus=0, inter_payment_order=ORDER)

        name = self.create()

        self.assertEqual(name, "ACC-PAY-DRAFT")
        self.assertEqual(self.events("insert", "submit"), [("submit", "Payment Entry", "ACC-PAY-DRAFT")])
        self.assertEqual(self.order()["payment_entry"], "ACC-PAY-DRAFT")

    def test_a_cancelled_entry_of_this_order_is_not_adopted(self):
        self.completed()
        self.db.add("Payment Entry", "ACC-PAY-CANCELLED", docstatus=2, inter_payment_order=ORDER)

        name = self.create()

        self.assertNotEqual(name, "ACC-PAY-CANCELLED")
        self.assertEqual(len(self.events("insert")), 1)

    def test_an_order_that_already_has_its_entry_gets_no_second_one(self):
        self.completed(payment_entry="ACC-PAY-1", invoice_lock=INVOICE)

        self.assertEqual(self.create(), "ACC-PAY-1")
        self.assertEqual(self.events("insert", "submit"), [])
        self.assertIsNone(self.order()["invoice_lock"])

    def test_a_settled_invoice_gets_no_entry_only_a_comment(self):
        self.completed()
        self.db.set_value("Purchase Invoice", INVOICE, "outstanding_amount", 0.0)
        self.db.commit()

        self.assertIsNone(self.create())

        self.assertEqual(self.events("insert"), [])
        self.assertEqual(len(self.comments()), 1)
        self.assertIn(INVOICE, self.comments()[0])
        self.assertEqual(self.alerts, [])
        self.assertEqual((self.order()["status"], self.order()["invoice_lock"]), ("Completed", INVOICE))

    def test_an_order_without_an_invoice_is_left_to_the_operator(self):
        self.completed(purchase_invoice=None, invoice_lock=None)

        self.assertIsNone(self.create())

        self.assertEqual(self.events("insert"), [])
        self.assertEqual(len(self.alerts), 1)

    def test_only_a_completed_order_is_settled(self):
        for status in ALL_STATUSES:
            if status == "Completed":
                continue
            with self.subTest(status=status):
                self.setUp()
                self.add_in_flight(status, invoice_lock=None)

                self.assertIsNone(self.create())
                self.assertEqual(self.events("insert", "submit", "set_value"), [])

    def test_a_failing_entry_rolls_back_first_alerts_once_and_leaves_the_order_completed(self):
        for step in ("insert", "submit"):
            with self.subTest(step=step):
                self.setUp()
                self.completed()
                self.entry_fails_on = step

                self.assertIsNone(self.create())

                rollback = self.db.events.index(("rollback",))
                self.assertLess(rollback, self.db.events.index(self.events("alert")[0]))
                self.log_error.assert_called()
                self.assertEqual(len(self.alerts), 1)
                self.assertIn("accounting period is closed", self.alerts[0][1])
                row = self.order()
                self.assertEqual((row["status"], row["payment_entry"], row["invoice_lock"]),
                                 ("Completed", None, INVOICE))
                self.assertEqual(self.db.get_all("Payment Entry"), [], "the half-made entry was rolled back")

    def test_there_is_no_automatic_retry(self):
        self.completed()
        self.entry_fails_on = "insert"

        self.create()

        self.assertEqual(len(self.entries), 1)
        self.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# The hourly cron never sends (I3)
# ---------------------------------------------------------------------------

class TestCron(ServiceCase):
    def run_cron(self):
        _ps_mod.scheduled_payment_status_check()

    def add_many(self, status, count, **fields):
        names = [f"IPO-2026-1{index:04d}" for index in range(count)]
        for name in names:
            self.add_in_flight(status, name=name, purchase_invoice=None, **fields)
        return names

    def test_no_status_and_no_payment_type_ever_makes_the_cron_send(self):
        """Every status, fresh and old, with and without the marks of a previous claim.

        The rows without a key matter most: an ``Approved`` one is exactly what a claim accepts,
        so a cron that called the execution for it WOULD reach the bank.
        """
        for payment_type in ("PIX", "Boleto Payment", "TED"):
            for index, status in enumerate(ALL_STATUSES):
                common = {
                    "payment_type": payment_type, "purchase_invoice": None,
                    "docstatus": 2 if status == "Cancelled" else 1,
                }
                for age in (1, 61, 60 * 24 * 90):  # 15 orders end up awaiting the bank: all are polled
                    prefix = f"IPO-{payment_type[:3]}-{index}-{age}"
                    modified = NOW - datetime.timedelta(minutes=age)
                    self.add_order(status, name=f"{prefix}-CLEAN", modified=modified, **common)
                    self.add_in_flight(status, name=f"{prefix}-MARKED", minutes_ago=age, approval_code=PIX_ID, **common)

        self.run_cron()

        self.assertEqual(self.sends(), [])
        # Not sending in-process is only half of it: the incident was an hourly job handing orders
        # back to the execution path. Queueing one is the same defect, one worker removed.
        self.assertEqual(self.enqueue.call_count, 0, "the cron queued an execution")
        self.assertEqual(self.client.i2_violations, [])
        self.assertEqual(len(self.bank_calls("get_pix_payment")), 2, "the matrix must exercise the polling branch")
        self.assertEqual(len(self.bank_calls("find_barcode_payments")), 2)
        untouched = [name for name in self.db.get_all(DOCTYPE, filters={"status": "Approved"}, pluck="name")]
        self.assertEqual(len(untouched), 18, "no Approved order was claimed")

    def test_orders_awaiting_the_bank_are_polled(self):
        self.add_in_flight("Awaiting Bank")
        self.client.responses["get_pix_payment"] = {"transacaoPix": {"status": "PAGO", "endToEnd": "E0041"}}

        self.run_cron()

        self.assertEqual(self.order()["status"], "Completed")

    def test_at_most_15_polls_per_run_and_the_cap_is_logged(self):
        names = self.add_many("Awaiting Bank", 20)

        self.run_cron()

        self.assertEqual(len(self.bank_calls("get_pix_payment")), 15)
        self.logger.warning.assert_called_once()
        warning = str(self.logger.warning.call_args)
        self.assertIn("20", warning)
        self.assertEqual(sum(name in warning for name in names), 5, "the orders left for a later run are named")

    def test_polling_stops_before_the_job_is_killed(self):
        """The hourly job lives on a queue that kills it at 300 s, and one hung poll can take 270.

        Without a budget a slow bank costs the whole run: the stale-Processing rescue and every
        other poll simply do not happen, hour after hour.
        """
        names = self.add_many("Awaiting Bank", 5)
        clock = [NOW]
        self._patch(_ps_mod, now_datetime=lambda: clock[0])
        real_poll = _ps_mod.poll_bank_status

        def slow(name, **kwargs):
            clock[0] += datetime.timedelta(seconds=100)
            return real_poll(name, **kwargs)

        self._patch(_ps_mod, poll_bank_status=slow)

        self.run_cron()

        self.assertEqual(len(self.bank_calls("get_pix_payment")), 3, "240 s of budget, 100 s per poll")
        warning = str(self.logger.warning.call_args)
        self.assertEqual(sum(name in warning for name in names), 2, "the orders left behind are named")

    def test_the_window_moves_every_hour_so_no_order_starves(self):
        names = self.add_many("Awaiting Bank", 20)
        polled_orders = []
        real_poll = _ps_mod.poll_bank_status
        self._patch(_ps_mod, poll_bank_status=lambda name: polled_orders.append(name) or real_poll(name))

        self.run_cron()
        self._patch(_ps_mod, now_datetime=lambda: NOW + datetime.timedelta(hours=1))
        self.run_cron()

        self.assertEqual(len(polled_orders), 30)
        self.assertEqual(set(polled_orders), set(names))

    def test_a_stale_processing_order_needs_verification(self):
        self.add_in_flight("Processing", name="IPO-STALE", minutes_ago=31, purchase_invoice=None)
        self.add_in_flight("Processing", name="IPO-FRESH", minutes_ago=29, purchase_invoice=None)
        self.add_order("Processing", name="IPO-LEGACY", purchase_invoice=None, bank_request_at=None)
        fresh_before = self.snapshot("IPO-FRESH")

        self.run_cron()

        self.assertEqual(self.order("IPO-STALE")["status"], "Needs Verification")
        self.assertEqual(self.order("IPO-LEGACY")["status"], "Needs Verification")
        self.assertEqual(self.snapshot("IPO-FRESH"), fresh_before)
        self.assertEqual(self.events("bank"), [])
        self.assertEqual(len(self.alerts), 2)

    def test_an_order_awaiting_the_bank_for_85_days_needs_verification(self):
        self.add_in_flight("Awaiting Bank", name="IPO-OLD", minutes_ago=60 * 24 * 86, purchase_invoice=None)
        self.add_in_flight("Awaiting Bank", name="IPO-RECENT", minutes_ago=60 * 24 * 84, purchase_invoice=None)

        self.run_cron()

        self.assertEqual(self.order("IPO-OLD")["status"], "Needs Verification")
        self.assertEqual(self.order("IPO-RECENT")["status"], "Awaiting Bank")
        self.assertEqual(len(self.bank_calls("get_pix_payment")), 1)  # only the recent one

    def test_the_kill_switch_stops_everything(self):
        self.add_in_flight("Awaiting Bank")
        self.add_in_flight("Processing", name="IPO-STALE", minutes_ago=90, purchase_invoice=None)
        self.db.singles["Banco Inter Settings"]["enabled"] = 0

        self.run_cron()

        self.assertEqual(self.events("bank", "set_value", "get_value"), [])

    def test_one_broken_order_does_not_stop_the_others(self):
        names = self.add_many("Awaiting Bank", 3)
        real_poll = _ps_mod.poll_bank_status

        def poll(name):
            if name == names[0]:
                raise RuntimeError("row is corrupt")
            return real_poll(name)

        self._patch(_ps_mod, poll_bank_status=poll)

        self.run_cron()

        self.assertEqual(len(self.bank_calls("get_pix_payment")), 2)
        self.log_error.assert_called()

    def test_a_missing_read_scope_alerts_once_per_run_not_once_per_order(self):
        self.add_many("Awaiting Bank", 5)
        self.client.responses["get_pix_payment"] = _ps_mod.InterAPIError("forbidden", status_code=403)

        self.run_cron()

        self.assertEqual(len(self.bank_calls("get_pix_payment")), 1)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("pagamento-pix.read", self.alerts[0][1])


# ---------------------------------------------------------------------------
# Payment Entry cancelled (doc_event hook)
# ---------------------------------------------------------------------------

class TestOnPaymentEntryCancel(ServiceCase):
    def cancel(self, entry="ACC-PAY-1", order=ORDER):
        doc = {"name": entry, "inter_payment_order": order}
        _ps_mod.on_payment_entry_cancel(SimpleNamespace(name=entry, get=doc.get), "on_cancel")

    def test_the_link_is_cleared_and_the_invoice_is_protected_again(self):
        self.add_in_flight("Completed", payment_entry="ACC-PAY-1")
        self.assertIsNone(self.db.row(DOCTYPE, ORDER)["invoice_lock"])

        self.cancel()

        row = self.db.row(DOCTYPE, ORDER)
        self.assertEqual((row["payment_entry"], row["invoice_lock"], row["status"]), (None, INVOICE, "Completed"))
        self.assertEqual(len(self.comments()), 1)
        self.assertIn("ACC-PAY-1", self.comments()[0])

    def test_it_runs_inside_the_cancel_transaction(self):
        self.add_in_flight("Completed", payment_entry="ACC-PAY-1")

        self.cancel()

        self.assertEqual(self.events("commit", "rollback"), [], "the transaction belongs to the Payment Entry")
        self.assertIn(("get_value", DOCTYPE, ORDER, True), self.db.events)

    def test_an_entry_without_an_order_is_none_of_our_business(self):
        _ps_mod.on_payment_entry_cancel(SimpleNamespace(name="ACC-PAY-1", get={}.get))

        self.assertEqual(self.db.events, [])

    def test_an_entry_the_order_does_not_point_to_clears_nothing(self):
        self.add_in_flight("Completed", payment_entry="ACC-PAY-2")

        self.cancel("ACC-PAY-1")

        self.assertEqual(self.db.row(DOCTYPE, ORDER)["payment_entry"], "ACC-PAY-2")
        self.assertEqual(self.order_writes(), [])

    def test_another_blocking_order_keeps_the_lock_and_the_operator_is_told(self):
        self.add_in_flight("Completed", payment_entry="ACC-PAY-1")
        self.add_in_flight("Awaiting Bank", name="IPO-2026-00009")  # holds the lock of the same invoice

        self.cancel()

        row = self.db.row(DOCTYPE, ORDER)
        self.assertEqual((row["payment_entry"], row["invoice_lock"]), (None, None))
        # Telling a human means HTTP, and this hook holds the order row locked inside the Payment
        # Entry's own transaction: the alert has to wait for the commit that frees it.
        self.assertEqual(self.alerts, [], "the alert went out while the row was still locked")
        self.db.commit()
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("IPO-2026-00009", self.alerts[0][1])

    def test_a_unique_key_violation_is_swallowed_and_alerted(self):
        """The race backstop: the other order appeared after the check."""
        self.add_in_flight("Completed", payment_entry="ACC-PAY-1")
        self.add_in_flight("Awaiting Bank", name="IPO-2026-00009")
        self._patch(_ps_mod, find_blocking_payment_order=lambda *args, **kwargs: None)

        self.cancel()  # must not raise: the Payment Entry has to be cancellable

        row = self.db.row(DOCTYPE, ORDER)
        self.assertEqual((row["payment_entry"], row["invoice_lock"]), (None, None))
        self.assertEqual(self.alerts, [])
        self.db.commit()
        self.assertEqual(len(self.alerts), 1)

    def test_a_rolled_back_cancel_alerts_nobody(self):
        """Nothing was unlinked, so there is nothing to warn about."""
        self.add_in_flight("Completed", payment_entry="ACC-PAY-1")
        self.add_in_flight("Awaiting Bank", name="IPO-2026-00009")

        self.cancel()
        self.db.rollback()

        self.assertEqual(self.alerts, [])

    def test_the_hook_is_registered_next_to_the_submit_hook(self):
        events = _hooks.doc_events["Payment Entry"]

        self.assertEqual(events["on_cancel"], "brazil_module.services.banking.payment_service.on_payment_entry_cancel")
        self.assertEqual(events["on_submit"], "brazil_module.services.banking.reconciliation.on_payment_entry_submit")
        self.assertTrue(callable(_ps_mod.on_payment_entry_cancel))


# ---------------------------------------------------------------------------
# create_payment_order_for_invoice, enqueue_payment_execution
# ---------------------------------------------------------------------------

class FakeNewOrder:
    def __init__(self, log):
        self.name = "IPO-2026-00077"
        self._log = log

    def insert(self, **kwargs):
        self._log.append(("insert", kwargs))
        return self

    def submit(self):
        self._log.append(("submit", {}))


class TestCreatePaymentOrderForInvoice(ServiceCase):
    def setUp(self):
        super().setUp()
        self.doc_log = []
        self.new_order = FakeNewOrder(self.doc_log)
        _shadow_frappe(self, new_doc=self._new_order)

    def _new_order(self, doctype):
        self.assertEqual(doctype, DOCTYPE)
        return self.new_order

    def test_everything_comes_from_the_invoice(self):
        name = _ps_mod.create_payment_order_for_invoice(
            INVOICE, "PIX", pix_key="pix@fornecedor.com.br", scheduled_date="2026-09-25",
        )

        order = self.new_order
        self.assertEqual(name, "IPO-2026-00077")
        self.assertEqual((order.payment_type, order.company, order.inter_company_account), ("PIX", COMPANY, ACCOUNT))
        self.assertEqual((order.purchase_invoice, order.party_type, order.party), (INVOICE, "Supplier", "SUP-1"))
        self.assertEqual((order.amount, order.pix_key, order.scheduled_date),
                         (16800.0, "pix@fornecedor.com.br", "2026-09-25"))
        self.assertEqual(order.recipient_name, "Fornecedor Ltda")
        self.assertEqual([step for step, _kwargs in self.doc_log], ["insert", "submit"])
        self.assertEqual(self.doc_log[0][1], {}, "permissions are checked: no ignore_permissions")
        self.assertEqual(self.events("bank"), [])

    def test_a_boleto_defaults_its_due_date_to_the_invoice(self):
        _ps_mod.create_payment_order_for_invoice(INVOICE, "Boleto Payment", barcode=BARCODE)

        self.assertEqual((self.new_order.barcode, self.new_order.boleto_due_date),
                         (BARCODE, datetime.date(2026, 9, 25)))

    def test_an_explicit_boleto_due_date_wins(self):
        _ps_mod.create_payment_order_for_invoice(
            INVOICE, "Boleto Payment", barcode=BARCODE, boleto_due_date="2026-10-01",
        )

        self.assertEqual(self.new_order.boleto_due_date, "2026-10-01")

    def test_submit_false_leaves_a_draft(self):
        _ps_mod.create_payment_order_for_invoice(INVOICE, "PIX", pix_key="k", submit=False)

        self.assertEqual([step for step, _kwargs in self.doc_log], ["insert"])

    def test_no_inter_account_no_order(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "sync_enabled", 0)

        with self.assertRaises(Thrown):
            _ps_mod.create_payment_order_for_invoice(INVOICE, "PIX", pix_key="k")
        self.assertEqual(self.doc_log, [])

    def test_an_unknown_invoice_or_one_that_is_not_payable_is_refused_with_the_reason(self):
        with self.assertRaises(Thrown):
            _ps_mod.create_payment_order_for_invoice("ACC-PINV-404", "PIX", pix_key="k")
        self.add_in_flight("Needs Verification", name="IPO-2026-00001")
        with self.assertRaises(Thrown) as refused:
            _ps_mod.create_payment_order_for_invoice(INVOICE, "PIX", pix_key="k")
        self.assertIn("IPO-2026-00001", str(refused.exception))
        self.assertEqual(self.doc_log, [])


class TestEnqueuePaymentExecution(ServiceCase):
    def test_the_job_is_deduplicated_by_order(self):
        self.assertIs(_ps_mod.enqueue_payment_execution(ORDER), True)

        self.enqueue.assert_called_once_with(
            _ps_mod.execute_payment_order, queue="short", timeout=600,
            job_id=f"inter_payment_order::{ORDER}", deduplicate=True,
            payment_order_name=ORDER, requested_at=str(NOW),
        )

    def test_an_execution_that_is_already_queued_or_running_is_reported(self):
        self.enqueue.return_value = None

        self.assertIs(_ps_mod.enqueue_payment_execution(ORDER), False)

    def test_enqueueing_writes_nothing(self):
        self.add_order("Approved")

        _ps_mod.enqueue_payment_execution(ORDER)

        self.assertEqual(self.events("set_value", "commit", "bank"), [])


if __name__ == "__main__":
    unittest.main()
