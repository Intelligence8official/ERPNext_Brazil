"""Tests for the shared payment fakes. Every other payment test file stands on them
(a bare MagicMock frappe cannot fail), so they are tested like production code."""

import ast
import copy
import datetime
import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from brazil_module.tests._payment_fakes import (
    DOCTYPE,
    FakeDB,
    FakeInterClient,
    FakeSubmittedDoc,
    UniqueViolationError,
    UpdateAfterSubmitError,
    install_frappe_mock,
    patch_frappe,
    patch_frappe_db,
)

KEY = "0b0f3c9e-5a1d-4a57-9d0e-3f1f4f6f7a10"
OTHER_KEY = "11111111-2222-4333-8444-555555555555"
T0 = datetime.datetime(2026, 9, 20, 10, 0, 0)


def _db_with_order(**overrides) -> FakeDB:
    db = FakeDB()
    fields = {"status": "Approved", "docstatus": 1, "payment_type": "PIX", "amount": 16800.0}
    fields.update(overrides)
    db.add(DOCTYPE, "IPO-1", **fields)
    return db


def _claim(db: FakeDB, commit: bool = True) -> None:
    db.set_value(DOCTYPE, "IPO-1", {"status": "Processing", "idempotency_key": KEY, "bank_request_at": T0})
    if commit:
        db.commit()


def _claimed_client(**order) -> tuple[FakeDB, FakeInterClient]:
    """An order whose claim is committed, and the bank client that serves it."""
    db = _db_with_order(**order)
    _claim(db)
    return db, FakeInterClient(db, doctype_row=(DOCTYPE, "IPO-1"))


class TestInstallFrappeMock(unittest.TestCase):
    def test_injects_a_mock_with_a_pass_through_translation(self):
        with patch.dict(sys.modules):
            sys.modules.pop("frappe", None)
            sys.modules.pop("frappe.utils", None)

            mock = install_frappe_mock()

            self.assertIsInstance(mock, MagicMock)
            self.assertIs(sys.modules["frappe"], mock)
            self.assertIs(sys.modules["frappe.utils"], mock.utils)
            self.assertEqual(mock._("Invoice {0}"), "Invoice {0}")

    def test_keeps_the_mock_another_test_module_already_installed(self):
        with patch.dict(sys.modules):
            first = install_frappe_mock()
            self.assertIs(install_frappe_mock(), first)

    def test_registers_frappe_utils_when_the_installed_mock_came_without_it(self):
        # test_qrcode_gen.py installs a bare mock; `from frappe.utils import ...` must still work after it.
        with patch.dict(sys.modules):
            bare = MagicMock()
            sys.modules["frappe"] = bare
            sys.modules.pop("frappe.utils", None)

            self.assertIs(install_frappe_mock(), bare)
            self.assertIs(sys.modules["frappe.utils"], bare.utils)

    def test_leaves_an_already_registered_frappe_utils_alone(self):
        with patch.dict(sys.modules):
            installed = install_frappe_mock()
            registered = sys.modules["frappe.utils"]

            self.assertIs(install_frappe_mock(), installed)
            self.assertIs(sys.modules["frappe.utils"], registered)


class TestFakeDBGetValue(unittest.TestCase):
    def setUp(self):
        self.db = _db_with_order(purchase_invoice="PINV-1", payment_entry=None)

    def test_one_field_is_a_scalar_and_several_are_a_tuple_like_frappe(self):
        self.assertEqual(self.db.get_value(DOCTYPE, "IPO-1", "status"), "Approved")
        self.assertEqual(self.db.get_value(DOCTYPE, "IPO-1"), "IPO-1")  # the default field is the name
        self.assertEqual(self.db.get_value(DOCTYPE, "IPO-1", ["status"]), "Approved")
        self.assertEqual(self.db.get_value(DOCTYPE, "IPO-1", ["status", "docstatus"]), ("Approved", 1))

    def test_as_dict_gives_attribute_access(self):
        row = self.db.get_value(DOCTYPE, "IPO-1", ["status", "payment_entry"], as_dict=True)

        self.assertEqual(row.status, "Approved")
        self.assertEqual(row["status"], "Approved")
        self.assertIsNone(row.payment_entry)

    def test_a_missing_row_is_none(self):
        self.assertIsNone(self.db.get_value(DOCTYPE, "IPO-404", "status"))
        self.assertIsNone(self.db.get_value(DOCTYPE, "IPO-404", ["status", "docstatus"], as_dict=True))
        self.assertIsNone(self.db.get_value("Unknown DocType", "X", "status"))

    def test_the_result_is_a_copy_that_can_itself_be_copied(self):
        row = self.db.get_value(DOCTYPE, "IPO-1", ["status"], as_dict=True)
        row["status"] = "Completed"

        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["status"], "Approved")
        self.assertEqual(copy.deepcopy(row).status, "Completed")

    def test_for_update_is_recorded_in_the_events(self):
        self.db.get_value(DOCTYPE, "IPO-1", "status")
        self.db.get_value(DOCTYPE, "IPO-1", ["status", "docstatus"], as_dict=True, for_update=True)

        self.assertEqual(
            self.db.events,
            [("get_value", DOCTYPE, "IPO-1", False), ("get_value", DOCTYPE, "IPO-1", True)],
        )

    def test_the_fields_that_were_read_are_recorded(self):
        self.db.get_value(DOCTYPE, "IPO-1", ["status", "idempotency_key"], as_dict=True, for_update=True)

        self.assertEqual(
            self.db.reads,
            [{"doctype": DOCTYPE, "filters": "IPO-1", "fields": ["status", "idempotency_key"], "for_update": True}],
        )

    def test_dict_filters_match_by_equality(self):
        self.assertEqual(self.db.get_value(DOCTYPE, {"purchase_invoice": "PINV-1"}), "IPO-1")
        self.assertIsNone(self.db.get_value(DOCTYPE, {"purchase_invoice": "PINV-2"}))
        self.assertIsNone(self.db.get_value(DOCTYPE, {"purchase_invoice": "PINV-1", "status": "Failed"}))

    def test_get_values_is_a_list_of_rows(self):
        self.db.add(DOCTYPE, "IPO-2", status="Failed", docstatus=1)

        self.assertEqual(
            self.db.get_values(DOCTYPE, {"docstatus": 1}, ["name", "status"]),
            [("IPO-1", "Approved"), ("IPO-2", "Failed")],
        )
        self.assertEqual(self.db.get_values(DOCTYPE, "IPO-404", "status"), [])

    def test_none_filters_read_a_single_doctype(self):
        db = FakeDB(singles={"Banco Inter Settings": {"enabled": 1}})

        self.assertEqual(db.get_value("Banco Inter Settings", None, "enabled"), 1)


