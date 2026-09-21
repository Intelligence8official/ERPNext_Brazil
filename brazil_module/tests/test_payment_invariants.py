"""Repo-wide static tripwires for the outbound payment invariants.

See docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md (sections 2 and 7).

The behaviour is tested where it lives (``test_payment_service.py`` and friends). These tests
read the *source* of the whole package instead, so that a new file, a new scheduler or a
well-meant shortcut cannot open a second way to the bank without a test turning red:

- I1  ``send_pix`` / ``pay_barcode`` are reached only through ``execute_payment_order``;
      ``send_ted`` is called from nowhere.
- I3  nothing scheduled can reach the sender.
- I5  no ``Document.save()`` in the payment service, no silent (``update_modified=False``) write.

Every scanner is first pointed at a small offending tree (``TestTheTripwiresCanTrip``): a
tripwire that cannot fail protects nothing. Nothing here imports frappe.
"""

import ast
import os
import re
import shutil
import tempfile
import unittest

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(PACKAGE_ROOT)

SERVICE = "services/banking/payment_service.py"
CLIENT = "services/banking/inter_client.py"
CONTROLLER = "bancos/doctype/inter_payment_order/inter_payment_order.py"
MIGRATION_PATCH = "patches/v1_1/flag_stuck_payment_orders.py"
AGENT_EXECUTOR = "services/intelligence/action_executor.py"

SENDERS = ("send_pix", "pay_barcode", "send_ted")
OUTBOUND_ENDPOINTS = ("/banking/v2/pix", "/banking/v2/pagamento", "/banking/v2/ted")
ENTRY_POINTS_TO_THE_BANK = {"execute_payment_order", "enqueue_payment_execution"}
SILENT_WRITE = re.compile(r"update_modified\s*=\s*False")
HOURLY = "0 * * * *"
STATUS_CHECK = "brazil_module.services.banking.payment_service.scheduled_payment_status_check"
EXCLUDED_DIRECTORIES = ("tests", "__pycache__", "node_modules")


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

def production_sources(root: str) -> dict[str, str]:
    """``{relative path: source}`` of every ``.py`` under ``root`` that is not a test."""
    sources = {}
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(name for name in subdirectories if name not in EXCLUDED_DIRECTORIES)
        for filename in sorted(files):
            if filename.endswith(".py"):
                path = os.path.join(directory, filename)
                with open(path, encoding="utf-8") as fh:
                    sources[os.path.relpath(path, root).replace(os.sep, "/")] = fh.read()
    return sources


def files_containing(root: str, needle: str) -> list[str]:
    return [path for path, source in production_sources(root).items() if needle in source]


def files_with_string_constant(root: str, fragment: str) -> list[str]:
    """Files holding ``fragment`` inside a string literal (comments and names do not count)."""
    found = []
    for path, source in production_sources(root).items():
        constants = (node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant))
        if any(isinstance(value, str) and fragment in value for value in constants):
            found.append(path)
    return found


def functions_of(source: str) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def method_calls(function: ast.AST, names: tuple) -> list[str]:
    """Names in ``names`` that ``function`` calls as ``<something>.<name>(...)``."""
    return [
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in names
    ]


def functions_reaching_a_sender(source: str) -> set[str]:
    """Functions of the module from which a ``send_*`` / ``pay_barcode`` call can be reached.

    A function *refers* to another when its name appears anywhere in the body - called, or
    handed to ``frappe.enqueue``. Coarse on purpose: a false alarm costs a look, a miss costs money.
    """
    functions = functions_of(source)
    refers_to = {
        name: {node.id for node in ast.walk(function) if isinstance(node, ast.Name) and node.id in functions}
        for name, function in functions.items()
    }
    reaching = {name for name, function in functions.items() if method_calls(function, SENDERS)}
    grew = True
    while grew:
        more = {name for name, referred in refers_to.items() if referred & reaching} - reaching
        reaching |= more
        grew = bool(more)
    return reaching


def cron_jobs(hooks_source: str, expression: str) -> list[str]:
    for node in ast.parse(hooks_source).body:
        is_scheduler = isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "scheduler_events" for target in node.targets
        )
        if is_scheduler:
            events = ast.literal_eval(node.value)
            return list(events.get("cron", {}).get(expression, []))
    raise AssertionError("scheduler_events not found in hooks.py")


def module_constant(source: str, name: str):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


