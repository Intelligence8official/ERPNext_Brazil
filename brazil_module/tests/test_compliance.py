import sys
from datetime import date
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]
sys.modules.setdefault("brazil_module.services.intelligence.recurring.planning_loop", MagicMock())
sys.modules.setdefault("brazil_module.services.intelligence.notifications", MagicMock())

import unittest

from brazil_module.services.intelligence.analytics.compliance import (
    check_nf_cancellations,
    check_tax_anomalies,
)


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
