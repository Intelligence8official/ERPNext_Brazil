"""Tests for the migration patch of the payment-safety fix (spec 4.7).

The patch meets the production data of the defect: a submitted order frozen in ``Processing``
that the old hourly cron re-sent to the bank. It must leave every such order in
``Needs Verification``, give every blocking order its ``invoice_lock``, and tell the operator -
on the order's timeline only - which bank ids the log holds for it.
"""

import ast
import configparser
import datetime
import inspect
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from brazil_module.tests._payment_fakes import (
    DOCTYPE,
    FakeDB,
    FakeSubmittedDoc,
    UniqueViolationError,
    install_frappe_mock,
    patch_frappe,
    patch_frappe_db,
)

frappe = install_frappe_mock()

import brazil_module.patches.v1_1.flag_stuck_payment_orders as patch_mod

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCHES_TXT = os.path.join(PACKAGE_ROOT, "patches.txt")
PATCH_NAME = "brazil_module.patches.v1_1.flag_stuck_payment_orders"

LOG = "Inter API Log"
INVOICE = "ACC-PINV-2026-00031"
OTHER_INVOICE = "ACC-PINV-2026-00040"
ALL_STATUSES = (
    "Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
    "Needs Verification", "Completed", "Failed", "Cancelled",
)
T0 = datetime.datetime(2026, 3, 30, 14, 0, 0)


def at(minutes: int) -> datetime.datetime:
    return T0 + datetime.timedelta(minutes=minutes)


class PatchCase(unittest.TestCase):
    def setUp(self):
        self.db = patch_frappe_db(self, FakeDB())
        self.docs: dict[str, FakeSubmittedDoc] = {}
        self.log_error = MagicMock()
        patch_frappe(self, get_doc=self.get_doc, log_error=self.log_error)
        patcher = patch.object(patch_mod, "_", lambda text: text)  # bound at import time
        patcher.start()
        self.addCleanup(patcher.stop)

    def get_doc(self, doctype, name):
        self.assertEqual(doctype, DOCTYPE)
        if name not in self.docs:
            self.docs[name] = FakeSubmittedDoc(self.db, name, doctype)
        return self.docs[name]

    def add_order(self, name, status, *, docstatus=1, invoice=INVOICE, created=0, **fields):
        fields.setdefault("payment_type", "PIX")
        self.db.add(
            DOCTYPE, name, status=status, docstatus=docstatus, purchase_invoice=invoice,
            creation=at(created), **fields,
        )

    def add_log(self, name, request, response, created=0):
        self.db.add(
            LOG, name, creation=at(created), timestamp=at(created), method="POST",
            request_body=json.dumps(request) if isinstance(request, dict) else request,
            response_body=json.dumps(response) if isinstance(response, dict) else response,
        )

    def status(self, name):
        return self.db.committed_row(DOCTYPE, name)["status"]

    def lock(self, name):
        return self.db.committed_row(DOCTYPE, name).get("invoice_lock")

    def comments(self, name) -> str:
        return "\n".join(text for _kind, text in self.docs[name].comments) if name in self.docs else ""

    def errors(self) -> str:
        return "\n".join(f"{call.args} {call.kwargs}" for call in self.log_error.call_args_list)


