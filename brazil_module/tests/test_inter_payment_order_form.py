"""What the Inter Payment Order form actually DOES, by running it (spec 4.3).

``test_inter_payment_order.py`` greps the form script; a grep cannot tell whether a second click
sends a second request, or whether a toast claims the bank answered when it never did. This module
runs the real script in node against a stubbed desk (``_form_js_harness.js``) and asserts on the
behaviour. It skips when node is absent, so it never blocks a bench that has no JS toolchain.
"""

import json
import os
import shutil
import subprocess
import unittest

from brazil_module.tests._payment_fakes import install_frappe_mock

install_frappe_mock()

from brazil_module.services.banking.payment_guards import CANCELLABLE_STATUSES

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TESTS_DIR)
HARNESS_PATH = os.path.join(_TESTS_DIR, "_form_js_harness.js")
FORM_JS_PATH = os.path.join(_APP_DIR, "bancos", "doctype", "inter_payment_order", "inter_payment_order.js")

# poll_bank_status answers in which the bank really spoke about this order.
ANSWERED = ("unchanged", "completed", "failed", "awaiting_bank", "needs_verification")
# ... and the ones where it did not, including a status this version has never heard of.
NOT_ANSWERED = ("blocked", "error", "no_bank_id", "skipped", "brand_new_status_from_a_later_version")


_HARNESS_RESULT = None


def _run_harness() -> dict:
    """The scenarios are the same for every class here, so run node once for the whole module."""
    global _HARNESS_RESULT
    if _HARNESS_RESULT is not None:
        return _HARNESS_RESULT
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        raise unittest.SkipTest("node is not installed: the form behaviour tests need it")
    done = subprocess.run(
        [node, HARNESS_PATH, FORM_JS_PATH],
        capture_output=True, text=True, timeout=120, cwd=_TESTS_DIR,
    )
    if done.returncode != 0:
        raise AssertionError(f"the form harness failed:\n{done.stderr}")
    _HARNESS_RESULT = json.loads(done.stdout)
    return _HARNESS_RESULT


class FormBehaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = _run_harness()


class TestSecondClick(FormBehaviour):
    """A slow bank plus an impatient operator must not become two requests."""

    def test_clicking_a_button_twice_calls_the_server_once(self):
        for scenario in ("double_click_approve", "double_click_check"):
            with self.subTest(scenario=scenario):
                self.assertEqual(self.result[scenario]["calls"], 1)

    def test_clicking_resolve_twice_calls_the_server_once(self):
        self.assertEqual(self.result["double_click_dialog"]["calls"], 1)
        self.assertEqual(self.result["double_click_dialog"]["hides"], 1)


class TestResolveDialog(FormBehaviour):
    def test_a_refusal_keeps_the_dialog_open_with_what_was_typed(self):
        refused = self.result["resolve_refused"]
        self.assertFalse(refused["hidden"], "the operator would have to type everything again")
        self.assertTrue(refused["re_enabled"])

    def test_success_closes_the_dialog(self):
        self.assertTrue(self.result["resolve_success"]["hidden"])

    def test_not_paid_carries_the_three_confirmations_into_the_note(self):
        """The checkboxes live in the browser; the timeline is where the attestation must survive."""
        note = self.result["resolve_not_paid"]["args"]["note"]
        for checked in ("statement", "approval queue", "scheduled payments"):
            with self.subTest(checked=checked):
                self.assertIn(checked, note)
        self.assertIn("nothing in the statement", note, "the operator's own words must be kept")

    def test_an_outcome_that_is_not_not_paid_sends_the_note_untouched(self):
        self.assertEqual(self.result["resolve_success"]["args"]["note"], "")