class TestFakeDBFilterOperators(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        rows = (
            ("A", "Processing", 1, 10.0, "abc", T0),
            ("B", "Failed", 1, 20.0, None, None),
            ("C", "Cancelled", 2, 30.0, "", None),
        )
        for name, status, docstatus, amount, code, sent_at in rows:
            self.db.add(
                DOCTYPE, name, status=status, docstatus=docstatus, amount=amount,
                approval_code=code, bank_request_at=sent_at,
            )

    def _names(self, filters):
        return self.db.get_all(DOCTYPE, filters=filters, pluck="name")

    def test_in_not_in_and_not_equal(self):
        self.assertEqual(self._names({"status": ["in", ["Processing", "Failed"]]}), ["A", "B"])
        self.assertEqual(self._names({"status": ["not in", ("Failed", "Cancelled")]}), ["A"])
        self.assertEqual(self._names({"status": ["!=", "Failed"]}), ["A", "C"])

    def test_less_and_greater(self):
        self.assertEqual(self._names({"docstatus": ["<", 2]}), ["A", "B"])
        self.assertEqual(self._names({"amount": [">", 10]}), ["B", "C"])
        self.assertEqual(self._names({"amount": [">=", 20.0]}), ["B", "C"])
        self.assertEqual(self._names({"amount": ["<=", 10]}), ["A"])

    def test_is_set_and_is_not_set_treat_empty_strings_as_null(self):
        self.assertEqual(self._names({"approval_code": ["is", "set"]}), ["A"])
        self.assertEqual(self._names({"approval_code": ["is", "not set"]}), ["B", "C"])

    def test_a_null_never_satisfies_an_ordering_comparison(self):
        later = T0 + datetime.timedelta(hours=1)

        self.assertEqual(self._names({"bank_request_at": ["<", later]}), ["A"])
        self.assertEqual(self._names({"bank_request_at": [">", later]}), [])

    def test_a_datetime_column_compares_with_an_iso_string(self):
        self.assertEqual(self._names({"bank_request_at": ["<", "2026-09-20 11:00:00"]}), ["A"])
        self.assertEqual(self._names({"bank_request_at": [">", "2026-09-20 11:00:00"]}), [])

    def test_between_and_like(self):
        self.assertEqual(self._names({"amount": ["between", [15, 30]]}), ["B", "C"])
        self.assertEqual(self._names({"status": ["like", "%cess%"]}), ["A"])

    def test_filters_as_a_list_of_lists(self):
        self.assertEqual(self._names([["status", "=", "Failed"]]), ["B"])
        self.assertEqual(self._names([[DOCTYPE, "docstatus", "<", 2], ["amount", ">", 10]]), ["B"])

    def test_an_unknown_operator_fails_loudly(self):
        with self.assertRaises(NotImplementedError):
            self._names({"status": ["descendants of", "X"]})

    def test_get_all_coalesces_null_for_negations_like_db_query(self):
        # frappe.get_all builds ifnull(col, '') != value: a NULL row matches.
        self.assertEqual(self._names({"approval_code": ["!=", "abc"]}), ["B", "C"])
        self.assertEqual(self._names({"approval_code": ["not in", ["abc"]]}), ["B", "C"])

    def test_get_value_keeps_sql_null_semantics_for_negations(self):
        # frappe.db.get_value goes through the query builder: NULL != 'abc' is NULL, no match.
        self.assertEqual(self.db.get_value(DOCTYPE, {"approval_code": ["!=", "abc"]}), "C")
        self.assertEqual(self.db.get_value(DOCTYPE, {"approval_code": ["not in", ["abc", ""]]}), None)


class TestFakeDBSetValue(unittest.TestCase):
    def setUp(self):
        self.db = _db_with_order(modified=T0)

    def test_sets_one_field_or_a_dict_of_fields(self):
        self.db.set_value(DOCTYPE, "IPO-1", "status", "Processing")
        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["status"], "Processing")

        self.db.set_value(DOCTYPE, "IPO-1", {"status": "Failed", "invoice_lock": None})
        row = self.db.row(DOCTYPE, "IPO-1")
        self.assertEqual((row["status"], row["invoice_lock"]), ("Failed", None))
        self.assertEqual(self.db.events, [
            ("set_value", DOCTYPE, "IPO-1", {"status": "Processing"}),
            ("set_value", DOCTYPE, "IPO-1", {"status": "Failed", "invoice_lock": None}),
        ])

    def test_bumps_modified(self):
        self.db.set_value(DOCTYPE, "IPO-1", "status", "Processing")
        first = self.db.row(DOCTYPE, "IPO-1")["modified"]
        self.db.set_value(DOCTYPE, "IPO-1", "status", "Awaiting Bank")
        second = self.db.row(DOCTYPE, "IPO-1")["modified"]

        self.assertGreater(first, T0)
        self.assertGreater(second, first)

    def test_update_modified_false_freezes_modified_like_frappe(self):
        self.db.set_value(DOCTYPE, "IPO-1", "status", "Processing", update_modified=False)

        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["modified"], T0)

    def test_modified_comes_from_the_injected_clock(self):
        db = FakeDB(now=lambda: datetime.datetime(2030, 1, 1))
        db.add(DOCTYPE, "IPO-1", status="Approved", modified=T0)

        db.set_value(DOCTYPE, "IPO-1", "status", "Processing")

        self.assertEqual(db.row(DOCTYPE, "IPO-1")["modified"], datetime.datetime(2030, 1, 1))

    def test_a_frozen_clock_still_bumps_modified(self):
        # check_if_latest compares `modified`: a write must always be observable.
        db = FakeDB(now=lambda: T0)
        db.add(DOCTYPE, "IPO-1", status="Approved")

        db.set_value(DOCTYPE, "IPO-1", "status", "Processing")

        self.assertGreater(db.row(DOCTYPE, "IPO-1")["modified"], T0)

    def test_dict_filters_update_every_matching_row(self):
        self.db.add(DOCTYPE, "IPO-2", status="Approved", docstatus=1)
        self.db.add(DOCTYPE, "IPO-3", status="Draft", docstatus=0)

        self.db.set_value(DOCTYPE, {"status": "Approved"}, "status", "Cancelled")

        statuses = [self.db.row(DOCTYPE, n)["status"] for n in ("IPO-1", "IPO-2", "IPO-3")]
        self.assertEqual(statuses, ["Cancelled", "Cancelled", "Draft"])

    def test_a_missing_row_or_no_name_is_a_silent_no_op_like_frappe(self):
        self.db.set_value(DOCTYPE, "IPO-404", "status", "Failed")
        self.db.set_value(DOCTYPE, None, "status", "Failed")  # must never mean "every row"

        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["status"], "Approved")
        with self.assertRaises(KeyError):
            self.db.row(DOCTYPE, "IPO-404")

    def test_the_stored_value_does_not_alias_the_callers_object(self):
        values = {"status": "Processing"}
        self.db.set_value(DOCTYPE, "IPO-1", values)
        values["status"] = "Completed"

        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["status"], "Processing")
        self.assertEqual(self.db.events[-1][3], {"status": "Processing"})


