"""Tests for the DocType ``Inter Payment Order`` (spec 4.3): the JSON contract and the controller.

The controller is loaded as a *private copy* with ``@frappe.whitelist()`` as a pass-through:
under the bare frappe mock the decorator turns every whitelisted method into a ``MagicMock``
(a test on it could never fail), and which copy another test module imported first depends on
the collection order.
"""

import importlib.util
import json
import os
import re
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from brazil_module.tests._payment_fakes import FakeDB, install_frappe_mock, patch_frappe, patch_frappe_db

frappe = install_frappe_mock()

import brazil_module.services.banking.payment_guards as guards
from brazil_module.services.banking.payment_guards import (
    CANCELLABLE_STATUSES,
    DOCTYPE,
    IN_FLIGHT_STATUSES,
    NON_BLOCKING_STATUSES,
)

_DOCTYPE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "bancos", "doctype", "inter_payment_order",
)
JSON_PATH = os.path.join(_DOCTYPE_DIR, "inter_payment_order.json")
CONTROLLER_PATH = os.path.join(_DOCTYPE_DIR, "inter_payment_order.py")
FORM_JS_PATH = os.path.join(_DOCTYPE_DIR, "inter_payment_order.js")
LIST_JS_PATH = os.path.join(_DOCTYPE_DIR, "inter_payment_order_list.js")

SPEC_STATUSES = [
    "Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
    "Needs Verification", "Completed", "Failed", "Cancelled",
]
NEW_FIELDS = {
    "idempotency_key": "Data",
    "bank_status": "Data",
    "bank_request_at": "Datetime",
    "invoice_lock": "Data",
}
RESULT_FIELDS = (
    "idempotency_key", "bank_status", "bank_request_at", "transaction_id", "approval_code",
    "execution_date", "inter_response", "payment_entry",
)


def _load_json() -> dict:
    with open(JSON_PATH) as fh:
        return json.load(fh)


def _fields_by_name() -> dict:
    return {df["fieldname"]: df for df in _load_json()["fields"]}


# ---------------------------------------------------------------------------
# JSON contract
# ---------------------------------------------------------------------------

class TestJsonContract(unittest.TestCase):
    def setUp(self):
        self.schema = _load_json()
        self.fields = _fields_by_name()

    def test_status_options_are_the_spec_list_in_order(self):
        self.assertEqual(self.fields["status"]["options"].split("\n"), SPEC_STATUSES)
        self.assertEqual(self.fields["status"]["default"], "Draft")

    def test_every_guard_constant_is_a_status_option(self):
        options = set(self.fields["status"]["options"].split("\n"))
        constants = set(IN_FLIGHT_STATUSES) | set(NON_BLOCKING_STATUSES) | set(CANCELLABLE_STATUSES)
        self.assertEqual(constants - options, set())

    def test_new_fields_exist_read_only_and_no_copy(self):
        for fieldname, fieldtype in NEW_FIELDS.items():
            with self.subTest(fieldname=fieldname):
                self.assertIn(fieldname, self.fields)
                df = self.fields[fieldname]
                self.assertEqual(df["fieldtype"], fieldtype)
                self.assertEqual(df.get("read_only"), 1)
                self.assertEqual(df.get("no_copy"), 1)
                self.assertTrue(df.get("label"))

    def test_invoice_lock_is_unique_and_hidden(self):
        df = self.fields.get("invoice_lock", {})
        self.assertEqual(df.get("unique"), 1)
        self.assertEqual(df.get("hidden"), 1)

    def test_invoice_lock_is_the_only_unique_field(self):
        unique = [name for name, df in self.fields.items() if df.get("unique")]
        self.assertEqual(unique, ["invoice_lock"])

    def test_result_fields_are_not_copied_by_duplicate(self):
        for fieldname in ("status", "transaction_id", "approval_code", "execution_date", "inter_response",
                          "payment_entry"):
            with self.subTest(fieldname=fieldname):
                self.assertEqual(self.fields[fieldname].get("no_copy"), 1)

    def test_field_order_lists_every_field_exactly_once(self):
        order = self.schema["field_order"]
        self.assertEqual(sorted(order), sorted(self.fields))
        self.assertEqual(len(order), len(set(order)))
        self.assertEqual(order, [df["fieldname"] for df in self.schema["fields"]])

    def test_new_result_fields_sit_in_the_result_section(self):
        order = self.schema["field_order"]
        start = order.index("result_section")
        for fieldname in ("idempotency_key", "bank_status", "bank_request_at"):
            with self.subTest(fieldname=fieldname):
                self.assertIn(fieldname, order)
                self.assertGreater(order.index(fieldname), start)

    def test_no_state_field_is_editable_after_submit(self):
        """State is written with frappe.db.set_value (I5); nothing invites a Document.save()."""
        for fieldname in ("status", "invoice_lock", *RESULT_FIELDS):
            with self.subTest(fieldname=fieldname):
                self.assertFalse(self.fields.get(fieldname, {}).get("allow_on_submit"))

    def test_the_form_asks_for_the_boleto_due_date(self):
        """validate() is the authority; the form only says it earlier."""
        self.assertEqual(
            self.fields["boleto_due_date"].get("mandatory_depends_on"), "eval:doc.payment_type=='Boleto Payment'"
        )

    def test_the_schema_was_touched(self):
        self.assertGreater(self.schema["modified"], "2026-02-09 00:00:00.000000")
        self.assertEqual(self.schema["is_submittable"], 1)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