class TestResolveOutcomes(FormBehaviour):
    def test_all_three_outcomes_are_always_offered(self):
        """Whether the bank still has the last word depends on its id, its last answer and the age
        of the request. Copying that rule here only produced a dead end: an order the bank had
        already reported as failed offered no way to say it was not paid, so it stayed in
        verification forever with its invoice locked. The server refuses with its reason instead,
        and the dialog now keeps what was typed.
        """
        for scenario in ("outcomes_without_a_bank_id", "outcomes_with_a_bank_id"):
            with self.subTest(scenario=scenario):
                self.assertEqual(self.result[scenario]["values"], ["at_bank", "paid", "not_paid"])


class TestStaleForm(FormBehaviour):
    def test_navigating_away_during_the_reload_cancels_the_action(self):
        """One Form object serves every document of a doctype: frm.doc can change under an await."""
        moved = self.result["navigation_during_reload"]
        self.assertEqual(moved["calls"], 0, "the confirmed order was not the one that would be sent")
        self.assertTrue(moved["warned"])
        self.assertEqual(moved["reloads_of_b"], 0, "the delayed reload landed on another document")


class TestBankStatusHonesty(FormBehaviour):
    """Saying 'the bank was asked' when it was not is how an operator settles an unpaid invoice."""

    def test_it_claims_a_fresh_answer_only_when_the_bank_answered(self):
        for status in ANSWERED:
            with self.subTest(status=status):
                self.assertEqual(self.result["check_bank_status"][status]["told"], "show_alert")

    def test_it_warns_when_the_bank_was_not_consulted(self):
        for status in NOT_ANSWERED:
            with self.subTest(status=status):
                self.assertEqual(
                    self.result["check_bank_status"][status]["told"], "msgprint",
                    "an unknown or failed answer must not read as a fresh bank status",
                )


class TestBankTextIsEscaped(FormBehaviour):
    def test_the_bank_status_cannot_inject_html_into_the_intro(self):
        intro = self.result["intro_escapes_bank_text"]["intro"]
        self.assertNotIn("<img", intro)
        self.assertIn("&#60;img", intro)


class TestButtons(FormBehaviour):
    EXPECTED = {
        "Draft": [],
        "Pending Approval": ["Approve"],
        "Approved": ["Execute Payment"],
        "Processing": [],
        "Awaiting Bank": ["Check Bank Status"],
        "Needs Verification": ["Resolve Verification"],
        "Completed": ["Create Payment Entry"],
        "Failed": [],
        "Cancelled": [],
        "Completed with entry": [],
    }

    def test_each_status_offers_exactly_the_actions_of_the_spec(self):
        for status, labels in self.EXPECTED.items():
            with self.subTest(status=status):
                self.assertEqual(self.result["buttons"][status]["labels"], labels)

    def test_a_reader_gets_no_buttons(self):
        """Every action is a document method on a submitted document: without submit it is refused."""
        self.assertEqual(self.result["buttons_without_submit_perm"]["labels"], [])

    def test_cancel_is_hidden_exactly_where_the_server_refuses_it(self):
        for status, shown in self.result["buttons"].items():
            if status == "Completed with entry":
                continue
            with self.subTest(status=status):
                self.assertEqual(
                    shown["cancel_cleared"], status not in CANCELLABLE_STATUSES,
                    "the form must not offer a cancel that before_cancel will refuse",
                )

    def test_a_completed_order_without_its_entry_still_cannot_be_cancelled(self):
        self.assertTrue(self.result["buttons"]["Completed with entry"]["cancel_cleared"])


class TestConstantsAgreeWithTheServer(unittest.TestCase):
    def test_the_form_and_payment_guards_cancel_the_same_statuses(self):
        with open(FORM_JS_PATH, encoding="utf-8") as handle:
            source = handle.read()
        declared = source[source.index("IPO_CANCELLABLE_STATUSES = ["):]
        declared = declared[declared.index("[") + 1:declared.index("]")]
        in_js = tuple(part.strip().strip('"') for part in declared.split(",") if part.strip())
        self.assertEqual(in_js, CANCELLABLE_STATUSES)


if __name__ == "__main__":
    unittest.main()