class TestFakeDBTransactions(unittest.TestCase):
    def setUp(self):
        self.db = _db_with_order()

    def test_add_inserts_into_both_copies(self):
        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["status"], "Approved")
        self.assertEqual(self.db.committed_row(DOCTYPE, "IPO-1")["status"], "Approved")
        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["name"], "IPO-1")
        with self.assertRaises(ValueError):
            self.db.add(DOCTYPE, "IPO-1", status="Draft")  # a test set-up mistake, not an update

    def test_a_write_is_not_committed_until_commit(self):
        self.db.set_value(DOCTYPE, "IPO-1", "status", "Processing")

        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["status"], "Processing")
        self.assertEqual(self.db.committed_row(DOCTYPE, "IPO-1")["status"], "Approved")

        self.db.commit()

        self.assertEqual(self.db.committed_row(DOCTYPE, "IPO-1")["status"], "Processing")

    def test_rollback_restores_the_working_copy_from_the_committed_copy(self):
        self.db.set_value(DOCTYPE, "IPO-1", "status", "Processing")
        self.db.commit()
        self.db.set_value(DOCTYPE, "IPO-1", "status", "Failed")

        self.db.rollback()

        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["status"], "Processing")
        self.assertEqual(self.db.get_value(DOCTYPE, "IPO-1", "status"), "Processing")

    def test_a_later_write_does_not_leak_into_the_committed_copy(self):
        self.db.commit()
        self.db.row(DOCTYPE, "IPO-1")["status"] = "Failed"

        self.assertEqual(self.db.committed_row(DOCTYPE, "IPO-1")["status"], "Approved")

    def test_commit_and_rollback_are_events_in_order(self):
        self.db.set_value(DOCTYPE, "IPO-1", "status", "Processing")
        self.db.commit()
        self.db.rollback()

        self.assertEqual(
            self.db.events,
            [("set_value", DOCTYPE, "IPO-1", {"status": "Processing"}), ("commit",), ("rollback",)],
        )


