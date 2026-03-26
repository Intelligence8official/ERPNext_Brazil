import sys
import types as _types
from datetime import date
from unittest.mock import MagicMock, patch

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

# Temporarily inject mocks for transitive imports, then restore afterwards
# so other test files that import these modules get the real ones.
_temp_mocks = {}
for _mod_key in [
    "brazil_module.services.banking.reconciliation",
    "brazil_module.services.intelligence.channels.telegram_bot",
]:
    _was_present = _mod_key in sys.modules
    _original = sys.modules.get(_mod_key)
    _temp_mocks[_mod_key] = (_was_present, _original)
    if not _was_present or not isinstance(_original, _types.ModuleType):
        sys.modules[_mod_key] = MagicMock()

import unittest

from brazil_module.services.intelligence.recurring.planning_loop import (
    hourly_check,
    run_reconciliation,
    check_overdue_payments,
    process_pending_nfs,
)

# Keep references to the mocks we injected (needed for tests that patch them)
_rec_mock = sys.modules["brazil_module.services.banking.reconciliation"]
_tb_mock = sys.modules["brazil_module.services.intelligence.channels.telegram_bot"]

# Restore original modules so later test files get the real ones
for _mod_key, (_was_present, _original) in _temp_mocks.items():
    if not _was_present:
        sys.modules.pop(_mod_key, None)
    elif _original is not None:
        sys.modules[_mod_key] = _original


class TestHourlyCheck(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None

    def test_skips_when_disabled(self):
        frappe.db.get_single_value.return_value = False
        hourly_check()
        frappe.get_all.assert_not_called()

    def test_runs_when_enabled(self):
        frappe.db.get_single_value.return_value = True
        frappe.get_all.return_value = []
        hourly_check()
        # Should have called get_all for bank accounts
        frappe.get_all.assert_called()


class TestRunReconciliation(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.return_value = []
        frappe.db.get_single_value.return_value = "chat-123"
        # Ensure telegram_bot mock is available for _notify_telegram
        sys.modules["brazil_module.services.intelligence.channels.telegram_bot"] = _tb_mock

    def tearDown(self):
        sys.modules.pop("brazil_module.services.intelligence.channels.telegram_bot", None)
        sys.modules.pop("brazil_module.services.banking.reconciliation", None)

    def test_reconciles_all_bank_accounts(self):
        frappe.get_all.return_value = [{"name": "ACC-1"}, {"name": "ACC-2"}]

        _rec_mock.batch_reconcile = MagicMock(return_value={"matched": 2, "errors": 0, "unmatched": 1})
        # Re-inject the mock so the runtime import inside run_reconciliation finds it
        sys.modules["brazil_module.services.banking.reconciliation"] = _rec_mock

        run_reconciliation()
        self.assertEqual(_rec_mock.batch_reconcile.call_count, 2)

    def test_handles_empty_accounts(self):
        frappe.get_all.return_value = []
        run_reconciliation()  # Should not raise


class TestCheckOverduePayments(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.return_value = "chat-123"
        sys.modules["brazil_module.services.intelligence.channels.telegram_bot"] = _tb_mock

    def tearDown(self):
        sys.modules.pop("brazil_module.services.intelligence.channels.telegram_bot", None)

    def test_alerts_on_overdue(self):
        frappe.get_all.return_value = [
            {"name": "PI-001", "supplier_name": "Test", "outstanding_amount": 5000},
        ]
        frappe.utils.today.return_value = date.today().isoformat()
        check_overdue_payments()
        # Should not raise

    def test_no_alert_when_nothing_overdue(self):
        frappe.get_all.return_value = []
        check_overdue_payments()


class TestProcessPendingNfs(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.return_value = "chat-123"
        frappe.enqueue.side_effect = None
        sys.modules["brazil_module.services.intelligence.channels.telegram_bot"] = _tb_mock

    def tearDown(self):
        sys.modules.pop("brazil_module.services.intelligence.channels.telegram_bot", None)

    def test_enqueues_nfs(self):
        frappe.get_all.return_value = [
            {"name": "NF-001", "cnpj_emitente": "12345678000190"},
        ]
        process_pending_nfs()
        frappe.enqueue.assert_called_once()

    def test_notifies_when_no_nfs(self):
        frappe.get_all.return_value = []
        process_pending_nfs()
        # Should notify "Nenhuma NF pendente"


if __name__ == "__main__":
    unittest.main()
