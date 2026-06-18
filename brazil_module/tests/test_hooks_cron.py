"""Regression tests for the scheduler_events cron map in hooks.py.

The cron map is a Python dict literal: a duplicated cron expression silently
overwrites the earlier entry, dropping its jobs. This happened on 2026-05-27
when the daily briefing was moved to "*/15 * * * *" (colliding with the PIX
status check) — and a pre-existing collision on "0 * * * *" dropped the SEFAZ
fetch. Both jobs silently stopped being scheduled.

These tests assert (a) no cron expression is duplicated and (b) every job that
must run is actually present in the effective schedule.
"""
import ast
import os
import unittest


HOOKS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "hooks.py"
)


def _scheduler_events_node():
    """Parse hooks.py and return the raw AST dict for scheduler_events['cron'].

    Parsing the source (rather than importing) lets us detect duplicate keys
    BEFORE Python collapses them, which is the whole point of this test.
    """
    with open(HOOKS_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "scheduler_events" for t in node.targets
        ):
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and key.value == "cron":
                    return value
    raise AssertionError("scheduler_events['cron'] not found in hooks.py")


class TestCronSchedule(unittest.TestCase):
    def test_no_duplicate_cron_expressions(self):
        """A cron expression must appear exactly once, or its jobs are dropped."""
        cron = _scheduler_events_node()
        keys = [k.value for k in cron.keys]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(
            duplicates, [],
            f"Duplicate cron expression(s) {duplicates} — the later entry "
            f"silently overwrites the earlier one and drops its jobs.",
        )

    def test_all_required_jobs_are_scheduled(self):
        """Every job that must run has to appear in the effective schedule."""
        cron = _scheduler_events_node()
        scheduled = {
            elt.value
            for value in cron.values
            for elt in value.elts
            if isinstance(elt, ast.Constant)
        }
        required = [
            "brazil_module.services.fiscal.dfe_client.scheduled_fetch",
            "brazil_module.services.banking.payment_service.scheduled_payment_status_check",
            "brazil_module.services.banking.pix_service.scheduled_pix_status_check",
            "brazil_module.services.intelligence.recurring.daily_briefing.scheduled_briefing",
        ]
        for fn in required:
            self.assertIn(
                fn, scheduled,
                f"{fn} is not scheduled — likely dropped by a duplicate cron key.",
            )


if __name__ == "__main__":
    unittest.main()