class TestFakeDBUniqueColumns(unittest.TestCase):
    """`invoice_lock` is the database backstop of I6; the fake enforces it like MariaDB."""

    def setUp(self):
        self.db = FakeDB()
        self.db.add(DOCTYPE, "IPO-1", status="Approved", invoice_lock="PINV-1")
        self.db.add(DOCTYPE, "IPO-2", status="Draft", invoice_lock=None)
        self.db.add(DOCTYPE, "IPO-3", status="Failed", invoice_lock=None)

    def test_a_second_lock_on_the_same_invoice_is_refused(self):
        with self.assertRaises(UniqueViolationError):
            self.db.set_value(DOCTYPE, "IPO-2", "invoice_lock", "PINV-1")

        self.assertIsNone(self.db.row(DOCTYPE, "IPO-2")["invoice_lock"])

    def test_a_refused_write_changes_nothing_at_all(self):
        with self.assertRaises(UniqueViolationError):
            self.db.set_value(DOCTYPE, "IPO-2", {"status": "Approved", "invoice_lock": "PINV-1"})

        self.assertEqual(self.db.row(DOCTYPE, "IPO-2")["status"], "Draft")

    def test_an_empty_string_collides_so_the_lock_must_be_cleared_with_none(self):
        self.db.set_value(DOCTYPE, "IPO-2", "invoice_lock", "")

        with self.assertRaises(UniqueViolationError):
            self.db.set_value(DOCTYPE, "IPO-3", "invoice_lock", "")

    def test_rewriting_its_own_lock_is_allowed(self):
        self.db.set_value(DOCTYPE, "IPO-1", "invoice_lock", "PINV-1")

    def test_add_refuses_an_impossible_database_state(self):
        with self.assertRaises(UniqueViolationError):
            self.db.add(DOCTYPE, "IPO-4", status="Approved", invoice_lock="PINV-1")

    def test_null_never_collides_and_a_released_lock_is_free_again(self):
        self.db.set_value(DOCTYPE, "IPO-1", "invoice_lock", None)  # IPO-2 and IPO-3 are NULL too
        self.db.set_value(DOCTYPE, "IPO-2", "invoice_lock", "PINV-1")

        self.assertIsNone(self.db.row(DOCTYPE, "IPO-1")["invoice_lock"])
        self.assertEqual(self.db.row(DOCTYPE, "IPO-2")["invoice_lock"], "PINV-1")

    def test_other_unique_columns_can_be_declared(self):
        db = FakeDB(unique={"Payment Entry": ("reference_no",)})
        db.add("Payment Entry", "PE-1", reference_no="E2E-1")

        with self.assertRaises(UniqueViolationError):
            db.add("Payment Entry", "PE-2", reference_no="E2E-1")