SERVICE_MODULE = "brazil_module.services.banking.payment_service"
MANAGER_ROLES = ("Banco Inter Manager", "System Manager")
ORDER = "IPO-2026-00001"
INVOICE = "ACC-PINV-2026-00031"
_MISSING = object()


class ThrownError(Exception):
    """What ``frappe.throw`` raises in these tests."""


def _throw(message, *args, **kwargs):
    raise ThrownError(message)


def _flt(value, precision=None):
    number = float(value or 0)
    return round(number, precision) if precision is not None else number


class _PlainDocument:
    """``frappe.model.document.Document`` reduced to what the controller leans on."""

    doctype = DOCTYPE

    def __init__(self, *args, **kwargs):
        pass

    def db_set(self, fieldname, value=None, update_modified=True, **kwargs):
        values = fieldname if isinstance(fieldname, dict) else {fieldname: value}
        for key, item in values.items():
            setattr(self, key, item)
        frappe.db.set_value(self.doctype, self.name, values, update_modified=update_modified)


def _load_controller():
    """A private copy of the controller, imported with a pass-through ``@frappe.whitelist()``."""
    document_module = types.ModuleType("frappe.model.document")
    document_module.Document = _PlainDocument
    namespace = frappe.__dict__
    previous_whitelist = namespace.get("whitelist", _MISSING)
    previous_document = sys.modules.get("frappe.model.document", _MISSING)
    namespace["whitelist"] = lambda *args, **kwargs: (lambda function: function)
    sys.modules["frappe.model.document"] = document_module
    try:
        spec = importlib.util.spec_from_file_location("_inter_payment_order_under_test", CONTROLLER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous_whitelist is _MISSING:
            namespace.pop("whitelist", None)
        else:
            namespace["whitelist"] = previous_whitelist
        if previous_document is not _MISSING:
            sys.modules["frappe.model.document"] = previous_document
        # else: the stub stays. The other doctype test modules expect the entry to exist whenever
        # the frappe mock does (test_doctype_fiscal_validation.py indexes it without a guard).
    return module


_WHITELIST_BEFORE_LOAD = frappe.__dict__.get("whitelist", _MISSING)
controller = _load_controller()
_WHITELIST_AFTER_LOAD = frappe.__dict__.get("whitelist", _MISSING)
InterPaymentOrder = controller.InterPaymentOrder


class ControllerCase(unittest.TestCase):
    """An enabled integration, a payable invoice, and a fake payment service (T4's names)."""

    def setUp(self):
        self.db = FakeDB(singles={"Banco Inter Settings": {"enabled": 1, "payment_approval_required": 1}})
        self.db.add(
            "Purchase Invoice", INVOICE, docstatus=1, on_hold=0, outstanding_amount=16800.0,
            supplier="SUP-1", company="Intelligence8",
        )
        self.db.add("Supplier", "SUP-1", on_hold=0, hold_type=None)
        patch_frappe_db(self, self.db)
        self.msgprint = MagicMock()
        self.only_for = MagicMock()
        patch_frappe(self, throw=_throw, msgprint=self.msgprint, only_for=self.only_for)
        # `from frappe import _` / `from frappe.utils import flt` were bound at import time.
        for module, name, value in (
            (controller, "_", lambda text: text), (controller, "flt", _flt),
            (guards, "_", lambda text: text), (guards, "flt", _flt),
        ):
            self._start(patch.object(module, name, value))
        self.service = types.ModuleType(SERVICE_MODULE)
        self.service.enqueue_payment_execution = MagicMock(return_value=True)
        self.service.poll_bank_status = MagicMock(return_value={"status": "awaiting_bank"})
        self.service.resolve_verification = MagicMock(return_value={"status": "completed"})
        self.service.create_payment_entry_for_order = MagicMock(return_value="ACC-PAY-2026-00009")
        self._start(patch.dict(sys.modules, {SERVICE_MODULE: self.service}))

    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_order(self, name=ORDER, **fields):
        """The document as the request rebuilt it (from client JSON): it proves nothing about the DB."""
        order = InterPaymentOrder.__new__(InterPaymentOrder)
        values = {fieldname: None for fieldname in _fields_by_name()}
        values.update(
            name=name, docstatus=0, status="Draft", payment_type="PIX", amount=16800.0,
            pix_key="financeiro@fornecedor.com.br", company="Intelligence8",
        )
        values.update(fields)
        for key, value in values.items():
            setattr(order, key, value)
        order.check_permission = MagicMock()
        return order

    def add_row(self, name=ORDER, status="Approved", docstatus=1, **fields):
        """A stored order. ``ORDER`` pays ``INVOICE``; any other name pays an invoice of its own
        (``invoice_lock`` is unique), unless the test says which."""
        fields.setdefault("purchase_invoice", INVOICE if name == ORDER else f"PINV-OF-{name}")
        blocking = docstatus < 2 and guards.is_blocking(status, fields.get("payment_entry"))
        fields.setdefault("invoice_lock", fields["purchase_invoice"] if blocking else None)
        self.db.add(DOCTYPE, name, status=status, docstatus=docstatus, **fields)
        return self.db.row(DOCTYPE, name)

    def writes(self):
        return [event for event in self.db.events if event[0] == "set_value"]

    def assert_message_mentions(self, mock, *fragments):
        text = " ".join(str(call.args[0]) for call in mock.call_args_list).lower()
        for fragment in fragments:
            self.assertIn(fragment.lower(), text)


class TestWhitelistedMethodsAreReal(unittest.TestCase):
    def test_the_private_copy_has_functions_not_mocks(self):
        for method in ("approve_payment", "execute_payment", "check_bank_status", "resolve_verification",
                       "create_payment_entry"):
            with self.subTest(method=method):
                self.assertIsInstance(getattr(InterPaymentOrder, method, None), types.FunctionType)

    def test_loading_the_copy_left_the_shared_mock_as_it_was(self):
        """Whatever ``frappe.whitelist`` was for the other test modules, it still is."""
        self.assertIs(_WHITELIST_AFTER_LOAD, _WHITELIST_BEFORE_LOAD)


class TestBeforeInsert(ControllerCase):
    def test_an_amended_order_starts_clean(self):
        """Amend ignores no_copy in Frappe v15: the copy arrives with the whole result of the original."""
        order = self.make_order(
            status="Cancelled", idempotency_key="0b0f3c9e-5a1d-4a57-9d0e-3f1f4f6f7a10",
            approval_code="cod-sol-1", transaction_id="E0041696820260330", bank_status="PAGO",
            bank_request_at="2026-03-30 10:00:00", execution_date="2026-03-30 10:00:05",
            inter_response='{"tipoRetorno": "APROVACAO"}', payment_entry="ACC-PAY-2026-00001",
            amended_from="IPO-2026-00000",
        )

        order.before_insert()

        self.assertEqual(order.status, "Draft")
        for fieldname in RESULT_FIELDS:
            with self.subTest(fieldname=fieldname):
                self.assertIsNone(getattr(order, fieldname))

    def test_what_the_user_typed_survives(self):
        order = self.make_order(purchase_invoice=INVOICE, amended_from="IPO-2026-00000")

        order.before_insert()

        self.assertEqual(order.purchase_invoice, INVOICE)
        self.assertEqual(order.amount, 16800.0)
        self.assertEqual(order.pix_key, "financeiro@fornecedor.com.br")
        self.assertEqual(order.amended_from, "IPO-2026-00000")


class TestValidate(ControllerCase):
    def test_a_valid_pix_order_passes(self):
        self.make_order().validate()

    def test_amount_must_be_positive(self):
        for amount in (-10, 0, None):
            with self.subTest(amount=amount), self.assertRaises(ThrownError):
                self.make_order(amount=amount).validate()

    def test_pix_requires_a_key(self):
        with self.assertRaises(ThrownError):
            self.make_order(pix_key=None).validate()

    def test_ted_is_rejected_even_when_complete(self):
        order = self.make_order(
            payment_type="TED", pix_key=None, recipient_bank_code="077", recipient_agency="0001",
            recipient_account="123456-7",
        )
        with self.assertRaises(ThrownError) as raised:
            order.validate()
        self.assertIn("no TED endpoint", str(raised.exception))

    def test_boleto_requires_a_barcode(self):
        order = self.make_order(payment_type="Boleto Payment", pix_key=None, boleto_due_date="2026-10-05")
        with self.assertRaises(ThrownError):
            order.validate()

    def test_boleto_with_a_barcode_without_digits_is_rejected(self):
        order = self.make_order(
            payment_type="Boleto Payment", pix_key=None, barcode="not a barcode", boleto_due_date="2026-10-05",
        )
        with self.assertRaises(ThrownError):
            order.validate()

    def test_boleto_requires_the_due_date(self):
        order = self.make_order(
            payment_type="Boleto Payment", pix_key=None, barcode="2379338128600000000031234567890123456789012345",
        )
        with self.assertRaises(ThrownError) as raised:
            order.validate()
        self.assertIn("Due Date", str(raised.exception))

    def test_boleto_barcode_is_normalised_to_digits(self):
        order = self.make_order(
            payment_type="Boleto Payment", pix_key=None, boleto_due_date="2026-10-05",
            barcode="23793.38128 60000.000003 12345.678901 2 34567890123456",
        )
        order.validate()
        self.assertEqual(order.barcode, "23793381286000000000312345678901234567890123456")

    def test_an_unpayable_invoice_is_refused_with_the_reason(self):
        order = self.make_order(purchase_invoice=INVOICE)
        with patch.object(controller, "check_invoice_payable", return_value="Purchase Invoice X is on hold") as check:
            with self.assertRaises(ThrownError) as raised:
                order.validate()
        self.assertIn("is on hold", str(raised.exception))
        check.assert_called_once_with(INVOICE, 16800.0, order_name=ORDER, company="Intelligence8")
        self.assertIsNone(order.invoice_lock)

    def test_a_payable_invoice_is_locked(self):
        order = self.make_order(purchase_invoice=INVOICE)
        order.validate()
        self.assertEqual(order.invoice_lock, INVOICE)

    def test_without_invoice_there_is_no_lock(self):
        for forged in (None, "", INVOICE):
            with self.subTest(forged=forged):
                order = self.make_order(purchase_invoice=None, invoice_lock=forged)
                order.validate()
                # None, never '': the column is unique and two '' collide.
                self.assertIsNone(order.invoice_lock)

    def test_another_blocking_order_is_named(self):
        self.add_row("IPO-2026-00077", status="Needs Verification", purchase_invoice=INVOICE)
        order = self.make_order(name="IPO-2026-00078", purchase_invoice=INVOICE)
        with self.assertRaises(ThrownError) as raised:
            order.validate()
        self.assertIn("IPO-2026-00077", str(raised.exception))

    def test_a_failed_order_does_not_block_a_new_attempt(self):
        self.add_row("IPO-2026-00077", status="Failed", purchase_invoice=INVOICE)
        order = self.make_order(name="IPO-2026-00078", purchase_invoice=INVOICE)
        order.validate()
        self.assertEqual(order.invoice_lock, INVOICE)

    def test_the_order_does_not_block_itself_on_submit(self):
        self.add_row(ORDER, status="Draft", docstatus=0)
        order = self.make_order(purchase_invoice=INVOICE, docstatus=1)
        order.validate()
        self.assertEqual(order.invoice_lock, INVOICE)

    def test_a_draft_is_always_draft(self):
        """``status`` is read-only in the form only; a draft saved through the API cannot forge it."""
        order = self.make_order(status="Approved", docstatus=0)
        order.validate()
        self.assertEqual(order.status, "Draft")

    def test_validate_never_writes(self):
        self.make_order(purchase_invoice=INVOICE).validate()
        self.assertEqual(self.writes(), [])


class TestOnSubmit(ControllerCase):
    def test_goes_to_pending_approval_when_approval_is_required(self):
        row = self.add_row(status="Draft")
        before = row["modified"]
        order = self.make_order(docstatus=1)

        order.on_submit()

        self.assertEqual(row["status"], "Pending Approval")
        self.assertEqual(order.status, "Pending Approval")
        self.assertGreater(row["modified"], before)  # never update_modified=False (I5)

    def test_goes_to_approved_when_no_approval_is_required(self):
        self.db.singles["Banco Inter Settings"]["payment_approval_required"] = 0
        row = self.add_row(status="Draft")

        self.make_order(docstatus=1, status="Completed").on_submit()

        self.assertEqual(row["status"], "Approved")


class TestApprovePayment(ControllerCase):
    def test_pending_approval_becomes_approved(self):
        row = self.add_row(status="Pending Approval")
        before = row["modified"]
        order = self.make_order(status="Pending Approval", docstatus=1)

        order.approve_payment()

        self.only_for.assert_called_once_with(MANAGER_ROLES)
        self.assertEqual(self.db.committed_row(DOCTYPE, ORDER)["status"], "Approved")
        self.assertGreater(row["modified"], before)
        self.assertEqual(order.status, "Approved")
        locked = [read for read in self.db.reads if read["doctype"] == DOCTYPE]
        self.assertTrue(locked and all(read["for_update"] for read in locked))

    def test_a_forged_status_does_not_touch_an_order_in_flight(self):
        for db_status in ("Draft", "Approved", "Processing", "Awaiting Bank", "Needs Verification", "Completed",
                          "Failed", "Cancelled"):
            with self.subTest(db_status=db_status):
                name = f"IPO-{db_status}"
                self.add_row(name, status=db_status)
                order = self.make_order(name=name, status="Pending Approval", docstatus=1)

                with self.assertRaises(ThrownError):
                    order.approve_payment()

                self.assertEqual(self.db.row(DOCTYPE, name)["status"], db_status)
        self.assertEqual(self.writes(), [])

    def test_only_a_submitted_order_is_approved(self):
        for docstatus in (0, 2):
            with self.subTest(docstatus=docstatus):
                name = f"IPO-DS-{docstatus}"
                self.add_row(name, status="Pending Approval", docstatus=docstatus)
                with self.assertRaises(ThrownError):
                    self.make_order(name=name, status="Pending Approval", docstatus=1).approve_payment()
        self.assertEqual(self.writes(), [])

    def test_an_unknown_order_is_refused(self):
        with self.assertRaises(ThrownError):
            self.make_order(name="IPO-GHOST", status="Pending Approval", docstatus=1).approve_payment()
        self.assertEqual(self.writes(), [])

    def test_without_the_role_nothing_is_read_or_written(self):
        self.add_row(status="Pending Approval")
        self.only_for.side_effect = PermissionError("not a manager")

        with self.assertRaises(PermissionError):
            self.make_order(status="Pending Approval", docstatus=1).approve_payment()

        self.assertEqual(self.db.events, [])


class TestExecutePayment(ControllerCase):
    def test_queues_the_execution_and_never_writes_status(self):
        self.add_row(status="Approved")
        order = self.make_order(status="Approved", docstatus=1)

        result = order.execute_payment()

        order.check_permission.assert_called_once_with("submit")
        self.service.enqueue_payment_execution.assert_called_once_with(ORDER)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.db.row(DOCTYPE, ORDER)["status"], "Approved")
        self.assertEqual(result, {"status": "queued", "payment_order": ORDER})
        self.msgprint.assert_called_once()

    def test_says_so_when_already_queued_or_running(self):
        self.add_row(status="Approved")
        self.service.enqueue_payment_execution.return_value = False

        result = self.make_order(status="Approved", docstatus=1).execute_payment()

        self.assertEqual(result, {"status": "already_queued", "payment_order": ORDER})
        self.assert_message_mentions(self.msgprint, "already queued or running")
        self.assertEqual(self.writes(), [])

    def test_the_database_status_decides_not_the_document(self):
        for db_status in ("Draft", "Pending Approval", "Processing", "Awaiting Bank", "Needs Verification",
                          "Completed", "Failed", "Cancelled"):
            with self.subTest(db_status=db_status):
                name = f"IPO-{db_status}"
                self.add_row(name, status=db_status)
                with self.assertRaises(ThrownError):
                    self.make_order(name=name, status="Approved", docstatus=1).execute_payment()
        self.service.enqueue_payment_execution.assert_not_called()
        self.assertEqual(self.writes(), [])

    def test_only_a_submitted_order_is_executed(self):
        for docstatus in (0, 2):
            with self.subTest(docstatus=docstatus):
                name = f"IPO-DS-{docstatus}"
                self.add_row(name, status="Approved", docstatus=docstatus)
                with self.assertRaises(ThrownError):
                    self.make_order(name=name, status="Approved", docstatus=1).execute_payment()
        self.service.enqueue_payment_execution.assert_not_called()

    def test_the_kill_switch_blocks_the_execution(self):
        self.db.singles["Banco Inter Settings"]["enabled"] = 0
        self.add_row(status="Approved")

        with self.assertRaises(ThrownError) as raised:
            self.make_order(status="Approved", docstatus=1).execute_payment()

        self.assertIn("disabled", str(raised.exception))
        self.service.enqueue_payment_execution.assert_not_called()
        self.assertEqual(self.writes(), [])

    def test_without_submit_permission_nothing_happens(self):
        self.add_row(status="Approved")
        order = self.make_order(status="Approved", docstatus=1)
        order.check_permission.side_effect = PermissionError("no submit")

        with self.assertRaises(PermissionError):
            order.execute_payment()

        self.service.enqueue_payment_execution.assert_not_called()
        self.assertEqual(self.db.events, [])


