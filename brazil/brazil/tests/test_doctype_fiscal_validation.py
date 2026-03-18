"""Tests for fiscal DocType controller validation."""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import sys

# Ensure frappe mock is in place
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils
    sys.modules["frappe.utils.password"] = MagicMock()
    sys.modules["frappe.model.document"] = MagicMock()

frappe = sys.modules["frappe"]

# Make Document a usable base class
_Document = type("Document", (), {"__init__": lambda self, *a, **kw: None})
sys.modules["frappe.model.document"].Document = _Document

# Ensure sub-modules importable
sys.modules.setdefault("frappe.utils.password", MagicMock())

# Mock cryptography chain for cert_utils import
for _m in [
    "cryptography", "cryptography.hazmat", "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.hazmat.primitives.serialization.pkcs12",
    "cryptography.x509",
]:
    sys.modules.setdefault(_m, MagicMock())


def _reset():
    frappe.reset_mock()
    frappe._ = lambda x: x
    frappe.throw.side_effect = None
    frappe.msgprint.side_effect = None
    frappe.db.set_value.side_effect = None
    frappe.db.exists.side_effect = None
    frappe.get_doc.side_effect = None
    frappe.get_single.side_effect = None
    frappe.get_all.side_effect = None
    frappe.log_error.side_effect = None


# Import controllers AFTER mock setup
from brazil.fiscal.doctype.nota_fiscal.nota_fiscal import NotaFiscal
from brazil.fiscal.doctype.nf_company_settings.nf_company_settings import NFCompanySettings
from brazil.fiscal.doctype.nota_fiscal_settings.nota_fiscal_settings import NotaFiscalSettings
from brazil.fiscal.doctype.nf_import_log.nf_import_log import NFImportLog
from brazil.fiscal.doctype.nota_fiscal_evento.nota_fiscal_evento import NotaFiscalEvento
from brazil.fiscal.doctype.nota_fiscal_item.nota_fiscal_item import NotaFiscalItem

# Patch now_datetime for predictable defaults
_NOW = "2024-01-15 12:00:00"


# ---------------------------------------------------------------------------
# NotaFiscal
# ---------------------------------------------------------------------------
class TestNotaFiscalValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_nf(self, **overrides):
        nf = NotaFiscal.__new__(NotaFiscal)
        nf.document_type = "NF-e"
        nf.chave_de_acesso = None
        nf.emitente_cnpj = None
        nf.purchase_invoice = None
        nf.data_recebimento = None
        nf.processing_status = None
        nf.invoice_number = None
        nf.vendor_name = None
        for k, v in overrides.items():
            setattr(nf, k, v)
        return nf

    def test_valid_chave_passes(self):
        """A valid 44-digit chave should not trigger throw."""
        nf = self._make_nf(chave_de_acesso="31240112345678000195550010000000011123456789")
        with patch("brazil.utils.chave_acesso.validate_chave_acesso", return_value=True):
            nf.validate()
        frappe.throw.assert_not_called()

    def test_invalid_chave_throws(self):
        """An invalid chave triggers frappe.throw."""
        frappe.throw.side_effect = Exception("thrown")
        nf = self._make_nf(chave_de_acesso="12345")
        with patch("brazil.utils.chave_acesso.validate_chave_acesso", return_value=False):
            with patch("brazil.utils.chave_acesso.clean_chave", return_value="12345"):
                with self.assertRaises(Exception):
                    nf.validate()
        frappe.throw.assert_called_once()

    def test_cnpj_warning_on_invalid(self):
        """Invalid CNPJ triggers a warning msgprint."""
        nf = self._make_nf(emitente_cnpj="12345678000100")
        with patch("brazil.utils.chave_acesso.validate_chave_acesso", return_value=True):
            with patch("brazil.utils.cnpj.validate_cnpj", return_value=False):
                with patch("brazil.utils.cnpj.clean_cnpj", return_value="12345678000100"):
                    nf.validate()
        frappe.msgprint.assert_called_once()

    def test_before_insert_sets_defaults(self):
        """before_insert sets data_recebimento and processing_status."""
        nf = self._make_nf()
        with patch("brazil.fiscal.doctype.nota_fiscal.nota_fiscal.now_datetime", return_value=_NOW):
            nf.before_insert()
        self.assertEqual(nf.data_recebimento, _NOW)
        self.assertEqual(nf.processing_status, "New")

    def test_on_update_links_purchase_invoice(self):
        """on_update sets chave on linked Purchase Invoice."""
        nf = self._make_nf(
            purchase_invoice="PINV-001",
            chave_de_acesso="31240112345678000195550010000000011123456789",
        )
        nf.on_update()
        frappe.db.set_value.assert_called_once_with(
            "Purchase Invoice", "PINV-001", "chave_de_acesso",
            "31240112345678000195550010000000011123456789",
        )

    def test_invoice_type_requires_number_or_vendor(self):
        """International Invoice without number or vendor throws."""
        frappe.throw.side_effect = Exception("thrown")
        nf = self._make_nf(document_type="Invoice", invoice_number=None, vendor_name=None)
        with self.assertRaises(Exception):
            nf.validate()
        frappe.throw.assert_called_once()