class TestFakeDBQueries(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(singles={"Banco Inter Settings": {"enabled": 1}})
        self.db.add(DOCTYPE, "IPO-1", status="Awaiting Bank", docstatus=1, amount=30.0)
        self.db.add(DOCTYPE, "IPO-2", status="Awaiting Bank", docstatus=1, amount=10.0)
        self.db.add(DOCTYPE, "IPO-3", status="Failed", docstatus=1, amount=20.0)

    def test_get_all_returns_the_requested_fields_and_defaults_to_the_name(self):
        rows = self.db.get_all(DOCTYPE, filters={"status": "Failed"})
        self.assertEqual(rows, [{"name": "IPO-3"}])
        self.assertEqual(rows[0].name, "IPO-3")

        rows = self.db.get_all(DOCTYPE, filters={"status": "Failed"}, fields=["name", "amount"])
        self.assertEqual(rows, [{"name": "IPO-3", "amount": 20.0}])

    def test_get_all_star_returns_the_whole_row(self):
        rows = self.db.get_all(DOCTYPE, filters={"name": "IPO-3"}, fields=["*"])

        self.assertEqual(rows[0].status, "Failed")
        self.assertEqual(rows[0].docstatus, 1)

    def test_get_all_pluck_is_a_flat_list(self):
        names = self.db.get_all(DOCTYPE, filters={"status": "Awaiting Bank"}, pluck="name")

        self.assertEqual(names, ["IPO-1", "IPO-2"])

    def test_get_all_without_filters_returns_everything(self):
        self.assertEqual(len(self.db.get_all(DOCTYPE)), 3)
        self.assertEqual(self.db.get_all("Unknown DocType"), [])

    def test_get_all_orders_and_limits(self):
        self.assertEqual(self.db.get_all(DOCTYPE, pluck="name", order_by="amount asc"), ["IPO-2", "IPO-3", "IPO-1"])
        self.assertEqual(self.db.get_all(DOCTYPE, pluck="name", order_by="amount desc", limit=2), ["IPO-1", "IPO-3"])
        self.assertEqual(self.db.get_all(DOCTYPE, pluck="name", limit_page_length=1), ["IPO-1"])

    def test_get_all_refuses_what_it_cannot_mirror(self):
        with self.assertRaises(NotImplementedError):
            self.db.get_all(DOCTYPE, or_filters={"status": "Failed"})
        with self.assertRaises(NotImplementedError):
            self.db.get_all(DOCTYPE, fields=["sum(amount) as total"])

    def test_get_all_rows_are_copies(self):
        self.db.get_all(DOCTYPE, fields=["name", "status"])[0]["status"] = "Completed"

        self.assertEqual(self.db.row(DOCTYPE, "IPO-1")["status"], "Awaiting Bank")

    def test_exists_returns_the_name_or_none(self):
        self.assertEqual(self.db.exists(DOCTYPE, "IPO-1"), "IPO-1")
        self.assertEqual(self.db.exists(DOCTYPE, {"status": "Failed"}), "IPO-3")
        self.assertEqual(self.db.exists({"doctype": DOCTYPE, "status": "Failed"}), "IPO-3")
        self.assertIsNone(self.db.exists(DOCTYPE, "IPO-404"))
        self.assertIsNone(self.db.exists(DOCTYPE, {"status": "Completed"}))
        self.assertIsNone(self.db.exists(DOCTYPE))

    def test_get_single_value_reads_the_singles(self):
        self.assertEqual(self.db.get_single_value("Banco Inter Settings", "enabled"), 1)
        self.assertIsNone(self.db.get_single_value("Banco Inter Settings", "missing"))
        self.assertIsNone(self.db.get_single_value("I8 Agent Settings", "telegram_chat_id"))

    def test_sql_returns_the_canned_result_and_records_the_call(self):
        self.assertEqual(self.db.sql("select 1"), [])

        self.db.sql_result = [("IPO-1",)]

        self.assertEqual(self.db.sql("select name from tab where x = %s", ("y",), as_dict=False), [("IPO-1",)])
        self.assertEqual(self.db.sql_calls[-1], (("select name from tab where x = %s", ("y",)), {"as_dict": False}))

    def test_an_unknown_database_method_is_an_error_not_a_mock(self):
        with self.assertRaises(AttributeError):
            self.db.get_list(DOCTYPE)


def _run_inner(body) -> unittest.TestResult:
    """Run a throw-away test case by hand, to watch what it leaves on the shared frappe mock."""

    class InnerCase(unittest.TestCase):
        def runTest(self):
            body(self)

    return InnerCase().run()


class TestPatchFrappe(unittest.TestCase):
    """`patch.object(frappe, "db", fake)` is a trap on the shared MagicMock: when it stops, mock
    deletes the child and puts it back *outside* `_mock_children`, and from then on
    `frappe.reset_mock()` no longer reaches `frappe.db` - call counts leak into every later
    test module. `patch_frappe` shadows the attribute instead and leaves the child alone."""

    def setUp(self):
        self.frappe = install_frappe_mock()
        # reset_mock() does not clear side_effect (CLAUDE.md): another test module may have left
        # an exhausted iterator on the children these tests call directly.
        self.frappe.db.get_value.side_effect = None
        self.frappe.get_all.side_effect = None

    def test_serves_db_and_get_all_from_the_fake_during_the_test(self):
        db = _db_with_order()
        seen = {}

        def body(case):
            self.assertIs(patch_frappe_db(case, db), db)
            seen["db"] = self.frappe.db
            seen["names"] = self.frappe.get_all(DOCTYPE, pluck="name")

        self.assertTrue(_run_inner(body).wasSuccessful())
        self.assertIs(seen["db"], db)
        self.assertEqual(seen["names"], ["IPO-1"])

    def test_gives_the_mock_children_back_when_the_test_ends(self):
        original_db, original_get_all = self.frappe.db, self.frappe.get_all

        self.assertTrue(_run_inner(lambda case: patch_frappe_db(case, FakeDB())).wasSuccessful())

        self.assertIs(self.frappe.db, original_db)
        self.assertIs(self.frappe.get_all, original_get_all)
        self.assertNotIn("db", self.frappe.__dict__)
        self.assertNotIn("get_all", self.frappe.__dict__)

    def test_reset_mock_still_reaches_frappe_db_afterwards(self):
        self.assertTrue(_run_inner(lambda case: patch_frappe_db(case, FakeDB())).wasSuccessful())

        self.frappe.db.get_value("Inter Payment Order", "IPO-1", "status")
        self.frappe.get_all("Inter Payment Order")
        self.frappe.reset_mock()

        self.assertEqual(self.frappe.db.get_value.call_count, 0)
        self.assertEqual(self.frappe.get_all.call_count, 0)

    def test_any_attribute_can_be_shadowed(self):
        loaded = []

        def body(case):
            patch_frappe(case, get_doc=lambda doctype, name: loaded.append((doctype, name)) or "doc")
            self.assertEqual(self.frappe.get_doc(DOCTYPE, "IPO-1"), "doc")

        self.assertTrue(_run_inner(body).wasSuccessful())
        self.assertEqual(loaded, [(DOCTYPE, "IPO-1")])
        self.assertNotIn("get_doc", self.frappe.__dict__)

    def test_nested_shadows_unwind_in_order(self):
        first, second = FakeDB(), FakeDB()
        seen = []

        def body(case):
            patch_frappe_db(case, first)
            patch_frappe_db(case, second)
            seen.append(self.frappe.db)

        self.assertTrue(_run_inner(body).wasSuccessful())
        self.assertIs(seen[0], second)
        self.assertNotIn("db", self.frappe.__dict__)


class TestFakeInterClientSendPix(unittest.TestCase):
    """I2: the claim is committed before the HTTP request is issued."""

    def test_refuses_to_send_for_an_order_that_was_never_claimed(self):
        db = _db_with_order()
        client = FakeInterClient(db, doctype_row=(DOCTYPE, "IPO-1"))

        with self.assertRaises(AssertionError) as ctx:
            client.send_pix({"valor": 16800.0}, KEY)

        self.assertIn("Processing", str(ctx.exception))

    def test_refuses_to_send_when_the_claim_was_written_but_not_committed(self):
        db = _db_with_order()
        _claim(db, commit=False)
        client = FakeInterClient(db, doctype_row=(DOCTYPE, "IPO-1"))

        with self.assertRaises(AssertionError):
            client.send_pix({"valor": 16800.0}, KEY)

    def test_refuses_to_send_when_the_committed_claim_is_incomplete(self):
        claim = {"status": "Processing", "idempotency_key": KEY, "bank_request_at": T0}
        for missing in ("idempotency_key", "bank_request_at"):
            with self.subTest(missing=missing):
                db = _db_with_order()
                db.set_value(DOCTYPE, "IPO-1", {key: value for key, value in claim.items() if key != missing})
                db.commit()

                with self.assertRaises(AssertionError) as ctx:
                    FakeInterClient(db, doctype_row=(DOCTYPE, "IPO-1")).send_pix({"valor": 1.0}, KEY)

                self.assertIn(missing, str(ctx.exception))

    def test_refuses_a_key_that_is_not_the_committed_one(self):
        with self.assertRaises(AssertionError):
            _claimed_client()[1].send_pix({"valor": 1.0}, OTHER_KEY)

    def test_refuses_to_send_when_it_was_not_told_which_order_it_serves(self):
        db, _client = _claimed_client()

        with self.assertRaises(AssertionError):
            FakeInterClient(db).send_pix({"valor": 1.0}, KEY)

    def test_an_i2_violation_is_kept_even_if_the_caller_swallows_the_assertion(self):
        db = _db_with_order()
        client = FakeInterClient(db, doctype_row=(DOCTYPE, "IPO-1"))

        try:
            client.send_pix({"valor": 1.0}, KEY)
        except AssertionError:
            pass

        self.assertEqual(len(client.i2_violations), 1)
        self.assertEqual(client.calls, [("send_pix", {"valor": 1.0}, KEY)])

    def test_rejects_a_key_the_real_client_would_reject(self):
        db, client = _claimed_client()

        for bad in ("", KEY.upper(), "not-a-uuid"):
            with self.assertRaises(ValueError):
                client.send_pix({"valor": 1.0}, bad)

        self.assertEqual(client.calls, [])
        self.assertNotIn("bank", [event[0] for event in db.events])

    def test_a_committed_claim_lets_the_send_through_and_records_it(self):
        db, client = _claimed_client()
        client.responses["send_pix"] = {"tipoRetorno": "APROVACAO", "codigoSolicitacao": "cs-1"}

        response = client.send_pix({"valor": 16800.0}, KEY)

        self.assertEqual(response, {"tipoRetorno": "APROVACAO", "codigoSolicitacao": "cs-1"})
        self.assertEqual(client.calls, [("send_pix", {"valor": 16800.0}, KEY)])
        self.assertEqual(client.i2_violations, [])
        self.assertEqual(db.events[-1], ("bank", "send_pix", {"valor": 16800.0}))
        self.assertLess(db.events.index(("commit",)), db.events.index(("bank", "send_pix", {"valor": 16800.0})))

    def test_has_a_default_response_shaped_like_the_bank(self):
        response = _claimed_client()[1].send_pix({"valor": 1.0}, KEY)

        self.assertEqual(response["tipoRetorno"], "APROVACAO")
        self.assertTrue(response["codigoSolicitacao"])

    def test_an_exception_response_is_raised_after_the_send_was_recorded(self):
        db, client = _claimed_client()
        client.responses["send_pix"] = RuntimeError("read timeout")

        with self.assertRaises(RuntimeError):
            client.send_pix({"valor": 1.0}, KEY)

        self.assertEqual(db.events[-1], ("bank", "send_pix", {"valor": 1.0}))

    def test_the_recorded_payload_is_a_snapshot(self):
        db, client = _claimed_client()
        payload = {"valor": 1.0}

        client.send_pix(payload, KEY)
        payload["valor"] = 2.0

        self.assertEqual(db.events[-1], ("bank", "send_pix", {"valor": 1.0}))


class TestFakeInterClientOtherCalls(unittest.TestCase):
    def test_pay_barcode_checks_i2_too(self):
        db = _db_with_order(payment_type="Boleto Payment")
        client = FakeInterClient(db, doctype_row=(DOCTYPE, "IPO-1"))

        with self.assertRaises(AssertionError):
            client.pay_barcode({"codBarraLinhaDigitavel": "123"})

        _claim(db)
        client.responses["pay_barcode"] = {"codigoTransacao": "ct-1", "statusPagamento": "REALIZADO"}

        self.assertEqual(client.pay_barcode({"codBarraLinhaDigitavel": "123"})["codigoTransacao"], "ct-1")
        self.assertEqual(db.events[-1], ("bank", "pay_barcode", {"codBarraLinhaDigitavel": "123"}))

    def test_reads_are_recorded_and_need_no_claim(self):
        db = _db_with_order()
        client = FakeInterClient(db)
        client.responses["get_pix_payment"] = {"transacaoPix": {"status": "PAGO", "endToEnd": "E123"}}
        client.responses["find_barcode_payments"] = [{"codigoTransacao": "ct-1", "statusPagamento": "REALIZADO"}]

        self.assertEqual(client.get_pix_payment("cs-1")["transacaoPix"]["status"], "PAGO")
        self.assertEqual(client.find_barcode_payments(codigo_transacao="ct-1")[0]["statusPagamento"], "REALIZADO")
        self.assertEqual(
            db.events,
            [("bank", "get_pix_payment", "cs-1"), ("bank", "find_barcode_payments", {"codigo_transacao": "ct-1"})],
        )
        self.assertEqual(
            client.calls,
            [("get_pix_payment", "cs-1"), ("find_barcode_payments", {"codigo_transacao": "ct-1"})],
        )

    def test_read_defaults_are_still_in_flight_and_empty(self):
        client = FakeInterClient(FakeDB())

        self.assertEqual(client.get_pix_payment("cs-1")["transacaoPix"]["status"], "AGUARDANDO_APROVACAO")
        self.assertEqual(client.find_barcode_payments(codigo_transacao="ct-1"), [])

    def test_a_read_can_fail_and_a_callable_response_sees_the_arguments(self):
        client = FakeInterClient(FakeDB())
        client.responses["get_pix_payment"] = TimeoutError("poll timeout")
        with self.assertRaises(TimeoutError):
            client.get_pix_payment("cs-1")

        client.responses["get_pix_payment"] = lambda codigo: {"transacaoPix": {"status": "PAGO", "codigo": codigo}}
        self.assertEqual(client.get_pix_payment("cs-9")["transacaoPix"]["codigo"], "cs-9")

    def test_has_no_send_ted(self):
        self.assertFalse(hasattr(FakeInterClient(FakeDB()), "send_ted"))


_INTER_CLIENT_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "banking", "inter_client.py"
)