class TestCancel(ControllerCase):
    def test_an_order_the_bank_may_hold_cannot_be_cancelled(self):
        for db_status in (*IN_FLIGHT_STATUSES, "Completed"):
            with self.subTest(db_status=db_status):
                name = f"IPO-{db_status}"
                self.add_row(name, status=db_status)
                order = self.make_order(name=name, status="Approved", docstatus=2)

                with self.assertRaises(ThrownError) as raised:
                    order.before_cancel()

                self.assertIn(db_status, str(raised.exception))
                self.assertEqual(self.db.reads[-1]["doctype"], DOCTYPE)
                self.assertTrue(self.db.reads[-1]["for_update"])

    def test_a_completed_order_with_payment_entry_cannot_be_cancelled_either(self):
        self.add_row(status="Completed", payment_entry="ACC-PAY-2026-00001")
        with self.assertRaises(ThrownError):
            self.make_order(status="Approved", docstatus=2).before_cancel()

    def test_the_cancellable_statuses_pass(self):
        for db_status in CANCELLABLE_STATUSES:
            with self.subTest(db_status=db_status):
                name = f"IPO-{db_status}"
                self.add_row(name, status=db_status)
                self.make_order(name=name, status="Processing", docstatus=2).before_cancel()
        self.assertTrue(all(read["for_update"] for read in self.db.reads if read["doctype"] == DOCTYPE))

    def test_an_unknown_order_cannot_be_cancelled(self):
        with self.assertRaises(ThrownError):
            self.make_order(name="IPO-GHOST", status="Approved", docstatus=2).before_cancel()

    def test_before_cancel_never_writes(self):
        self.add_row(status="Approved")
        self.make_order(status="Approved", docstatus=2).before_cancel()
        self.assertEqual(self.writes(), [])

    def test_on_cancel_frees_the_invoice(self):
        row = self.add_row(status="Approved")
        before = row["modified"]
        order = self.make_order(status="Approved", docstatus=2, invoice_lock=INVOICE)

        order.on_cancel()

        self.assertEqual(self.writes(), [("set_value", DOCTYPE, ORDER, {"status": "Cancelled", "invoice_lock": None})])
        self.assertEqual(row["status"], "Cancelled")
        self.assertIsNone(row["invoice_lock"])
        self.assertGreater(row["modified"], before)  # never update_modified=False (I5)
        self.assertEqual((order.status, order.invoice_lock), ("Cancelled", None))

    def test_after_cancel_the_invoice_takes_a_new_order(self):
        self.add_row(status="Approved")
        self.make_order(status="Approved", docstatus=2).on_cancel()
        self.db.row(DOCTYPE, ORDER)["docstatus"] = 2

        replacement = self.make_order(name="IPO-2026-00002", purchase_invoice=INVOICE)
        replacement.validate()

        self.assertEqual(replacement.invoice_lock, INVOICE)