# ---------------------------------------------------------------------------
# NFCompanySettings
# ---------------------------------------------------------------------------
class TestNFCompanySettingsValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_settings(self, **overrides):
        s = NFCompanySettings.__new__(NFCompanySettings)
        s.cnpj = None
        s.certificate_file = None
        s.certificate_password = ""
        s.certificate_valid = 0
        s.certificate_expiry = None
        s.name = "TEST"
        s.is_new = lambda: False
        s.has_value_changed = lambda f: False
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    def test_cnpj_must_be_14_digits(self):
        """CNPJ with wrong length throws."""
        frappe.throw.side_effect = Exception("thrown")
        s = self._make_settings(cnpj="1234")
        with patch("brazil.utils.cnpj.clean_cnpj", return_value="1234"):
            with self.assertRaises(Exception):
                s.validate()
        frappe.throw.assert_called_once()

    def test_valid_cnpj_stored_cleaned(self):
        """Valid CNPJ is cleaned and stored."""
        s = self._make_settings(cnpj="12.345.678/0001-95")
        with patch("brazil.utils.cnpj.clean_cnpj", return_value="12345678000195"):
            with patch("brazil.utils.cnpj.validate_cnpj", return_value=True):
                s.validate()
        self.assertEqual(s.cnpj, "12345678000195")

    def test_cert_validation_success(self):
        """Certificate validation sets valid flag and expiry."""
        s = self._make_settings(
            certificate_file="/files/cert.pfx",
            certificate_password="secret",
        )
        s.has_value_changed = lambda f: True  # Force re-validation
        with patch("brazil.services.fiscal.cert_utils.validate_pfx_certificate", return_value="2025-12-31"):
            s.validate()
        self.assertEqual(s.certificate_valid, 1)
        self.assertEqual(s.certificate_expiry, "2025-12-31")

    def test_no_cert_clears_fields(self):
        """No certificate file clears validation fields."""
        s = self._make_settings(certificate_file=None, certificate_valid=1, certificate_expiry="2025-01-01")
        s.validate()
        self.assertEqual(s.certificate_valid, 0)
        self.assertIsNone(s.certificate_expiry)


# ---------------------------------------------------------------------------
# NotaFiscalSettings
# ---------------------------------------------------------------------------
class TestNotaFiscalSettingsValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_settings(self, **overrides):
        s = NotaFiscalSettings.__new__(NotaFiscalSettings)
        s.fetch_interval_minutes = 15
        s.email_import_enabled = False
        s.email_account = None
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    def test_fetch_interval_minimum_5(self):
        """Fetch interval below 5 throws."""
        frappe.throw.side_effect = Exception("thrown")
        s = self._make_settings(fetch_interval_minutes=2)
        with self.assertRaises(Exception):
            s.validate()
        frappe.throw.assert_called_once()

    def test_email_import_requires_account(self):
        """Email import enabled without account throws."""
        frappe.throw.side_effect = Exception("thrown")
        s = self._make_settings(email_import_enabled=True, email_account=None)
        with self.assertRaises(Exception):
            s.validate()
        frappe.throw.assert_called_once()

    def test_valid_settings_pass(self):
        """Valid settings don't throw."""
        s = self._make_settings(fetch_interval_minutes=10, email_import_enabled=False)
        s.validate()
        frappe.throw.assert_not_called()