def _real_client_source() -> ast.Module:
    """The real client, parsed and not imported: importing it here would put ``requests`` and
    the client module into ``sys.modules`` under the feet of the other test modules."""
    with open(_INTER_CLIENT_PY) as fh:
        return ast.parse(fh.read())


def _real_client_parameters(method: str) -> tuple[list[str], list[str]]:
    """``(positional, keyword-only)`` parameter names of ``InterAPIClient.<method>``."""
    client = next(
        node for node in _real_client_source().body
        if isinstance(node, ast.ClassDef) and node.name == "InterAPIClient"
    )
    function = next(node for node in client.body if isinstance(node, ast.FunctionDef) and node.name == method)
    return [arg.arg for arg in function.args.args], [arg.arg for arg in function.args.kwonlyargs]


def _real_client_constant(name: str):
    for node in _real_client_source().body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", None) == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"inter_client.py no longer defines {name}")


class TestFakeInterClientMirrorsTheRealClient(unittest.TestCase):
    """payment_service is developed against the fake alone: a call the fake accepts and
    ``InterAPIClient`` refuses would first fail in production, inside a poll whose errors
    are (rightly) swallowed."""

    def test_the_positional_methods_take_the_same_parameters(self):
        for method in ("send_pix", "pay_barcode", "get_pix_payment"):
            with self.subTest(method=method):
                positional, keyword_only = _real_client_parameters(method)
                self.assertEqual(list(inspect.signature(getattr(FakeInterClient, method)).parameters), positional)
                self.assertEqual(keyword_only, [])

    def test_find_barcode_payments_knows_exactly_the_real_keywords(self):
        positional, keyword_only = _real_client_parameters("find_barcode_payments")

        self.assertEqual(positional, ["self"])
        self.assertEqual(sorted(FakeInterClient.FIND_BARCODE_PAYMENTS_KEYWORDS), sorted(keyword_only))
        self.assertEqual(FakeInterClient.PAYMENT_DATE_FILTERS, _real_client_constant("PAYMENT_DATE_FILTERS"))

    def test_find_barcode_payments_refuses_what_the_real_client_refuses(self):
        db = FakeDB()
        client = FakeInterClient(db)
        refused = (
            (TypeError, {"codigoTransacao": "ct-1"}),
            (TypeError, {"codigo_transacao": "ct-1", "date_filter": "INCLUSAO"}),
            (ValueError, {"codigo_transacao": "ct-1", "start_date": "2026-09-19"}),
            (ValueError, {"codigo_transacao": "ct-1", "end_date": "2026-09-21"}),
            (ValueError, {"codigo_transacao": "ct-1", "filter_date_by": "inclusao"}),
        )
        for error, kwargs in refused:
            with self.subTest(kwargs=kwargs), self.assertRaises(error):
                client.find_barcode_payments(**kwargs)
        with self.assertRaises(TypeError):
            client.find_barcode_payments("ct-1")  # keyword-only, like the real one

        self.assertEqual(client.calls, [])
        self.assertEqual(db.events, [])

    def test_find_barcode_payments_accepts_the_poll_of_the_spec(self):
        client = FakeInterClient(FakeDB())
        poll = {
            "codigo_transacao": "ct-1", "filter_date_by": "INCLUSAO",
            "start_date": datetime.date(2026, 9, 19), "end_date": datetime.date(2026, 9, 21),
        }

        self.assertEqual(client.find_barcode_payments(**poll), [])
        self.assertEqual(client.calls, [("find_barcode_payments", poll)])


