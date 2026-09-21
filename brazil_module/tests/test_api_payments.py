"""Tests for the payment entry points of ``brazil_module.api`` (spec 4.5) and the *Pay via Inter* dialog.

``brazil_module/api/__init__.py`` is loaded as a *private copy* with ``@frappe.whitelist()`` as a
pass-through: under the bare frappe mock the decorator turns every endpoint into a ``MagicMock``
and no test on it could ever fail. State lives in a ``FakeDB``; wherever the promise of an
endpoint depends on what stands behind it (the guards, ``create_payment_order_for_invoice``, the
controller's ``execute_payment``), the real code runs.
"""

import datetime
import importlib.util
import inspect
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from brazil_module.tests._payment_fakes import DOCTYPE, FakeDB, install_frappe_mock

install_frappe_mock()

# test_webhook_handler.py parks MagicMock placeholders for these modules when it is imported first.
for _name in (
    "brazil_module.services.banking.auth_manager",
    "brazil_module.services.banking.inter_client",
    "brazil_module.services.banking.payment_service",
):
    if isinstance(sys.modules.get(_name), MagicMock):
        del sys.modules[_name]

import brazil_module.services.banking.payment_guards as _guards
import brazil_module.services.banking.payment_service as _ps_mod

_PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(_PACKAGE, "api", "__init__.py")
CONTROLLER_PATH = os.path.join(_PACKAGE, "bancos", "doctype", "inter_payment_order", "inter_payment_order.py")
DIALOG_JS_PATH = os.path.join(_PACKAGE, "public", "js", "purchase_invoice.js")

ORDER = "IPO-2026-00002"
INVOICE = "ACC-PINV-2026-00031"
COMPANY = "Intelligence8"
ACCOUNT = "Inter - I8"
DUE_DATE = datetime.date(2026, 9, 25)
BARCODE = "07797000000000000004501008460019310001802680"
MANAGER_ROLES = ("Banco Inter Manager", "System Manager")
WEEKLY_JOB = "brazil_module.services.intelligence.recurring.planning_loop.schedule_weekly_payments"
ALL_STATUSES = (
    "Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
    "Needs Verification", "Completed", "Failed", "Cancelled",
)
_MISSING = object()


class Thrown(Exception):
    """What ``frappe.throw`` raises here (on the bare mock it would return and the code go on)."""


def _throw(message, *args, **kwargs):
    raise Thrown(str(message))


def _flt(value, precision=None):
    number = float(value or 0)
    return round(number, precision) if precision is not None else number


class _PlainDocument:
    """``frappe.model.document.Document`` reduced to what ``execute_payment`` leans on."""

    doctype = DOCTYPE

    def __init__(self, *args, **kwargs):
        pass


def _load_private_copy(module_name: str, path: str):
    """Import a source file with a pass-through ``@frappe.whitelist()`` and a plain ``Document``."""
    namespace = install_frappe_mock().__dict__
    document_module = types.ModuleType("frappe.model.document")
    document_module.Document = _PlainDocument
    previous_whitelist = namespace.get("whitelist", _MISSING)
    previous_document = sys.modules.get("frappe.model.document", _MISSING)
    namespace["whitelist"] = lambda *args, **kwargs: (lambda function: function)
    sys.modules["frappe.model.document"] = document_module
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous_whitelist is _MISSING:
            namespace.pop("whitelist", None)
        else:
            namespace["whitelist"] = previous_whitelist
        if previous_document is not _MISSING:
            sys.modules["frappe.model.document"] = previous_document
        # else: the stub stays, as in test_inter_payment_order.py - the doctype test modules expect
        # the entry to exist whenever the frappe mock does.
    return module


_WHITELIST_BEFORE_LOAD = install_frappe_mock().__dict__.get("whitelist", _MISSING)
api = _load_private_copy("_brazil_module_api_under_test", API_PATH)
controller = _load_private_copy("_inter_payment_order_behind_the_api", CONTROLLER_PATH)
_WHITELIST_AFTER_LOAD = install_frappe_mock().__dict__.get("whitelist", _MISSING)
InterPaymentOrder = controller.InterPaymentOrder


def _shadow_frappe(test_case, **attributes):
    """Shadow attributes on every frappe mock the modules under test hold (normally one).

    Only the instance ``__dict__`` is touched, never the mock's children - see ``patch_frappe``.
    """
    holders = {id(module.frappe): module.frappe for module in (api, controller, _ps_mod, _guards)}
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


