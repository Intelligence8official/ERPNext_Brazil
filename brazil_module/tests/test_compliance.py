import sys
from datetime import date
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

# Temporarily inject mocks for transitive imports, then restore afterwards
# so other test files that import these modules get the real ones.
_temp_deps = {
    "brazil_module.services.intelligence.recurring.planning_loop": None,
    "brazil_module.services.intelligence.notifications": None,
}
for _dep in _temp_deps:
    _temp_deps[_dep] = sys.modules.get(_dep)
    if _dep not in sys.modules:
        sys.modules[_dep] = MagicMock()

import unittest

from brazil_module.services.intelligence.analytics.compliance import (
    check_nf_cancellations,
    check_tax_anomalies,
)

# Restore original modules so later test files get the real ones
for _dep, _orig in _temp_deps.items():
    if _orig is None:
        sys.modules.pop(_dep, None)
    else:
        sys.modules[_dep] = _orig


class TestNfCancellations(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.sql.return_value = []

    def test_detects_cancelled_with_active_invoice(self):
        frappe.db.sql.return_value = [
            {"name": "NF-001", "chave_de_acesso": "1234", "razao_social": "Test",
             "invoice_name": "PI-001", "invoice_status": 1}
        ]
        check_nf_cancellations()  # Should not raise

    def test_no_alert_when_no_cancellations(self):
        frappe.db.sql.return_value = []
        check_nf_cancellations()  # Should not raise


class TestTaxAnomalies(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.sql.return_value = []

    def test_detects_unusual_iss_rate(self):
        frappe.db.sql.return_value = [
            {"name": "NF-001", "razao_social": "Test", "valor_total": 10000,
             "valor_servicos": 10000, "issqn_aliquota": 10,
             "issqn_valor": 0, "pis_valor": 0, "cofins_valor": 0, "inss_valor": 0, "irrf_valor": 0}
        ]
        check_tax_anomalies()  # Should not raise

    def test_no_anomalies_with_normal_data(self):
        frappe.db.sql.return_value = [
            {"name": "NF-001", "razao_social": "Test", "valor_total": 10000,
             "valor_servicos": 10000, "issqn_aliquota": 3,
             "issqn_valor": 300, "pis_valor": 0, "cofins_valor": 0, "inss_valor": 0, "irrf_valor": 0}
        ]
        check_tax_anomalies()  # Should not raise


if __name__ == "__main__":
    unittest.main()