# ---------------------------------------------------------------------------
# NFImportLog
# ---------------------------------------------------------------------------
class TestNFImportLogMethods(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_log(self, **overrides):
        log = NFImportLog.__new__(NFImportLog)
        log.started_at = None
        log.completed_at = None
        log.status = "Running"
        log.error_message = None
        log.documents_fetched = 0
        log.documents_created = 0
        log.documents_skipped = 0
        log.documents_failed = 0
        log.first_nsu = None
        log.last_nsu = None
        log.save = MagicMock()
        for k, v in overrides.items():
            setattr(log, k, v)
        return log

    def test_before_insert_defaults_started_at(self):
        """before_insert sets started_at if not set."""
        log = self._make_log()
        with patch("brazil.fiscal.doctype.nf_import_log.nf_import_log.now_datetime", return_value=_NOW):
            log.before_insert()
        self.assertEqual(log.started_at, _NOW)

    def test_mark_completed(self):
        """mark_completed sets status and completed_at."""
        log = self._make_log()
        with patch("brazil.fiscal.doctype.nf_import_log.nf_import_log.now_datetime", return_value=_NOW):
            log.mark_completed("Success")
        self.assertEqual(log.status, "Success")
        self.assertEqual(log.completed_at, _NOW)
        log.save.assert_called_once_with(ignore_permissions=True)

    def test_mark_failed(self):
        """mark_failed sets status, error, and completed_at."""
        log = self._make_log()
        with patch("brazil.fiscal.doctype.nf_import_log.nf_import_log.now_datetime", return_value=_NOW):
            log.mark_failed("Network timeout")
        self.assertEqual(log.status, "Failed")
        self.assertEqual(log.error_message, "Network timeout")
        log.save.assert_called_once_with(ignore_permissions=True)

    def test_update_counts_accumulates(self):
        """update_counts adds to existing counts."""
        log = self._make_log(documents_fetched=5, documents_created=3)
        log.update_counts(fetched=2, created=1, skipped=1)
        self.assertEqual(log.documents_fetched, 7)
        self.assertEqual(log.documents_created, 4)
        self.assertEqual(log.documents_skipped, 1)


# ---------------------------------------------------------------------------
# NotaFiscalEvento (new validation)
# ---------------------------------------------------------------------------
class TestNotaFiscalEventoValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_evento(self, **overrides):
        e = NotaFiscalEvento.__new__(NotaFiscalEvento)
        e.nota_fiscal = None
        e.event_type = None
        e.event_date = None
        for k, v in overrides.items():
            setattr(e, k, v)
        return e

    def test_requires_nota_fiscal(self):
        """Missing nota_fiscal throws."""
        frappe.throw.side_effect = Exception("thrown")
        e = self._make_evento(event_type="Ciencia")
        with self.assertRaises(Exception):
            e.validate()
        frappe.throw.assert_called_once()

    def test_requires_event_type(self):
        """Missing event_type throws."""
        frappe.throw.side_effect = Exception("thrown")
        e = self._make_evento(nota_fiscal="NF-001")
        with self.assertRaises(Exception):
            e.validate()
        frappe.throw.assert_called_once()

    def test_before_insert_defaults_event_date(self):
        """before_insert sets event_date if not set."""
        e = self._make_evento()
        with patch("brazil.fiscal.doctype.nota_fiscal_evento.nota_fiscal_evento.now_datetime", return_value=_NOW):
            e.before_insert()
        self.assertEqual(e.event_date, _NOW)

    def test_valid_evento_passes(self):
        """Valid evento passes validation."""
        e = self._make_evento(nota_fiscal="NF-001", event_type="Ciencia")
        e.validate()
        frappe.throw.assert_not_called()


# ---------------------------------------------------------------------------
# NotaFiscalItem (new validation)
# ---------------------------------------------------------------------------
class TestNotaFiscalItemValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_item(self, **overrides):
        item = NotaFiscalItem.__new__(NotaFiscalItem)
        item.descricao = "Test Item"
        item.quantidade = 1.0
        item.ncm = None
        item.cfop = None
        for k, v in overrides.items():
            setattr(item, k, v)
        return item

    def test_requires_descricao(self):
        """Missing descricao throws."""
        frappe.throw.side_effect = Exception("thrown")
        item = self._make_item(descricao=None)
        with self.assertRaises(Exception):
            item.validate()
        frappe.throw.assert_called_once()

    def test_quantidade_must_be_positive(self):
        """Zero or negative quantity throws."""
        frappe.throw.side_effect = Exception("thrown")
        item = self._make_item(quantidade=0)
        with self.assertRaises(Exception):
            item.validate()
        frappe.throw.assert_called_once()

    def test_ncm_must_be_8_digits(self):
        """NCM with wrong length throws."""
        frappe.throw.side_effect = Exception("thrown")
        item = self._make_item(ncm="1234")
        with self.assertRaises(Exception):
            item.validate()
        frappe.throw.assert_called_once()

    def test_valid_ncm_passes(self):
        """Valid 8-digit NCM passes."""
        item = self._make_item(ncm="84713012")
        item.validate()
        frappe.throw.assert_not_called()

    def test_cfop_must_be_4_digits(self):
        """CFOP with wrong length throws."""
        frappe.throw.side_effect = Exception("thrown")
        item = self._make_item(cfop="12")
        with self.assertRaises(Exception):
            item.validate()
        frappe.throw.assert_called_once()

    def test_valid_cfop_passes(self):
        """Valid 4-digit CFOP passes."""
        item = self._make_item(cfop="5102")
        item.validate()
        frappe.throw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