class FakeNewOrder:
    """What ``frappe.new_doc`` hands over. ``insert`` writes the row the way a transaction does:
    into the working copy only, so that a rollback undoes it and a test can see whether one ran."""

    def __init__(self, db, fail_after_insert=None):
        self._db = db
        self._fail_after_insert = fail_after_insert
        self.name = None

    def insert(self, **kwargs):
        self._insert_kwargs = kwargs
        self.name = f"IPO-2026-{len(self._db.get_all(DOCTYPE)) + 77:05d}"
        row = {key: value for key, value in vars(self).items() if not key.startswith("_")}
        self._db._working.setdefault(DOCTYPE, {})[self.name] = {**row, "docstatus": 0, "status": "Draft"}
        self._db.events.append(("insert", DOCTYPE, self.name))
        if self._fail_after_insert:
            raise self._fail_after_insert
        return self

    def submit(self):
        self._db.events.append(("submit", DOCTYPE, self.name))


def _fixture_lock(status: str, docstatus: int, fields: dict):
    """Restated on purpose: deriving it from ``is_blocking`` would let the fixture drift with it."""
    if docstatus >= 2 or status in ("Failed", "Cancelled"):
        return None
    if status == "Completed" and fields.get("payment_entry"):
        return None
    return fields["purchase_invoice"]


class ApiCase(unittest.TestCase):
    """An enabled integration, a payable invoice, its supplier and the company's Inter account."""

    def setUp(self):
        self.db = FakeDB(singles={"Banco Inter Settings": {"enabled": 1}})
        self.add_invoice(INVOICE)
        self.db.add(
            "Supplier", "SUP-1", on_hold=0, hold_type=None,
            supplier_name="Fornecedor Ltda", tax_id="12345678000190",
        )
        self.db.add("Inter Company Account", ACCOUNT, company=COMPANY, sync_enabled=1)
        self.new_orders = []
        self.insert_fails_with = None
        self.check_permission = MagicMock(name="doc.check_permission")
        self.enqueue = MagicMock(name="frappe.enqueue")
        self.msgprint = MagicMock(name="frappe.msgprint")
        self.only_for = MagicMock(name="frappe.only_for")
        self.clear_messages = MagicMock(name="frappe.clear_messages")
        self.error_logs = []
        _shadow_frappe(
            self, db=self.db, get_all=self.db.get_all, throw=_throw, enqueue=self.enqueue,
            msgprint=self.msgprint, only_for=self.only_for, clear_messages=self.clear_messages,
            log_error=self._log_error, get_traceback=lambda: "Traceback (most recent call last): ...",
            new_doc=self._new_doc, get_doc=self._get_doc,
        )
        # `from frappe import _` / `from frappe.utils import flt` were bound at import time.
        for module in (api, controller, _ps_mod, _guards):
            self._patch(module, _=lambda text: text)
        for module in (controller, _ps_mod, _guards):
            self._patch(module, flt=_flt)

    # -- plumbing -------------------------------------------------------------------

    def _patch(self, module, **attributes):
        for name, value in attributes.items():
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _log_error(self, *args, **kwargs):
        self.error_logs.append((args, kwargs))
        self.db.events.append(("log_error",))

    def _new_doc(self, doctype):
        self.assertEqual(doctype, DOCTYPE)
        order = FakeNewOrder(self.db, fail_after_insert=self.insert_fails_with)
        self.new_orders.append(order)
        return order

    def _get_doc(self, doctype, name=None):
        if doctype == "Supplier":
            return SimpleNamespace(**self.db.row("Supplier", name))
        self.assertEqual(doctype, DOCTYPE)
        order = InterPaymentOrder.__new__(InterPaymentOrder)
        for key, value in self.db.row(DOCTYPE, name).items():
            setattr(order, key, value)
        order.check_permission = self.check_permission
        return order

    # -- fixtures and inspection ----------------------------------------------------

    def add_invoice(self, name):
        self.db.add(
            "Purchase Invoice", name, docstatus=1, on_hold=0, outstanding_amount=16800.0,
            supplier="SUP-1", company=COMPANY, credit_to="2.1.1 Fornecedores - I8", due_date=DUE_DATE,
        )

    def add_order(self, status="Approved", name=ORDER, docstatus=1, **fields):
        fields.setdefault("purchase_invoice", INVOICE)
        fields.setdefault("invoice_lock", _fixture_lock(status, docstatus, fields))
        self.db.add(DOCTYPE, name, status=status, docstatus=docstatus, payment_type="PIX", amount=16800.0, **fields)

    def stored_orders(self):
        return self.db.get_all(DOCTYPE, pluck="name")

    def events(self, *kinds):
        return [event for event in self.db.events if event[0] in kinds]