class TestDelegation(ControllerCase):
    def test_check_bank_status(self):
        order = self.make_order(status="Awaiting Bank", docstatus=1)

        result = order.check_bank_status()

        order.check_permission.assert_called_once_with("write")
        self.service.poll_bank_status.assert_called_once_with(ORDER)
        self.assertEqual(result, {"status": "awaiting_bank"})
        self.assertEqual(self.writes(), [])

    def test_check_bank_status_needs_write_permission(self):
        order = self.make_order(status="Awaiting Bank", docstatus=1)
        order.check_permission.side_effect = PermissionError("read only")
        with self.assertRaises(PermissionError):
            order.check_bank_status()
        self.service.poll_bank_status.assert_not_called()

    def test_resolve_verification(self):
        order = self.make_order(status="Needs Verification", docstatus=1)

        result = order.resolve_verification("paid", bank_reference="E004169682026", paid_on="2026-03-30", note="ok")

        self.only_for.assert_called_once_with(MANAGER_ROLES)
        self.service.resolve_verification.assert_called_once_with(
            ORDER, "paid", bank_reference="E004169682026", paid_on="2026-03-30", note="ok",
        )
        self.assertEqual(result, {"status": "completed"})
        self.assertEqual(self.writes(), [])

    def test_resolve_verification_defaults(self):
        self.make_order(status="Needs Verification", docstatus=1).resolve_verification("not_paid")
        self.service.resolve_verification.assert_called_once_with(
            ORDER, "not_paid", bank_reference="", paid_on=None, note="",
        )

    def test_resolve_verification_treats_an_empty_date_as_no_date(self):
        """The dialog sends '' for an empty Date field."""
        self.make_order(status="Needs Verification", docstatus=1).resolve_verification("not_paid", "", "", "x")
        self.service.resolve_verification.assert_called_once_with(
            ORDER, "not_paid", bank_reference="", paid_on=None, note="x",
        )

    def test_resolve_verification_needs_the_role(self):
        self.only_for.side_effect = PermissionError("not a manager")
        with self.assertRaises(PermissionError):
            self.make_order(status="Needs Verification", docstatus=1).resolve_verification("paid")
        self.service.resolve_verification.assert_not_called()

    def test_create_payment_entry(self):
        self.add_row(status="Completed")
        order = self.make_order(status="Completed", docstatus=1)

        result = order.create_payment_entry()

        self.only_for.assert_called_once_with(MANAGER_ROLES)
        self.service.create_payment_entry_for_order.assert_called_once_with(ORDER)
        self.assertEqual(result, "ACC-PAY-2026-00009")
        self.assertEqual(self.writes(), [])

    def test_create_payment_entry_only_settles_what_was_paid(self):
        """I7: a forged 'Completed' in the document does not settle an order the bank never paid."""
        for db_status in (status for status in SPEC_STATUSES if status != "Completed"):
            with self.subTest(db_status=db_status):
                name = f"IPO-{db_status}"
                self.add_row(name, status=db_status)
                with self.assertRaises(ThrownError):
                    self.make_order(name=name, status="Completed", docstatus=1).create_payment_entry()
        self.add_row("IPO-CANCELLED-DOC", status="Completed", docstatus=2)
        with self.assertRaises(ThrownError):
            self.make_order(name="IPO-CANCELLED-DOC", status="Completed", docstatus=1).create_payment_entry()
        with self.assertRaises(ThrownError):
            self.make_order(name="IPO-GHOST", status="Completed", docstatus=1).create_payment_entry()
        self.service.create_payment_entry_for_order.assert_not_called()
        self.assertEqual(self.writes(), [])

    def test_create_payment_entry_needs_the_role(self):
        self.only_for.side_effect = PermissionError("not a manager")
        with self.assertRaises(PermissionError):
            self.make_order(status="Completed", docstatus=1).create_payment_entry()
        self.service.create_payment_entry_for_order.assert_not_called()


