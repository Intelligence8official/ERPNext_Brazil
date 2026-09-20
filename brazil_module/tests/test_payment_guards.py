"""Tests for the read-only payment guards (spec 4.1, invariants I6 and I8)."""

import unittest
from unittest.mock import patch

from brazil_module.tests._payment_fakes import DOCTYPE as FAKES_DOCTYPE
from brazil_module.tests._payment_fakes import FakeDB, install_frappe_mock, patch_frappe_db

frappe = install_frappe_mock()

import brazil_module.services.banking.payment_guards as guards
from brazil_module.services.banking.payment_guards import (
    AMOUNT_TOLERANCE,
    CANCELLABLE_STATUSES,
    DOCTYPE,
    IN_FLIGHT_STATUSES,
    NON_BLOCKING_STATUSES,
    check_invoice_payable,
    find_blocking_payment_order,
    find_draft_payment_entry,
    get_inter_account_for_company,
    is_blocking,
    is_integration_enabled,
)

ALL_STATUSES = (
    "Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
    "Needs Verification", "Completed", "Failed", "Cancelled",
)
INVOICE = "ACC-PINV-2026-00031"


def _flt(value, precision=None):
    number = float(value or 0)
    return round(number, precision) if precision is not None else number


class GuardCase(unittest.TestCase):
    """A payable invoice, an enabled integration, and nothing in the way."""

    def setUp(self):
        self.db = FakeDB(singles={"Banco Inter Settings": {"enabled": 1}})
        self.db.add(
            "Purchase Invoice", INVOICE, docstatus=1, on_hold=0, outstanding_amount=16800.0,
            supplier="SUP-1", company="Intelligence8",
        )
        self.db.add("Supplier", "SUP-1", on_hold=0, hold_type=None)
        patch_frappe_db(self, self.db)
        # `from frappe import _` / `from frappe.utils import flt` were bound at import time.
        for name, value in (("_", lambda text: text), ("flt", _flt)):
            patcher = patch.object(guards, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_order(self, name, status, docstatus=1, invoice=INVOICE, **fields):
        self.db.add(DOCTYPE, name, status=status, docstatus=docstatus, purchase_invoice=invoice, **fields)

    def add_payment_entry(self, name, docstatus=0, invoice=INVOICE, order=None, reference_doctype="Purchase Invoice"):
        self.db.add("Payment Entry", name, docstatus=docstatus, inter_payment_order=order)
        self.db.add(
            "Payment Entry Reference", f"{name}-ref", parent=name, parenttype="Payment Entry",
            docstatus=docstatus, reference_doctype=reference_doctype, reference_name=invoice,
        )


class TestConstants(unittest.TestCase):
    def test_are_exactly_the_ones_of_the_spec(self):
        self.assertEqual(DOCTYPE, "Inter Payment Order")
        self.assertEqual(IN_FLIGHT_STATUSES, ("Processing", "Awaiting Bank", "Needs Verification"))
        self.assertEqual(NON_BLOCKING_STATUSES, ("Failed", "Cancelled"))
        self.assertEqual(CANCELLABLE_STATUSES, ("Draft", "Pending Approval", "Approved", "Failed"))
        self.assertEqual(AMOUNT_TOLERANCE, 0.01)

    def test_the_fakes_talk_about_the_same_doctype(self):
        self.assertEqual(FAKES_DOCTYPE, DOCTYPE)

    def test_an_in_flight_order_can_never_be_cancelled(self):
        self.assertEqual(set(IN_FLIGHT_STATUSES) & set(CANCELLABLE_STATUSES), set())
        self.assertNotIn("Completed", CANCELLABLE_STATUSES)

    def test_every_status_named_by_a_constant_exists(self):
        named = set(IN_FLIGHT_STATUSES) | set(NON_BLOCKING_STATUSES) | set(CANCELLABLE_STATUSES)
        self.assertLessEqual(named, set(ALL_STATUSES))


class TestIsIntegrationEnabled(GuardCase):
    def test_on(self):
        self.assertIs(is_integration_enabled(), True)

    def test_off(self):
        self.db.singles["Banco Inter Settings"]["enabled"] = 0
        self.assertIs(is_integration_enabled(), False)

    def test_never_configured_is_off(self):
        self.db.singles.clear()
        self.assertIs(is_integration_enabled(), False)


class TestIsBlocking(unittest.TestCase):
    def test_failed_and_cancelled_do_not_block(self):
        for status in ("Failed", "Cancelled"):
            self.assertFalse(is_blocking(status, None), status)
            self.assertFalse(is_blocking(status, "ACC-PAY-1"), status)

    def test_completed_with_a_payment_entry_does_not_block(self):
        self.assertFalse(is_blocking("Completed", "ACC-PAY-1"))

    def test_completed_without_a_payment_entry_blocks(self):
        self.assertTrue(is_blocking("Completed", None))
        self.assertTrue(is_blocking("Completed", ""))

    def test_every_other_status_blocks_with_or_without_a_payment_entry(self):
        for status in ALL_STATUSES:
            if status in ("Failed", "Cancelled", "Completed"):
                continue
            self.assertTrue(is_blocking(status, None), status)
            self.assertTrue(is_blocking(status, "ACC-PAY-1"), status)

    def test_an_unknown_status_blocks(self):
        # The safe direction of failure is an order that keeps protecting its invoice.
        self.assertTrue(is_blocking("Something New", None))
        self.assertTrue(is_blocking(None, None))

    def test_returns_a_bool(self):
        self.assertIs(is_blocking("Completed", "ACC-PAY-1"), False)
        self.assertIs(is_blocking("Completed", ""), True)


class TestFindBlockingPaymentOrder(GuardCase):
    def test_nothing_blocks_an_invoice_without_orders(self):
        self.assertIsNone(find_blocking_payment_order(INVOICE))

    def test_returns_the_name_and_status_of_the_blocking_order(self):
        self.add_order("IPO-1", "Needs Verification")

        self.assertEqual(find_blocking_payment_order(INVOICE), {"name": "IPO-1", "status": "Needs Verification"})

    def test_every_in_flight_status_blocks(self):
        self.add_order("IPO-1", "Approved")
        for status in IN_FLIGHT_STATUSES:
            with self.subTest(status=status):
                self.db.row(DOCTYPE, "IPO-1")["status"] = status

                self.assertEqual(find_blocking_payment_order(INVOICE), {"name": "IPO-1", "status": status})

    def test_a_draft_order_blocks_too(self):
        self.add_order("IPO-1", "Draft", docstatus=0)

        self.assertEqual(find_blocking_payment_order(INVOICE), {"name": "IPO-1", "status": "Draft"})

    def test_ignores_the_excluded_order(self):
        self.add_order("IPO-1", "Approved")

        self.assertIsNone(find_blocking_payment_order(INVOICE, exclude="IPO-1"))

    def test_the_exclusion_does_not_hide_another_order(self):
        self.add_order("IPO-1", "Approved")
        self.add_order("IPO-2", "Awaiting Bank")

        self.assertEqual(find_blocking_payment_order(INVOICE, exclude="IPO-1")["name"], "IPO-2")

    def test_ignores_cancelled_documents_whatever_their_status_says(self):
        self.add_order("IPO-1", "Approved", docstatus=2)

        self.assertIsNone(find_blocking_payment_order(INVOICE))

    def test_ignores_failed_and_cancelled_orders(self):
        self.add_order("IPO-1", "Failed")
        self.add_order("IPO-2", "Cancelled")

        self.assertIsNone(find_blocking_payment_order(INVOICE))

    def test_ignores_a_completed_order_that_has_its_payment_entry(self):
        self.add_order("IPO-1", "Completed", payment_entry="ACC-PAY-1")

        self.assertIsNone(find_blocking_payment_order(INVOICE))

    def test_a_completed_order_without_payment_entry_blocks(self):
        self.add_order("IPO-1", "Completed", payment_entry=None)

        self.assertEqual(find_blocking_payment_order(INVOICE), {"name": "IPO-1", "status": "Completed"})

    def test_a_failed_order_does_not_hide_a_blocking_one(self):
        self.add_order("IPO-1", "Failed")
        self.add_order("IPO-2", "Processing")

        self.assertEqual(find_blocking_payment_order(INVOICE)["name"], "IPO-2")

    def test_ignores_orders_of_another_invoice(self):
        self.add_order("IPO-1", "Processing", invoice="ACC-PINV-2026-00099")

        self.assertIsNone(find_blocking_payment_order(INVOICE))

    def test_without_an_invoice_there_is_nothing_to_look_for(self):
        self.add_order("IPO-1", "Processing", invoice=None)

        self.assertIsNone(find_blocking_payment_order(None))
        self.assertIsNone(find_blocking_payment_order(""))


class TestFindDraftPaymentEntry(GuardCase):
    def test_no_entry(self):
        self.assertIsNone(find_draft_payment_entry(INVOICE))

    def test_returns_the_draft_entry_that_references_the_invoice(self):
        self.add_payment_entry("ACC-PAY-1")

        self.assertEqual(find_draft_payment_entry(INVOICE), {"name": "ACC-PAY-1"})

    def test_ignores_submitted_and_cancelled_entries(self):
        self.add_payment_entry("ACC-PAY-1", docstatus=1)
        self.add_payment_entry("ACC-PAY-2", docstatus=2)

        self.assertIsNone(find_draft_payment_entry(INVOICE))

    def test_trusts_the_docstatus_of_the_entry_not_of_the_child_row(self):
        self.add_payment_entry("ACC-PAY-1", docstatus=0)
        self.db.row("Payment Entry Reference", "ACC-PAY-1-ref")["docstatus"] = 1

        self.assertEqual(find_draft_payment_entry(INVOICE), {"name": "ACC-PAY-1"})

    def test_ignores_the_entry_of_the_excluded_order(self):
        self.add_payment_entry("ACC-PAY-1", order="IPO-1")

        self.assertIsNone(find_draft_payment_entry(INVOICE, exclude_order="IPO-1"))

    def test_the_entry_of_another_order_is_not_excluded(self):
        self.add_payment_entry("ACC-PAY-1", order="IPO-2")

        self.assertEqual(find_draft_payment_entry(INVOICE, exclude_order="IPO-1"), {"name": "ACC-PAY-1"})

    def test_an_entry_without_order_is_found_even_when_an_order_is_excluded(self):
        # inter_payment_order is NULL on entries typed by a human: NULL != 'IPO-1' is not TRUE in SQL.
        self.add_payment_entry("ACC-PAY-1", order=None)

        self.assertEqual(find_draft_payment_entry(INVOICE, exclude_order="IPO-1"), {"name": "ACC-PAY-1"})

    def test_the_excluded_entry_does_not_hide_another_draft(self):
        self.add_payment_entry("ACC-PAY-1", order="IPO-1")
        self.add_payment_entry("ACC-PAY-2", order=None)

        self.assertEqual(find_draft_payment_entry(INVOICE, exclude_order="IPO-1"), {"name": "ACC-PAY-2"})

    def test_ignores_references_to_other_documents(self):
        self.add_payment_entry("ACC-PAY-1", invoice="ACC-PINV-2026-00099")
        self.add_payment_entry("ACC-PAY-2", reference_doctype="Purchase Order")

        self.assertIsNone(find_draft_payment_entry(INVOICE))

    def test_uses_no_raw_sql(self):
        self.add_payment_entry("ACC-PAY-1")

        find_draft_payment_entry(INVOICE)

        self.assertEqual(self.db.sql_calls, [])


class TestCheckInvoicePayable(GuardCase):
    def test_a_submitted_invoice_with_enough_outstanding_is_payable(self):
        self.assertIsNone(check_invoice_payable(INVOICE, 16800.0))

    def test_a_partial_payment_is_payable(self):
        self.assertIsNone(check_invoice_payable(INVOICE, 5000.0))

    def test_an_unknown_invoice(self):
        reason = check_invoice_payable("ACC-PINV-404", 10.0)

        self.assertIsInstance(reason, str)
        self.assertIn("ACC-PINV-404", reason)
        self.assertIn("not found", reason)

    def test_no_invoice_at_all_is_a_reason_not_a_free_pass(self):
        # frappe.db.get_value(doctype, None, ...) reads a Single: never let that answer "payable".
        for missing in (None, ""):
            self.assertIn("No Purchase Invoice", check_invoice_payable(missing, 10.0))
        self.assertEqual(self.db.events, [])

    def test_a_draft_invoice(self):
        self.db.row("Purchase Invoice", INVOICE)["docstatus"] = 0

        reason = check_invoice_payable(INVOICE, 16800.0)

        self.assertIn(INVOICE, reason)
        self.assertIn("not submitted", reason)

    def test_a_cancelled_invoice(self):
        self.db.row("Purchase Invoice", INVOICE)["docstatus"] = 2

        reason = check_invoice_payable(INVOICE, 16800.0)

        self.assertIn(INVOICE, reason)
        self.assertIn("cancelled", reason)

    def test_an_invoice_on_hold(self):
        self.db.row("Purchase Invoice", INVOICE)["on_hold"] = 1

        reason = check_invoice_payable(INVOICE, 16800.0)

        self.assertIn(INVOICE, reason)
        self.assertIn("on hold", reason)

    def test_a_supplier_on_payment_hold(self):
        for hold_type in ("All", "Payments"):
            with self.subTest(hold_type=hold_type):
                self.db.row("Supplier", "SUP-1").update(on_hold=1, hold_type=hold_type)

                reason = check_invoice_payable(INVOICE, 16800.0)

                self.assertIn("SUP-1", reason)
                self.assertIn("hold", reason)

    def test_a_supplier_held_for_invoices_only_can_still_be_paid(self):
        self.db.row("Supplier", "SUP-1").update(on_hold=1, hold_type="Invoices")

        self.assertIsNone(check_invoice_payable(INVOICE, 16800.0))

    def test_a_hold_type_left_behind_without_the_hold_does_not_block(self):
        self.db.row("Supplier", "SUP-1").update(on_hold=0, hold_type="All")

        self.assertIsNone(check_invoice_payable(INVOICE, 16800.0))

    def test_a_company_mismatch(self):
        reason = check_invoice_payable(INVOICE, 16800.0, company="Other Company")

        self.assertIn(INVOICE, reason)
        self.assertIn("Intelligence8", reason)
        self.assertIn("Other Company", reason)

    def test_the_same_company_is_fine_and_no_company_is_not_checked(self):
        self.assertIsNone(check_invoice_payable(INVOICE, 16800.0, company="Intelligence8"))
        self.assertIsNone(check_invoice_payable(INVOICE, 16800.0, company=None))

    def test_not_enough_outstanding(self):
        self.db.row("Purchase Invoice", INVOICE)["outstanding_amount"] = 0.0

        reason = check_invoice_payable(INVOICE, 16800.0)

        self.assertIn(INVOICE, reason)
        self.assertIn("16800.00", reason)
        self.assertIn("0.00", reason)

    def test_the_tolerance_is_one_cent(self):
        self.db.row("Purchase Invoice", INVOICE)["outstanding_amount"] = 100.0

        self.assertIsNone(check_invoice_payable(INVOICE, 100.01))
        self.assertIsNotNone(check_invoice_payable(INVOICE, 100.02))

    def test_float_noise_does_not_decide_the_boundary(self):
        # In binary floats 2.03 + 0.01 < 2.04 is True: compared naively, one cent of
        # tolerance is refused for about one amount in five. The guard compares cents.
        self.assertLess(2.03 + AMOUNT_TOLERANCE, 2.04)
        for outstanding in (2.03, 2.17, 2.59, 0.1 + 0.2):
            with self.subTest(outstanding=outstanding):
                self.db.row("Purchase Invoice", INVOICE)["outstanding_amount"] = outstanding

                self.assertIsNone(check_invoice_payable(INVOICE, round(outstanding + 0.01, 2)))
                self.assertIsNotNone(check_invoice_payable(INVOICE, round(outstanding + 0.02, 2)))

    def test_a_payment_of_nothing_is_not_payable(self):
        for amount in (0, 0.0, -10.0, None):
            with self.subTest(amount=amount):
                self.assertIn("greater than zero", check_invoice_payable(INVOICE, amount))

    def test_an_invoice_without_outstanding_amount_is_not_payable(self):
        self.db.row("Purchase Invoice", INVOICE)["outstanding_amount"] = None

        self.assertIsNotNone(check_invoice_payable(INVOICE, 16800.0))

    def test_another_blocking_order(self):
        self.add_order("IPO-2026-00001", "Needs Verification")

        reason = check_invoice_payable(INVOICE, 16800.0)

        self.assertIn("IPO-2026-00001", reason)
        self.assertIn("Needs Verification", reason)
        self.assertIn(INVOICE, reason)

    def test_the_order_being_checked_does_not_block_itself(self):
        self.add_order("IPO-2026-00001", "Approved")

        self.assertIsNone(check_invoice_payable(INVOICE, 16800.0, order_name="IPO-2026-00001"))
        self.assertIsNotNone(check_invoice_payable(INVOICE, 16800.0, order_name="IPO-2026-00002"))

    def test_a_failed_order_does_not_block_the_next_attempt(self):
        self.add_order("IPO-2026-00001", "Failed")

        self.assertIsNone(check_invoice_payable(INVOICE, 16800.0))

    def test_a_draft_payment_entry(self):
        self.add_payment_entry("ACC-PAY-2026-00007")

        reason = check_invoice_payable(INVOICE, 16800.0)

        self.assertIn("ACC-PAY-2026-00007", reason)
        self.assertIn(INVOICE, reason)

    def test_the_draft_entry_of_the_order_being_checked_does_not_block_it(self):
        self.add_payment_entry("ACC-PAY-2026-00007", order="IPO-2026-00001")

        self.assertIsNone(check_invoice_payable(INVOICE, 16800.0, order_name="IPO-2026-00001"))
        self.assertIsNotNone(check_invoice_payable(INVOICE, 16800.0))

    def test_a_submitted_payment_entry_is_already_in_the_outstanding_amount(self):
        self.add_payment_entry("ACC-PAY-2026-00007", docstatus=1)

        self.assertIsNone(check_invoice_payable(INVOICE, 16800.0))

    def test_the_guard_only_reads(self):
        self.add_order("IPO-2026-00001", "Approved")
        self.add_payment_entry("ACC-PAY-2026-00007")

        check_invoice_payable(INVOICE, 16800.0)
        check_invoice_payable(INVOICE, 16800.0, order_name="IPO-2026-00001", company="Intelligence8")

        kinds = {event[0] for event in self.db.events}
        self.assertLessEqual(kinds, {"get_value"})
        self.assertEqual(self.db.sql_calls, [])


class TestGetInterAccountForCompany(GuardCase):
    def test_returns_the_sync_enabled_account_of_the_company(self):
        self.db.add("Inter Company Account", "Inter - Other", company="Other Company", sync_enabled=1)
        self.db.add("Inter Company Account", "Inter - I8", company="Intelligence8", sync_enabled=1)

        self.assertEqual(get_inter_account_for_company("Intelligence8"), "Inter - I8")

    def test_ignores_an_account_with_sync_disabled(self):
        self.db.add("Inter Company Account", "Inter - I8", company="Intelligence8", sync_enabled=0)

        self.assertIsNone(get_inter_account_for_company("Intelligence8"))

    def test_no_account(self):
        self.assertIsNone(get_inter_account_for_company("Intelligence8"))
        self.assertIsNone(get_inter_account_for_company(None))

    def test_filters_by_the_fields_that_exist(self):
        # The weekly scheduler filtered by `enabled`, a field Inter Company Account does not have.
        get_inter_account_for_company("Intelligence8")

        self.assertEqual(
            self.db.reads[-1]["filters"], {"company": "Intelligence8", "sync_enabled": 1},
        )
        self.assertEqual(self.db.reads[-1]["doctype"], "Inter Company Account")


if __name__ == "__main__":
    unittest.main()