class TestFlagging(PatchCase):
    def test_submitted_processing_order_becomes_needs_verification_and_is_committed(self):
        self.add_order("IPO-2026-00001", "Processing")
        patch_mod.execute()
        self.assertEqual(self.status("IPO-2026-00001"), "Needs Verification")

    def test_every_stuck_order_is_flagged_not_only_the_first(self):
        self.add_order("IPO-1", "Processing", invoice=INVOICE)
        self.add_order("IPO-2", "Processing", invoice=OTHER_INVOICE)
        patch_mod.execute()
        self.assertEqual([self.status("IPO-1"), self.status("IPO-2")], ["Needs Verification"] * 2)

    def test_the_flag_is_a_compare_and_set_under_a_row_lock(self):
        self.add_order("IPO-1", "Processing")
        patch_mod.execute()
        events = self.db.events
        write = next(i for i, e in enumerate(events) if e[0] == "set_value" and e[3].get("status"))
        locked_reads = [e for e in events[:write] if e[:3] == ("get_value", DOCTYPE, "IPO-1") and e[3]]
        self.assertTrue(locked_reads, "the status was written without re-reading the row with for_update=True")

    def test_an_order_that_left_processing_meanwhile_is_not_overwritten(self):
        self.add_order("IPO-1", "Processing")
        real_get_value = self.db.get_value

        def answered_meanwhile(doctype, filters=None, *args, **kwargs):
            if doctype == DOCTYPE and filters == "IPO-1" and kwargs.get("for_update"):
                for copy_of_row in (self.db.row(DOCTYPE, "IPO-1"), self.db.committed_row(DOCTYPE, "IPO-1")):
                    copy_of_row["status"] = "Awaiting Bank"  # another worker committed the bank's answer
            return real_get_value(doctype, filters, *args, **kwargs)

        self.db.get_value = answered_meanwhile
        patch_mod.execute()
        self.assertEqual(self.status("IPO-1"), "Awaiting Bank")
        self.assertEqual(self.comments("IPO-1"), "")

    def test_the_write_bumps_modified(self):
        self.add_order("IPO-1", "Processing")
        before = self.db.committed_row(DOCTYPE, "IPO-1")["modified"]
        patch_mod.execute()
        self.assertGreater(self.db.committed_row(DOCTYPE, "IPO-1")["modified"], before)

    def test_every_other_status_is_left_alone(self):
        others = [status for status in ALL_STATUSES if status != "Processing"]
        for index, status in enumerate(others):
            self.add_order(f"IPO-{index}", status, invoice=f"PINV-{index}")
        patch_mod.execute()
        self.assertEqual([self.status(f"IPO-{index}") for index in range(len(others))], others)

    def test_draft_and_cancelled_documents_in_processing_are_left_alone(self):
        self.add_order("IPO-DRAFT", "Processing", docstatus=0, invoice="PINV-A")
        self.add_order("IPO-CANCELLED", "Processing", docstatus=2, invoice="PINV-B")
        patch_mod.execute()
        self.assertEqual([self.status("IPO-DRAFT"), self.status("IPO-CANCELLED")], ["Processing", "Processing"])