class TestTheEndpointsAreReal(unittest.TestCase):
    def test_the_private_copy_has_functions_not_mocks(self):
        for endpoint in ("create_payment_order", "execute_payment", "i8_run_payment_scheduling"):
            with self.subTest(endpoint=endpoint):
                self.assertIsInstance(getattr(api, endpoint, None), types.FunctionType)

    def test_loading_the_copy_left_the_shared_mock_as_it_was(self):
        self.assertIs(_WHITELIST_AFTER_LOAD, _WHITELIST_BEFORE_LOAD)

    def test_nothing_in_the_api_talks_to_the_bank_or_runs_the_execution_itself(self):
        """I1: the API only creates orders and hands over; the job is queued by the service."""
        source = inspect.getsource(api)
        for forbidden in (".send_pix(", ".pay_barcode(", ".send_ted(", "execute_payment_order"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


# ---------------------------------------------------------------------------
# create_payment_order - with a Purchase Invoice
# ---------------------------------------------------------------------------

class CreateForInvoiceCase(ApiCase):
    def create(self, **overrides):
        arguments = {
            "payment_type": "PIX", "amount": 16800.0, "company": COMPANY, "purchase_invoice": INVOICE,
            "party_type": "Supplier", "party": "SUP-1", "pix_key": "pix@fornecedor.com.br",
        }
        arguments.update(overrides)
        return api.create_payment_order(**arguments)


class TestCreateDelegatesToTheService(CreateForInvoiceCase):
    def setUp(self):
        super().setUp()
        self.service = MagicMock(name="create_payment_order_for_invoice", return_value="IPO-2026-00077")
        self._patch(_ps_mod, create_payment_order_for_invoice=self.service)

    def test_an_invoice_goes_through_the_service_as_a_draft(self):
        result = self.create()

        self.service.assert_called_once_with(
            INVOICE, "PIX", pix_key="pix@fornecedor.com.br", barcode="",
            scheduled_date=None, boleto_due_date=None, submit=False,
        )
        self.assertEqual(result, {"status": "success", "payment_order": "IPO-2026-00077"})

    def test_the_api_builds_no_document_of_its_own(self):
        self.create()

        self.assertEqual(self.new_orders, [], "with an invoice, only the service creates the order")

    def test_a_boleto_passes_its_barcode_and_the_dates_the_caller_gave(self):
        self.create(
            payment_type="Boleto Payment", pix_key="", barcode=BARCODE,
            boleto_due_date="2026-10-01", scheduled_date="2026-09-30", cmd="brazil_module.api.create_payment_order",
        )

        self.service.assert_called_once_with(
            INVOICE, "Boleto Payment", pix_key="", barcode=BARCODE,
            scheduled_date="2026-09-30", boleto_due_date="2026-10-01", submit=False,
        )

    def test_a_guard_exception_comes_back_as_the_error_message(self):
        self.service.side_effect = Thrown(f"Purchase Invoice {INVOICE} is on hold")

        result = self.create()

        self.assertEqual(result, {"status": "error", "message": f"Purchase Invoice {INVOICE} is on hold"})

    def test_creating_an_order_queues_nothing(self):
        self.create()

        self.enqueue.assert_not_called()


class TestCreateForInvoiceThroughTheRealService(CreateForInvoiceCase):
    """The endpoint, ``create_payment_order_for_invoice`` and the guards together, over one FakeDB."""

    def test_company_party_and_amount_come_from_the_invoice_whatever_the_client_sent(self):
        self.db.add("Supplier", "SUP-EVIL", on_hold=0, hold_type=None, supplier_name="Outro", tax_id="9")

        result = self.create(amount=99999.0, company="Outra Empresa", party="SUP-EVIL")

        self.assertEqual(result["status"], "success", result)
        order = self.db.row(DOCTYPE, result["payment_order"])
        self.assertEqual((order["company"], order["party"], order["amount"]), (COMPANY, "SUP-1", 16800.0))
        self.assertEqual((order["purchase_invoice"], order["inter_company_account"]), (INVOICE, ACCOUNT))
        self.assertEqual(order["recipient_name"], "Fornecedor Ltda")

    def test_the_order_is_left_as_a_draft_and_permissions_are_checked(self):
        result = self.create()

        self.assertEqual(self.events("insert", "submit"), [("insert", DOCTYPE, result["payment_order"])])
        self.assertEqual(self.new_orders[0]._insert_kwargs, {}, "no ignore_permissions")
        self.enqueue.assert_not_called()

    def test_a_boleto_defaults_its_due_date_to_the_invoice(self):
        result = self.create(payment_type="Boleto Payment", pix_key="", barcode=BARCODE)

        order = self.db.row(DOCTYPE, result["payment_order"])
        self.assertEqual((order["barcode"], order["boleto_due_date"]), (BARCODE, DUE_DATE))

    def test_an_invoice_on_hold_is_refused_with_the_reason(self):
        self.db.set_value("Purchase Invoice", INVOICE, "on_hold", 1)
        self.db.commit()

        result = self.create()

        self.assertEqual(result["status"], "error")
        self.assertIn(INVOICE, result["message"])
        self.assertIn("on hold", result["message"])
        self.assertEqual(self.stored_orders(), [])

    def test_an_order_in_flight_blocks_a_second_one_and_is_named(self):
        blocking = ("Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
                    "Needs Verification", "Completed")
        for number, status in enumerate(blocking, start=1):
            invoice, order = f"ACC-PINV-2026-001{number:02d}", f"IPO-2026-001{number:02d}"
            self.add_invoice(invoice)
            self.add_order(status, name=order, purchase_invoice=invoice, docstatus=0 if status == "Draft" else 1)
            with self.subTest(status=status):
                result = self.create(purchase_invoice=invoice)

                self.assertEqual(result["status"], "error")
                self.assertIn(order, result["message"])
        self.assertEqual(self.events("insert"), [])

    def test_a_failed_order_does_not_block_a_new_one(self):
        self.add_order("Failed", name="IPO-2026-00001")

        result = self.create()

        self.assertEqual(result["status"], "success", result)

    def test_a_settled_order_does_not_block_the_next_instalment(self):
        """I6: ``Completed`` with a Payment Entry is already reflected in ``outstanding_amount``."""
        self.add_order("Completed", name="IPO-2026-00001", payment_entry="ACC-PAY-2026-00001")

        result = self.create()

        self.assertEqual(result["status"], "success", result)

    def test_the_amount_of_the_client_cannot_revive_a_settled_invoice(self):
        """The amount is the invoice's: with nothing outstanding there is nothing to order."""
        self.db.set_value("Purchase Invoice", INVOICE, "outstanding_amount", 0)
        self.db.commit()

        result = self.create(amount=16800.0)

        self.assertEqual(result["status"], "error")
        self.assertEqual(self.stored_orders(), [])

    def test_no_inter_account_for_the_company_of_the_invoice(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "sync_enabled", 0)
        self.db.commit()

        result = self.create()

        self.assertEqual(result["status"], "error")
        self.assertIn("Inter Company Account", result["message"])
        self.assertEqual(self.stored_orders(), [])

    def test_an_unknown_invoice(self):
        result = self.create(purchase_invoice="ACC-PINV-404")

        self.assertEqual(result["status"], "error")
        self.assertIn("ACC-PINV-404", result["message"])


class TestCreateLeavesNothingHalfDone(CreateForInvoiceCase):
    def setUp(self):
        super().setUp()
        self.insert_fails_with = RuntimeError("on_update hook exploded")

    def test_error_means_no_order_was_left_behind(self):
        """The request commits when the endpoint returns: a row written before the failure would
        stay, holding ``invoice_lock``, while the user was told that nothing was created."""
        result = self.create()

        self.assertEqual(result, {"status": "error", "message": "on_update hook exploded"})
        self.assertEqual(len(self.events("insert")), 1, "the fake did write the row before failing")
        self.assertEqual(self.stored_orders(), [])

    def test_the_rollback_comes_before_the_error_log(self):
        self.create()

        self.assertEqual(self.events("rollback", "log_error"), [("rollback",), ("log_error",)])

    def test_the_error_log_carries_the_traceback_under_a_fixed_title(self):
        self.create()

        self.assertEqual(
            self.error_logs,
            [((), {"title": "Payment Order Creation Error", "message": "Traceback (most recent call last): ..."})],
        )

    def test_the_returned_message_is_the_only_channel(self):
        """``frappe.throw`` also queued its text for the client; the dialog shows the returned one."""
        self.create()

        self.clear_messages.assert_called_once_with()


# ---------------------------------------------------------------------------
# create_payment_order - without a Purchase Invoice (the existing behaviour)
# ---------------------------------------------------------------------------

class TestCreateWithoutAnInvoice(ApiCase):
    def create(self, **overrides):
        arguments = {
            "payment_type": "PIX", "amount": "250.50", "company": COMPANY, "party_type": "Supplier",
            "party": "SUP-1", "pix_key": "pix@fornecedor.com.br",
        }
        arguments.update(overrides)
        return api.create_payment_order(**arguments)

    def test_the_account_comes_from_get_inter_account_for_company(self):
        lookup = MagicMock(name="get_inter_account_for_company", return_value="Inter - Filial")
        self._patch(_guards, get_inter_account_for_company=lookup)

        result = self.create()

        lookup.assert_called_once_with(COMPANY)
        self.assertEqual(self.db.row(DOCTYPE, result["payment_order"])["inter_company_account"], "Inter - Filial")

    def test_the_order_is_built_from_the_arguments_as_before(self):
        result = self.create()

        self.assertEqual(result["status"], "success", result)
        order = self.db.row(DOCTYPE, result["payment_order"])
        self.assertEqual((order["payment_type"], order["company"], order["amount"]), ("PIX", COMPANY, 250.5))
        self.assertEqual((order["inter_company_account"], order["purchase_invoice"]), (ACCOUNT, None))
        self.assertEqual((order["party_type"], order["party"], order["pix_key"]),
                         ("Supplier", "SUP-1", "pix@fornecedor.com.br"))
        self.assertEqual((order["recipient_name"], order["recipient_cpf_cnpj"]), ("Fornecedor Ltda", "12345678000190"))
        self.assertEqual(self.events("insert", "submit"), [("insert", DOCTYPE, result["payment_order"])])

    def test_a_boleto_keeps_its_barcode_and_its_due_date(self):
        """The due date is now mandatory (the bank requires dataVencimento), so it must arrive.

        Without a Purchase Invoice there is nothing to take it from: dropping it here would make
        every boleto order created through this endpoint impossible to save.
        """
        result = self.create(
            payment_type="Boleto Payment", pix_key="", barcode=BARCODE, boleto_due_date="2026-09-25",
        )

        order = self.db.row(DOCTYPE, result["payment_order"])
        self.assertEqual(result["status"], "success", result)
        self.assertEqual((order["barcode"], str(order["boleto_due_date"])), (BARCODE, "2026-09-25"))

    def test_a_scheduled_date_is_not_dropped(self):
        result = self.create(scheduled_date="2026-09-25")

        self.assertEqual(str(self.db.row(DOCTYPE, result["payment_order"])["scheduled_date"]), "2026-09-25")

    def test_the_invoice_service_is_not_involved(self):
        service = MagicMock(name="create_payment_order_for_invoice")
        self._patch(_ps_mod, create_payment_order_for_invoice=service)

        self.create()

        service.assert_not_called()

    def test_no_inter_account_no_order(self):
        self.db.set_value("Inter Company Account", ACCOUNT, "sync_enabled", 0)
        self.db.commit()

        result = self.create()

        self.assertEqual(result, {"status": "error", "message": "No Inter Company Account for this company"})
        self.assertEqual(self.new_orders, [])

    def test_a_failed_insert_is_rolled_back_and_reported(self):
        self.insert_fails_with = RuntimeError("after_insert hook exploded")

        result = self.create()

        self.assertEqual(result, {"status": "error", "message": "after_insert hook exploded"})
        self.assertEqual(self.stored_orders(), [])
        self.assertEqual(self.events("rollback", "log_error"), [("rollback",), ("log_error",)])


# ---------------------------------------------------------------------------
# execute_payment
# ---------------------------------------------------------------------------

class TestExecutePaymentDelegates(ApiCase):
    def test_it_is_the_document_that_executes(self):
        document = MagicMock(name="Inter Payment Order")
        document.execute_payment.return_value = {"status": "queued", "payment_order": ORDER}
        get_doc = MagicMock(name="frappe.get_doc", return_value=document)
        _shadow_frappe(self, get_doc=get_doc)

        result = api.execute_payment(ORDER)

        get_doc.assert_called_once_with(DOCTYPE, ORDER)
        document.execute_payment.assert_called_once_with()
        self.assertEqual(result, {"status": "queued", "payment_order": ORDER})

    def test_the_api_queues_nothing_itself(self):
        _shadow_frappe(self, get_doc=MagicMock(name="frappe.get_doc"))

        api.execute_payment(ORDER)

        self.enqueue.assert_not_called()
        self.assertNotIn("frappe.enqueue", inspect.getsource(api.execute_payment))

    def test_a_refusal_of_the_document_is_not_swallowed(self):
        document = MagicMock(name="Inter Payment Order")
        document.execute_payment.side_effect = Thrown("Only approved payments can be executed")
        _shadow_frappe(self, get_doc=MagicMock(return_value=document))

        with self.assertRaises(Thrown):
            api.execute_payment(ORDER)


class TestExecutePaymentThroughTheRealController(ApiCase):
    """The endpoint, the controller and ``enqueue_payment_execution`` together, over one FakeDB."""

    def order_writes(self):
        return [event for event in self.events("set_value") if event[1] == DOCTYPE]

    def test_a_forged_status_in_the_request_document_is_ignored(self):
        """``run_doc_method`` rebuilds the document from the browser's JSON, so it can say anything.

        The other tests here load the document FROM the row, so ``self.status`` and the stored
        status always agree - they cannot tell a database re-read from a trusting one.
        """
        self.add_order("Needs Verification")
        order = InterPaymentOrder.__new__(InterPaymentOrder)
        for key, value in self.db.row(DOCTYPE, ORDER).items():
            setattr(order, key, value)
        order.check_permission = self.check_permission
        order.status, order.docstatus = "Approved", 1  # what the browser claims
        _shadow_frappe(self, get_doc=MagicMock(return_value=order))

        with self.assertRaises(Thrown):
            api.execute_payment(ORDER)

        self.enqueue.assert_not_called()
        self.assertEqual(self.order_writes(), [])

    def test_an_approved_order_is_queued_once_as_its_own_deduplicated_job(self):
        self.add_order("Approved")

        result = api.execute_payment(ORDER)

        self.assertEqual(result, {"status": "queued", "payment_order": ORDER})
        self.assertEqual(self.enqueue.call_count, 1)
        job = self.enqueue.call_args
        self.assertIs(job.args[0], _ps_mod.execute_payment_order)
        self.assertEqual((job.kwargs["job_id"], job.kwargs["deduplicate"]), (f"inter_payment_order::{ORDER}", True))
        self.assertEqual(job.kwargs["payment_order_name"], ORDER)
        self.check_permission.assert_called_once_with("submit")

    def test_the_status_is_not_written_by_the_request(self):
        """I2/I3: only the job claims the order."""
        self.add_order("Approved")

        api.execute_payment(ORDER)

        self.assertEqual(self.order_writes(), [])
        self.assertEqual(self.db.row(DOCTYPE, ORDER)["status"], "Approved")

    def test_an_order_that_is_not_approved_is_never_queued(self):
        docstatus = {"Draft": 0, "Cancelled": 2}
        for number, status in enumerate(ALL_STATUSES, start=1):
            if status == "Approved":
                continue
            order = f"IPO-2026-001{number:02d}"
            self.add_order(
                status, name=order, purchase_invoice=f"ACC-PINV-2026-001{number:02d}",
                docstatus=docstatus.get(status, 1),
            )
            with self.subTest(status=status):
                with self.assertRaises(Thrown):
                    api.execute_payment(order)
        self.enqueue.assert_not_called()
        self.assertEqual(self.order_writes(), [])

    def test_an_approved_order_that_is_not_submitted_is_never_queued(self):
        """``status`` alone proves nothing: a draft or a cancelled row that says Approved stays put."""
        for number, docstatus in enumerate((0, 2), start=1):
            order = f"IPO-2026-002{number:02d}"
            self.add_order(
                "Approved", name=order, purchase_invoice=f"ACC-PINV-2026-002{number:02d}", docstatus=docstatus,
            )
            with self.subTest(docstatus=docstatus):
                with self.assertRaises(Thrown):
                    api.execute_payment(order)
        self.enqueue.assert_not_called()

    def test_the_kill_switch_blocks_the_request(self):
        self.db.singles["Banco Inter Settings"]["enabled"] = 0
        self.add_order("Approved")

        with self.assertRaises(Thrown):
            api.execute_payment(ORDER)

        self.enqueue.assert_not_called()

    def test_without_submit_permission_nothing_is_queued(self):
        self.add_order("Approved")
        self.check_permission.side_effect = PermissionError("no submit permission")

        with self.assertRaises(PermissionError):
            api.execute_payment(ORDER)

        self.enqueue.assert_not_called()

    def test_a_second_click_is_told_that_the_job_already_exists(self):
        self.add_order("Approved")
        self.enqueue.return_value = None  # frappe.enqueue(deduplicate=True): already queued or started

        result = api.execute_payment(ORDER)

        self.assertEqual(result, {"status": "already_queued", "payment_order": ORDER})


# ---------------------------------------------------------------------------
# i8_run_payment_scheduling
# ---------------------------------------------------------------------------

class TestRunPaymentScheduling(ApiCase):
    def test_only_managers_may_start_it(self):
        api.i8_run_payment_scheduling()

        self.only_for.assert_called_once_with(MANAGER_ROLES)

    def test_anyone_else_is_stopped_before_anything_is_queued(self):
        self.only_for.side_effect = PermissionError("not a Banco Inter Manager")

        with self.assertRaises(PermissionError):
            api.i8_run_payment_scheduling()

        self.enqueue.assert_not_called()

    def test_the_role_check_comes_first(self):
        calls = []
        self.only_for.side_effect = lambda *args, **kwargs: calls.append("only_for")
        self.enqueue.side_effect = lambda *args, **kwargs: calls.append("enqueue") or MagicMock()

        api.i8_run_payment_scheduling()

        self.assertEqual(calls, ["only_for", "enqueue"])

    def test_the_kill_switch_blocks_it(self):
        self.db.singles["Banco Inter Settings"]["enabled"] = 0

        result = api.i8_run_payment_scheduling()

        self.assertEqual(result["status"], "blocked")
        self.enqueue.assert_not_called()
        self.only_for.assert_called_once_with(MANAGER_ROLES)

    def test_a_blocked_run_tells_the_user_why(self):
        """The settings form only reacts to ``queued``: the server message is what the user sees."""
        self.db.singles["Banco Inter Settings"]["enabled"] = 0

        result = api.i8_run_payment_scheduling()

        self.assertIn("disabled", result["message"])
        self.assertIn("disabled", str(self.msgprint.call_args.args[0]))

    def test_it_is_one_deduplicated_job_on_the_long_queue(self):
        result = api.i8_run_payment_scheduling()

        self.enqueue.assert_called_once_with(
            WEEKLY_JOB, queue="long", timeout=1500, job_id="inter_weekly_payments", deduplicate=True,
        )
        self.assertEqual(result, {"status": "queued"})

    def test_a_run_already_queued_is_not_queued_again(self):
        self.enqueue.return_value = None  # frappe.enqueue(deduplicate=True): already queued or started

        result = api.i8_run_payment_scheduling()

        self.assertEqual(result["status"], "already_queued")
        self.assertEqual(self.enqueue.call_count, 1)
        self.assertIn("already", str(self.msgprint.call_args.args[0]))


# ---------------------------------------------------------------------------
# The "Pay via Inter" dialog of the Purchase Invoice
# ---------------------------------------------------------------------------

class TestPayViaInterDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(DIALOG_JS_PATH) as fh:
            cls.source = fh.read()

    def test_ted_is_no_longer_offered(self):
        self.assertNotIn("TED", self.source)
        self.assertIn('options: "PIX\\nBoleto Payment"', self.source)

    def test_it_still_creates_the_order_through_the_api(self):
        self.assertIn('method: "brazil_module.api.create_payment_order"', self.source)
        self.assertIn("purchase_invoice: frm.doc.name", self.source)

    def test_a_refusal_is_shown_to_the_user(self):
        """Guard failures come back as ``{"status": "error", "message": ...}`` - not as an exception."""
        self.assertIn("frappe.msgprint", self.source)
        self.assertIn("result.message", self.source)


if __name__ == "__main__":
    unittest.main()