def read(relative_path: str, root: str | None = None) -> str:
    with open(os.path.join(root or PACKAGE_ROOT, relative_path), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# The tripwires can trip
# ---------------------------------------------------------------------------

OFFENDING_SERVICE = '''
def enqueue_payment_execution(name):
    frappe.enqueue(execute_payment_order, payment_order_name=name)

def execute_payment_order(name):
    return _send(client, name)

def _send(client, order):
    return client.send_pix({}, order)

def poll_bank_status(name):
    return client.get_pix_payment(name)

def scheduled_payment_status_check():
    for name in stuck_orders():
        _retry(name)

def _retry(name):
    execute_payment_order(name)  # the production defect: the hourly cron re-sent the order
'''


class TestTheTripwiresCanTrip(unittest.TestCase):
    def _tree(self, files: dict) -> str:
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for relative_path, source in files.items():
            path = os.path.join(root, relative_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source)
        return root

    def test_a_send_outside_the_allowed_files_is_seen_and_tests_are_ignored(self):
        root = self._tree({
            "services/intelligence/recurring/planning_loop.py": "client.send_pix(data, key)\n",
            "services/banking/payment_service.py": "client.send_pix(data, key)\n",
            "tests/test_something.py": "client.send_pix(data, key)\n",
            "services/tests/test_nested.py": "client.send_pix(data, key)\n",
            "public/js/not_python.js": "client.send_pix(data, key)\n",
        })
        self.assertEqual(
            files_containing(root, ".send_pix("),
            ["services/banking/payment_service.py", "services/intelligence/recurring/planning_loop.py"],
        )

    def test_an_endpoint_in_a_string_is_seen_and_one_in_a_comment_is_not(self):
        root = self._tree({
            "a.py": 'client._request("POST", "/banking/v2/pix", data=payload)\n',
            "b.py": "LIMIT = 90  # GET /banking/v2/pix/{id} only covers 90 days\n",
        })
        self.assertEqual(files_with_string_constant(root, "/banking/v2/pix"), ["a.py"])

    def test_a_cron_that_reaches_the_sender_is_seen(self):
        reaching = functions_reaching_a_sender(OFFENDING_SERVICE)
        self.assertIn("scheduled_payment_status_check", reaching)
        self.assertIn("_retry", reaching)
        self.assertNotIn("poll_bank_status", reaching)

    def test_method_calls_counts_every_call_site(self):
        source = "def f(c):\n    c.send_pix(1, 2)\n    c.send_pix(3, 4)\n    c.get_pix_payment(5)\n"
        function = functions_of(source)["f"]
        self.assertEqual(method_calls(function, SENDERS), ["send_pix", "send_pix"])

    def test_a_silent_write_is_seen_however_it_is_spaced(self):
        for text in ("update_modified=False", "update_modified = False", "update_modified= False"):
            self.assertTrue(SILENT_WRITE.search(f"frappe.db.set_value(d, n, f, v, {text})"), text)
        self.assertIsNone(SILENT_WRITE.search("frappe.db.set_value(d, n, f, v, update_modified=True)"))

    def test_cron_jobs_reads_the_list_of_an_expression(self):
        hooks = 'scheduler_events = {"cron": {"0 * * * *": ["a.b.c"], "*/5 * * * *": ["d.e.f"]}, "daily": []}\n'
        self.assertEqual(cron_jobs(hooks, HOURLY), ["a.b.c"])
        self.assertEqual(cron_jobs(hooks, "1 2 3 4 5"), [])


# ---------------------------------------------------------------------------
# I1 / I3 - one path to the bank
# ---------------------------------------------------------------------------

class TestSinglePathToTheBank(unittest.TestCase):
    def test_the_scan_covers_the_package(self):
        sources = production_sources(PACKAGE_ROOT)
        for expected in (SERVICE, CLIENT, CONTROLLER, "hooks.py", "api/__init__.py",
                         "services/intelligence/recurring/planning_loop.py"):
            self.assertIn(expected, sources)
        self.assertEqual([path for path in sources if "/tests/" in f"/{path}"], [])

    def test_payment_sends_appear_only_in_the_service_and_the_client(self):
        for sender in ("send_pix", "pay_barcode"):
            found = files_containing(PACKAGE_ROOT, f".{sender}(")
            self.assertEqual([path for path in found if path not in (SERVICE, CLIENT)], [], sender)
            self.assertIn(SERVICE, found, f"{sender}: the scan no longer finds the real call - it is blind")

    def test_send_ted_is_called_from_nowhere(self):
        self.assertEqual(files_containing(PACKAGE_ROOT, ".send_ted("), [])

    def test_the_sender_names_are_known_to_no_other_module(self):
        """Also catches ``getattr(client, "send_pix")``, an alias or a wrapper in another file."""
        for sender in SENDERS:
            found = files_containing(PACKAGE_ROOT, sender)
            self.assertEqual([path for path in found if path not in (SERVICE, CLIENT)], [], sender)

    def test_the_outbound_endpoints_are_known_only_to_the_client(self):
        for endpoint in OUTBOUND_ENDPOINTS:
            self.assertEqual(files_with_string_constant(PACKAGE_ROOT, endpoint), [CLIENT], endpoint)

    def test_nothing_outside_the_client_issues_a_raw_inter_request(self):
        self.assertEqual([path for path in files_containing(PACKAGE_ROOT, "._request(") if path != CLIENT], [])

    def test_the_service_sends_from_exactly_one_place(self):
        callers = {
            name: calls
            for name, function in functions_of(read(SERVICE)).items()
            if (calls := method_calls(function, SENDERS))
        }
        self.assertEqual(len(callers), 1, callers)
        self.assertEqual(sorted(next(iter(callers.values()))), ["pay_barcode", "send_pix"])

    def test_only_execute_payment_order_reaches_the_sender(self):
        """I1 and I3: the cron, the poll, the resolution and the hooks cannot send."""
        source = read(SERVICE)
        public = {name for name in functions_of(source) if not name.startswith("_")}
        self.assertTrue(ENTRY_POINTS_TO_THE_BANK <= public, "the entry points were renamed: update this tripwire")
        self.assertEqual(functions_reaching_a_sender(source) & public, ENTRY_POINTS_TO_THE_BANK)

    def test_no_other_module_executes_an_order_by_itself(self):
        """Everything else goes through ``enqueue_payment_execution`` (one deduplicated job per order).

        The production defect was a cron calling ``execute_payment_order``; hooks.py is scanned too.
        """
        found = files_containing(PACKAGE_ROOT, "execute_payment_order")
        self.assertEqual([path for path in found if path != SERVICE], [])
        self.assertIn(SERVICE, found, "the scan no longer finds the function - it is blind")

    def test_every_public_function_of_the_spec_is_under_watch(self):
        public = {name for name in functions_of(read(SERVICE)) if not name.startswith("_")}
        for name in ("scheduled_payment_status_check", "poll_bank_status", "resolve_verification",
                     "create_payment_entry_for_order", "on_payment_entry_cancel", "mark_needs_verification"):
            self.assertIn(name, public)


# ---------------------------------------------------------------------------
# I5 - persistence cannot be vetoed, and never goes unnoticed
# ---------------------------------------------------------------------------

class TestPersistence(unittest.TestCase):
    def test_the_payment_service_never_saves_a_document(self):
        self.assertNotIn(".save(", read(SERVICE))

    def test_no_silent_write_on_a_payment_order(self):
        for path in (SERVICE, CONTROLLER, MIGRATION_PATCH):
            self.assertIsNone(SILENT_WRITE.search(read(path)), path)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

class TestWiring(unittest.TestCase):
    def test_the_hourly_cron_still_runs_the_status_check(self):
        self.assertIn(STATUS_CHECK, cron_jobs(read("hooks.py"), HOURLY))

    def test_the_status_check_is_scheduled_exactly_once(self):
        events = module_constant(read("hooks.py"), "scheduler_events")
        scheduled = [job for jobs in events["cron"].values() for job in jobs]
        scheduled += [job for key, jobs in events.items() if key != "cron" for job in jobs]
        self.assertEqual(scheduled.count(STATUS_CHECK), 1)

    def test_the_agent_cannot_create_payment_orders(self):
        """The order path has its own guarded entry points; the generic executor is not one of them."""
        allowlist = module_constant(read(AGENT_EXECUTOR), "ACTION_ALLOWLIST")
        self.assertIn("Purchase Invoice", allowlist, "the allowlist was not read")
        self.assertNotIn("Inter Payment Order", allowlist)


# ---------------------------------------------------------------------------
# Developer guide
# ---------------------------------------------------------------------------

class TestTheWatchmanOnlyReads(unittest.TestCase):
    """D1 of the banking-health spec: no unattended job may write to Banco Inter again."""

    WATCHMAN = "services/banking/banking_health.py"
    WRITES = (
        "send_pix", "pay_barcode", "send_ted", "register_webhook", "delete_webhook",
        "create_boleto", "cancel_boleto", "create_pix_charge", "create_pix_charge_with_due_date",
    )

    def test_it_names_no_write_method_of_the_client(self):
        source = read(self.WATCHMAN)
        for method in self.WRITES:
            with self.subTest(method=method):
                self.assertNotIn(f".{method}(", source)

    def test_the_only_thing_it_writes_is_the_settings_single(self):
        source = read(self.WATCHMAN)
        self.assertNotIn("set_value(", source.replace("set_single_value(", ""))
        self.assertNotIn("frappe.new_doc(", source)
        self.assertNotIn(".insert(", source)


class TestDeveloperGuide(unittest.TestCase):
    def setUp(self):
        self.guide = read("CLAUDE.md", REPO_ROOT)

    def test_the_outdated_package_path_is_gone(self):
        outdated = [line for line in self.guide.splitlines() if re.search(r"brazil/|\bbrazil\.services", line)]
        self.assertEqual(outdated, [], "the package root is brazil_module/")

    def test_every_documented_directory_exists(self):
        documented = re.findall(r"^\| `(brazil_module/[^`]*)` \|", self.guide, flags=re.MULTILINE)
        self.assertGreaterEqual(len(documented), 8)
        self.assertEqual([path for path in documented if not os.path.isdir(os.path.join(REPO_ROOT, path))], [])

    def test_the_payment_invariants_are_documented(self):
        expected = ["## Outbound payments", "docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md",
                    ".venv/bin/python -m pytest"]
        expected += [f"**I{number} - " for number in range(1, 10)]
        self.assertEqual([text for text in expected if text not in self.guide], [])

    def test_the_documented_spec_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(
            REPO_ROOT, "docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md")))


if __name__ == "__main__":
    unittest.main()