class TestInvoiceLock(PatchCase):
    def test_every_blocking_order_gets_its_invoice_lock(self):
        blocking = ("Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
                    "Needs Verification", "Completed")
        for index, status in enumerate(blocking):
            self.add_order(f"IPO-{index}", status, invoice=f"PINV-{index}", docstatus=0 if status == "Draft" else 1)
        patch_mod.execute()
        self.assertEqual(
            [self.lock(f"IPO-{index}") for index in range(len(blocking))],
            [f"PINV-{index}" for index in range(len(blocking))],
        )

    def test_orders_that_do_not_block_keep_a_null_lock(self):
        self.add_order("IPO-FAILED", "Failed", invoice="PINV-A")
        self.add_order("IPO-CANCELLED", "Cancelled", invoice="PINV-B", docstatus=2)
        self.add_order("IPO-STATUS-CANCELLED", "Cancelled", invoice="PINV-C")
        self.add_order("IPO-SETTLED", "Completed", invoice="PINV-D", payment_entry="ACC-PAY-2026-00009")
        self.add_order("IPO-DOC-CANCELLED", "Approved", invoice="PINV-E", docstatus=2)
        self.add_order("IPO-NO-INVOICE", "Approved", invoice=None)
        patch_mod.execute()
        for name in ("IPO-FAILED", "IPO-CANCELLED", "IPO-STATUS-CANCELLED", "IPO-SETTLED",
                     "IPO-DOC-CANCELLED", "IPO-NO-INVOICE"):
            self.assertIsNone(self.lock(name), name)

    def test_first_blocking_order_of_an_invoice_wins_and_the_second_is_reported(self):
        self.add_order("IPO-NEWER", "Approved", created=60)  # inserted first: creation decides, not row order
        self.add_order("IPO-OLDER", "Processing", created=0)
        patch_mod.execute()  # must not raise
        self.assertEqual(self.lock("IPO-OLDER"), INVOICE)
        self.assertIsNone(self.lock("IPO-NEWER"))
        self.assertEqual(self.log_error.call_count, 1)
        for word in ("IPO-NEWER", "IPO-OLDER", INVOICE):
            self.assertIn(word, self.errors())

    def test_a_failed_order_does_not_take_the_place_of_the_blocking_one(self):
        self.add_order("IPO-FAILED", "Failed", created=0)
        self.add_order("IPO-LIVE", "Awaiting Bank", created=60)
        patch_mod.execute()
        self.assertIsNone(self.lock("IPO-FAILED"))
        self.assertEqual(self.lock("IPO-LIVE"), INVOICE)
        self.log_error.assert_not_called()

    def test_an_order_that_already_holds_the_lock_keeps_it(self):
        self.add_order("IPO-OLDER", "Completed", created=0)  # blocks again: its Payment Entry was cancelled
        self.add_order("IPO-HOLDER", "Approved", created=60, invoice_lock=INVOICE)
        patch_mod.execute()  # must not raise
        self.assertEqual(self.lock("IPO-HOLDER"), INVOICE)
        self.assertIsNone(self.lock("IPO-OLDER"))
        self.assertEqual([event for event in self.db.events if event[0] == "set_value"], [], "decided before writing")
        self.assertEqual(self.log_error.call_count, 1)
        self.assertIn("IPO-HOLDER keeps the invoice lock", self.errors())
        self.assertIn("cancel or resolve IPO-OLDER", self.errors())

    def test_a_unique_key_violation_is_reported_and_the_other_invoices_still_get_their_lock(self):
        self.add_order("IPO-STALE", "Failed", created=0, invoice_lock=INVOICE)  # should not exist; the DB says no
        self.add_order("IPO-LIVE", "Approved", created=10)
        self.add_order("IPO-OTHER", "Approved", created=20, invoice=OTHER_INVOICE)
        with self.assertRaises(UniqueViolationError):  # the fake really refuses it
            self.db.set_value(DOCTYPE, "IPO-LIVE", "invoice_lock", INVOICE)
        patch_mod.execute()  # must not raise
        self.assertIsNone(self.lock("IPO-LIVE"))
        self.assertEqual(self.lock("IPO-OTHER"), OTHER_INVOICE)
        self.assertIn("IPO-LIVE", self.errors())