class TestControllerSource(unittest.TestCase):
    """Static tripwires: what the controller must never do (I1, I5)."""

    def setUp(self):
        with open(CONTROLLER_PATH) as fh:
            self.source = fh.read()

    def test_never_saves_and_never_freezes_modified(self):
        self.assertNotIn(".save(", self.source)
        self.assertNotIn("update_modified=False", self.source)

    def test_never_sends_or_enqueues_by_itself(self):
        for forbidden in ("frappe.enqueue(", "execute_payment_order", "InterAPIClient", ".send_pix(",
                          ".pay_barcode(", ".send_ted("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_the_payment_service_is_imported_lazily(self):
        module_level = [line for line in self.source.splitlines()
                        if line.startswith(("import ", "from ")) and "payment_service" in line]
        self.assertEqual(module_level, [])


# ---------------------------------------------------------------------------
# Form and list scripts (static: there is no JS runtime here)
# ---------------------------------------------------------------------------

def _read(path: str) -> str:
    with open(path) as fh:
        return fh.read()


class TestFormScript(unittest.TestCase):
    def setUp(self):
        self.source = _read(FORM_JS_PATH)

    def test_every_status_has_an_indicator_colour(self):
        for status in SPEC_STATUSES:
            with self.subTest(status=status):
                self.assertRegex(self.source, rf'"{status}":\s*"[a-z]+"')
        self.assertRegex(self.source, r'"Awaiting Bank":\s*"purple"')
        self.assertRegex(self.source, r'"Needs Verification":\s*"red"')

    def test_every_controller_method_has_a_button(self):
        for method in ("approve_payment", "execute_payment", "check_bank_status", "resolve_verification",
                       "create_payment_entry"):
            with self.subTest(method=method):
                self.assertIn(f'"{method}"', self.source)

    def test_every_call_goes_through_the_helper_that_reloads_first(self):
        """The stale-form protection: reload, re-check the status, only then call."""
        self.assertEqual(self.source.count("frm.call("), 1)
        helper = self.source[self.source.index("async function call_on_fresh_doc"):]
        self.assertLess(helper.index("await frm.reload_doc()"), helper.index("frm.call("))

    def test_resolve_dialog_offers_the_three_outcomes(self):
        for outcome in ("at_bank", "paid", "not_paid"):
            with self.subTest(outcome=outcome):
                self.assertIn(f'"{outcome}"', self.source)
        for fieldname in ("outcome", "bank_reference", "paid_on", "note"):
            with self.subTest(fieldname=fieldname):
                self.assertIn(f'fieldname: "{fieldname}"', self.source)

    def test_not_paid_demands_three_explicit_confirmations(self):
        for fieldname in ("not_in_statement", "not_in_approval_queue", "not_in_scheduled_payments"):
            with self.subTest(fieldname=fieldname):
                self.assertIn(fieldname, self.source)
        self.assertEqual(self.source.count('fieldtype: "Check"'), 3)

    def test_amount_comes_from_what_is_still_outstanding(self):
        self.assertIn("outstanding_amount", self.source)
        self.assertNotIn("grand_total", self.source)

    def test_the_form_offers_no_way_around_the_service(self):
        for forbidden in ("execute_payment_order", "frappe.db.set_value", "frm.save(", "TED\\n"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_user_facing_strings_are_translatable(self):
        self.assertNotRegex(self.source, r'add_custom_button\(\s*"')


class TestListScript(unittest.TestCase):
    def test_the_list_shows_the_status_indicator(self):
        self.assertTrue(os.path.exists(LIST_JS_PATH), "inter_payment_order_list.js is missing")
        source = _read(LIST_JS_PATH)
        self.assertIn('frappe.listview_settings["Inter Payment Order"]', source)
        self.assertIn("get_indicator", source)
        for status in SPEC_STATUSES:
            with self.subTest(status=status):
                self.assertRegex(source, rf'"{status}":\s*"[a-z]+"')

    def test_form_and_list_agree_on_the_colours(self):
        pattern = re.compile(r'"(' + "|".join(SPEC_STATUSES) + r')":\s*"([a-z]+)"')
        self.assertTrue(os.path.exists(LIST_JS_PATH), "inter_payment_order_list.js is missing")
        self.assertEqual(dict(pattern.findall(_read(FORM_JS_PATH))), dict(pattern.findall(_read(LIST_JS_PATH))))


if __name__ == "__main__":
    unittest.main()