class TestFakeSubmittedDoc(unittest.TestCase):
    """The production defect: `order.save()` on a submitted order raises after the bank accepted."""

    def test_reads_its_fields_from_the_database_row(self):
        doc = FakeSubmittedDoc(_db_with_order(), "IPO-1")

        self.assertEqual((doc.doctype, doc.name, doc.status, doc.docstatus), (DOCTYPE, "IPO-1", "Approved", 1))
        self.assertEqual(doc.get("amount"), 16800.0)
        self.assertEqual(doc.get("missing", "fallback"), "fallback")

    def test_an_unset_field_is_none_but_a_name_that_is_not_a_field_is_an_error(self):
        self.assertIsNone(FakeSubmittedDoc(_db_with_order(), "IPO-1").transaction_id)
        with self.assertRaises(AttributeError):
            FakeSubmittedDoc(_db_with_order(), "IPO-1").statuss

    def test_a_missing_document_cannot_be_loaded(self):
        with self.assertRaises(KeyError):
            FakeSubmittedDoc(FakeDB(), "IPO-404")

    def test_save_raises_for_a_changed_status_on_a_submitted_order(self):
        db = _db_with_order(status="Processing")
        doc = FakeSubmittedDoc(db, "IPO-1")
        doc.status = "Completed"

        with self.assertRaises(UpdateAfterSubmitError) as ctx:
            doc.save(ignore_permissions=True)

        self.assertIn("status", str(ctx.exception))
        self.assertEqual(db.row(DOCTYPE, "IPO-1")["status"], "Processing")

    def test_save_raises_for_a_result_field_filled_after_submit(self):
        doc = FakeSubmittedDoc(_db_with_order(status="Processing"), "IPO-1")
        doc.transaction_id = "E2E-1"

        with self.assertRaises(UpdateAfterSubmitError):
            doc.save()

    def test_save_passes_on_a_draft_and_writes_the_row(self):
        db = _db_with_order(status="Draft", docstatus=0, modified=T0)
        doc = FakeSubmittedDoc(db, "IPO-1")
        doc.status = "Approved"

        doc.save()

        self.assertEqual(db.row(DOCTYPE, "IPO-1")["status"], "Approved")
        self.assertGreater(db.row(DOCTYPE, "IPO-1")["modified"], T0)
        self.assertIn(("save", DOCTYPE, "IPO-1"), db.events)

    def test_save_passes_on_a_submitted_order_when_nothing_changed(self):
        FakeSubmittedDoc(_db_with_order(), "IPO-1").save()

    def test_save_compares_with_the_database_not_with_what_was_loaded(self):
        db = _db_with_order(status="Processing")
        doc = FakeSubmittedDoc(db, "IPO-1")
        db.set_value(DOCTYPE, "IPO-1", "status", "Awaiting Bank")

        with self.assertRaises(UpdateAfterSubmitError):
            doc.save()

    def test_the_real_doctype_does_not_allow_status_or_result_fields_on_submit(self):
        # The premise of the fake. If someone adds allow_on_submit to these fields the
        # regression tests built on FakeSubmittedDoc stop proving anything.
        doc = FakeSubmittedDoc(_db_with_order(), "IPO-1")

        for fieldname in ("status", "transaction_id", "approval_code", "execution_date", "inter_response"):
            self.assertIn(fieldname, doc.meta_fields)
            self.assertNotIn(fieldname, doc.allow_on_submit)

    def test_allow_on_submit_is_read_from_the_doctype_json(self):
        schema = {"fields": [
            {"fieldname": "status", "fieldtype": "Select"},
            {"fieldname": "remarks", "fieldtype": "Data", "allow_on_submit": 1},
        ]}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "doctype.json")
            with open(path, "w") as fh:
                json.dump(schema, fh)
            db = _db_with_order(remarks="old")
            doc = FakeSubmittedDoc(db, "IPO-1", json_path=path)
            doc.remarks = "new"

            doc.save()

            self.assertEqual(db.row(DOCTYPE, "IPO-1")["remarks"], "new")
            doc.status = "Completed"
            with self.assertRaises(UpdateAfterSubmitError):
                doc.save()

    def test_db_set_goes_through_set_value(self):
        db = _db_with_order()
        doc = FakeSubmittedDoc(db, "IPO-1")

        doc.db_set("status", "Processing")

        self.assertEqual(doc.status, "Processing")
        self.assertEqual(db.row(DOCTYPE, "IPO-1")["status"], "Processing")
        self.assertIn(("set_value", DOCTYPE, "IPO-1", {"status": "Processing"}), db.events)

    def test_reload_reads_the_row_again(self):
        db = _db_with_order()
        doc = FakeSubmittedDoc(db, "IPO-1")
        db.set_value(DOCTYPE, "IPO-1", "status", "Processing")

        self.assertEqual(doc.status, "Approved")
        doc.reload()

        self.assertEqual(doc.status, "Processing")

    def test_comments_and_realtime_updates_are_recorded(self):
        doc = FakeSubmittedDoc(_db_with_order(), "IPO-1")

        doc.add_comment("Comment", "Sent to the bank")
        doc.notify_update()

        self.assertEqual(doc.comments, [("Comment", "Sent to the bank")])
        self.assertEqual(doc.notified, 1)

    def test_another_doctype_has_no_schema_and_every_field_is_protected(self):
        db = FakeDB()
        db.add("Purchase Invoice", "PINV-1", docstatus=1, outstanding_amount=100.0)
        doc = FakeSubmittedDoc(db, "PINV-1", doctype="Purchase Invoice")
        doc.outstanding_amount = 0.0

        self.assertIsNone(doc.supplier)
        with self.assertRaises(UpdateAfterSubmitError):
            doc.save()