class TestTimelineComment(PatchCase):
    def test_comment_lists_the_bank_ids_logged_for_the_invoice(self):
        self.add_order("IPO-2026-00001", "Processing")
        pix = {"valor": "16800.00", "descricao": f"Payment {INVOICE}", "destinatario": {"tipo": "CHAVE"}}
        self.add_log("LOG-1", pix, {"tipoRetorno": "APROVACAO", "codigoSolicitacao": "aaaa-1111"}, created=0)
        self.add_log("LOG-2", pix, {"tipoRetorno": "APROVACAO", "codigoSolicitacao": "bbbb-2222"}, created=60)
        self.add_log("LOG-3", pix, {"title": "Limite excedido", "violacoes": [{"codigo": "PIXP30"}]}, created=120)
        self.add_log("LOG-4", pix, {"tipoRetorno": "APROVACAO", "codigoSolicitacao": "aaaa-1111"}, created=180)
        self.add_log("LOG-5", {**pix, "descricao": f"Payment {OTHER_INVOICE}"}, {"codigoSolicitacao": "zzzz-9999"})
        self.add_log("LOG-6", {**pix, "descricao": f"Payment {INVOICE}-1"}, {"codigoSolicitacao": "yyyy-8888"})
        patch_mod.execute()
        comment = self.comments("IPO-2026-00001")
        self.assertIn("aaaa-1111", comment)
        self.assertIn("bbbb-2222", comment)
        self.assertEqual(comment.count("aaaa-1111"), 1, "the same bank id must be listed once")
        self.assertNotIn("zzzz-9999", comment, "an id of another invoice")
        self.assertNotIn("yyyy-8888", comment, "an id of the amended invoice, whose name only starts the same")
        self.assertLess(comment.index("aaaa-1111"), comment.index("bbbb-2222"), "oldest first")

    def test_comment_tells_the_operator_where_to_look(self):
        self.add_order("IPO-1", "Processing")
        patch_mod.execute()
        comment = self.comments("IPO-1").lower()
        for words in ("bank statement", "approval queue", "cannot be cancelled", "expire"):
            self.assertIn(words, comment)

    def test_no_id_in_the_log_is_not_presented_as_nothing_sent(self):
        self.add_order("IPO-1", "Processing")
        patch_mod.execute()
        comment = self.comments("IPO-1").lower()
        self.assertIn("no bank id", comment)
        self.assertIn("does not mean", comment)
        self.assertIn("bank statement", comment)

    def test_a_truncated_response_body_still_gives_its_id(self):
        self.add_order("IPO-1", "Processing")
        self.add_log("LOG-1", {"descricao": f"Payment {INVOICE}"}, '{"codigoSolicitacao": "cccc-3333", "x": "trunc')
        patch_mod.execute()
        self.assertIn("cccc-3333", self.comments("IPO-1"))

    def test_an_order_without_invoice_is_matched_by_its_own_name(self):
        self.add_order("IPO-2026-00007", "Processing", invoice=None)
        self.add_log("LOG-1", {"descricao": "Payment IPO-2026-00007"}, {"codigoSolicitacao": "dddd-4444"})
        patch_mod.execute()
        self.assertIn("dddd-4444", self.comments("IPO-2026-00007"))

    def test_a_boleto_is_matched_by_its_barcode_and_lists_codigo_transacao(self):
        barcode = "03399000000000168009" + "7" * 24
        self.add_order("IPO-1", "Processing", payment_type="Boleto Payment", barcode=f"{barcode[:5]}.{barcode[5:]}")
        self.add_log("LOG-1", {"codBarraLinhaDigitavel": barcode, "valorPagar": 100}, {"codigoTransacao": "eeee-5555"})
        patch_mod.execute()
        self.assertIn("eeee-5555", self.comments("IPO-1"))

    def test_bank_ids_are_html_escaped(self):
        self.add_order("IPO-1", "Processing")
        self.add_log("LOG-1", {"descricao": f"Payment {INVOICE}"}, {"codigoSolicitacao": "<script>x</script>"})
        patch_mod.execute()
        self.assertNotIn("<script>", self.comments("IPO-1"))

    def test_only_the_orders_flagged_by_this_run_get_a_comment(self):
        self.add_order("IPO-WAITING", "Awaiting Bank", invoice="PINV-A")
        self.add_order("IPO-VERIFY", "Needs Verification", invoice="PINV-B")
        patch_mod.execute()
        self.assertEqual(self.docs, {})

    def test_a_failing_comment_stops_nothing(self):
        self.add_order("IPO-1", "Processing", invoice=INVOICE, created=0)
        self.add_order("IPO-2", "Processing", invoice=OTHER_INVOICE, created=10)

        def get_doc(doctype, name):
            if name == "IPO-1":
                raise RuntimeError("timeline is down")
            return self.get_doc(doctype, name)

        patch_frappe(self, get_doc=get_doc)
        patch_mod.execute()  # must not raise
        self.assertEqual([self.status("IPO-1"), self.status("IPO-2")], ["Needs Verification"] * 2)
        self.assertEqual([self.lock("IPO-1"), self.lock("IPO-2")], [INVOICE, OTHER_INVOICE])
        self.assertTrue(self.comments("IPO-2"))
        self.assertIn("IPO-1", self.errors())

    def test_the_status_is_committed_before_the_comment_is_attempted(self):
        self.add_order("IPO-1", "Processing")
        seen = []

        def get_doc(doctype, name):
            seen.append(self.db.committed_row(DOCTYPE, name)["status"])
            return self.get_doc(doctype, name)

        patch_frappe(self, get_doc=get_doc)
        patch_mod.execute()
        self.assertEqual(seen, ["Needs Verification"])


class TestIdempotenceAndSilence(PatchCase):
    def _production_like_data(self):
        self.add_order("IPO-2026-00001", "Processing", created=0)
        self.add_order("IPO-2026-00002", "Approved", created=60)  # second blocking order of the same invoice
        self.add_order("IPO-2026-00003", "Failed", invoice=OTHER_INVOICE, created=120)
        self.add_order("IPO-2026-00004", "Awaiting Bank", invoice=OTHER_INVOICE, created=180)
        self.add_log("LOG-1", {"descricao": f"Payment {INVOICE}"}, {"codigoSolicitacao": "aaaa-1111"})

    def test_running_twice_changes_nothing(self):
        self._production_like_data()
        patch_mod.execute()
        rows = {name: dict(self.db.committed_row(DOCTYPE, name)) for name in self.db._committed[DOCTYPE]}
        comments = {name: list(doc.comments) for name, doc in self.docs.items()}
        first_run_events = len(self.db.events)

        patch_mod.execute()

        self.assertEqual({name: dict(self.db.committed_row(DOCTYPE, name)) for name in rows}, rows)
        self.assertEqual({name: list(doc.comments) for name, doc in self.docs.items()}, comments)
        writes = [event for event in self.db.events[first_run_events:] if event[0] == "set_value"]
        self.assertEqual(writes, [])

    def test_first_run_did_write_so_the_second_run_check_is_not_vacuous(self):
        self._production_like_data()
        patch_mod.execute()
        writes = [event for event in self.db.events if event[0] == "set_value"]
        self.assertEqual(len(writes), 3)  # one status, two locks (00001 and 00004)
        self.assertEqual(len(self.docs["IPO-2026-00001"].comments), 1)

    def test_no_external_alert_is_sent(self):
        self._production_like_data()
        with patch("brazil_module.services.banking.payment_alerts.alert_operator") as alert:
            patch_mod.execute()
        alert.assert_not_called()

    def test_the_patch_stays_plain(self):
        """No mark_*, no alert, no bank, no save(), no silent write - checked in the source."""
        source = inspect.getsource(patch_mod)
        for forbidden in ("alert_operator", "payment_alerts", "telegram", "payment_service", "mark_",
                          "inter_client", "InterAPIClient", "enqueue", ".save(", ".submit(", "update_modified"):
            self.assertNotIn(forbidden.lower(), source.lower(), forbidden)

    def test_functions_stay_under_fifty_lines(self):
        tree = ast.parse(inspect.getsource(patch_mod))
        sizes = {node.name: node.end_lineno - node.lineno + 1 for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
        self.assertIn("execute", sizes)
        self.assertEqual({name: size for name, size in sizes.items() if size >= 50}, {})


class TestPatchesTxt(unittest.TestCase):
    """Without sections Frappe runs every patch BEFORE the model sync - before the new columns exist."""

    def _sections(self) -> dict:
        parser = configparser.ConfigParser(allow_no_value=True, delimiters="\n")  # as frappe.modules.patch_handler
        parser.optionxform = str
        parser.read(PATCHES_TXT)
        return {section: list(parser[section]) for section in parser.sections()}

    def test_the_new_patch_runs_after_the_model_sync(self):
        self.assertEqual(self._sections().get("post_model_sync"), [PATCH_NAME])

    def test_the_existing_patches_stay_before_the_model_sync_under_the_same_names(self):
        self.assertEqual(
            self._sections().get("pre_model_sync"),
            ["brazil_module.patches.v1_0.migrate_from_old_apps", "brazil_module.patches.v1_1.rename_model_tiers"],
        )

    def test_every_listed_patch_is_a_module_with_execute(self):
        listed = [name for names in self._sections().values() for name in names]
        self.assertEqual(len(listed), 3)
        for dotted in listed:
            path = os.path.join(os.path.dirname(PACKAGE_ROOT), *dotted.split(".")) + ".py"
            with open(path) as fh:
                functions = [node.name for node in ast.parse(fh.read()).body if isinstance(node, ast.FunctionDef)]
            self.assertIn("execute", functions, dotted)


if __name__ == "__main__":
    unittest.main()
