"""Tests for NF processing pipeline."""

import unittest
from unittest.mock import MagicMock, patch
import sys

# Mock frappe
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    frappe_mock._ = lambda x: x
    sys.modules["frappe"] = frappe_mock
    sys.modules["frappe.utils"] = frappe_mock.utils

# Mock service dependencies that processor.py imports, saving originals to restore later.
# This avoids polluting sys.modules for other test files.
_mocked_deps = {}
_dep_modules = [
    "brazil_module.services.fiscal.supplier_manager",
    "brazil_module.services.fiscal.item_manager",
    "brazil_module.services.fiscal.po_matcher",
    "brazil_module.services.fiscal.invoice_creator",
]
for mod in _dep_modules:
    if mod not in sys.modules:
        _mocked_deps[mod] = None
        sys.modules[mod] = MagicMock()
    # If already imported (real module), leave it alone

from brazil_module.services.fiscal.processor import (
    NFProcessor,
    process_new_nf,
    cleanup_processed_xmls,
)

# Restore sys.modules: remove mocks we injected so other test files
# can import the real modules.
for mod, orig in _mocked_deps.items():
    if orig is None:
        del sys.modules[mod]
    else:
        sys.modules[mod] = orig


class TestNFProcessorInit(unittest.TestCase):
    def test_init_loads_settings(self):
        with patch("brazil_module.services.fiscal.processor.frappe") as mock_frappe:
            mock_frappe.get_single.return_value = MagicMock()
            processor = NFProcessor()
            mock_frappe.get_single.assert_called_with("Nota Fiscal Settings")


class TestProcessNewNf(unittest.TestCase):
    def test_enqueue_on_new_when_enabled(self):
        with patch("brazil_module.services.fiscal.processor.frappe") as mock_frappe:
            settings = MagicMock()
            settings.enabled = True
            settings.auto_process = True
            mock_frappe.get_single.return_value = settings

            doc = MagicMock()
            doc.name = "NF-001"
            doc.processing_status = ""

            process_new_nf(doc)
            mock_frappe.enqueue.assert_called_once()

    def test_skip_when_disabled(self):
        with patch("brazil_module.services.fiscal.processor.frappe") as mock_frappe:
            settings = MagicMock()
            settings.enabled = False
            mock_frappe.get_single.return_value = settings

            doc = MagicMock()
            doc.name = "NF-001"
            process_new_nf(doc)
            mock_frappe.enqueue.assert_not_called()


class TestCleanupProcessedXmls(unittest.TestCase):
    def test_no_matching_nfs_returns_zero(self):
        with patch("brazil_module.services.fiscal.processor.frappe") as mock_frappe:
            settings = MagicMock()
            settings.xml_retention_days = 90
            mock_frappe.get_single.return_value = settings
            mock_frappe.utils.add_days.return_value = "2024-01-01"
            mock_frappe.utils.today.return_value = "2024-04-01"
            mock_frappe.get_all.return_value = []

            result = cleanup_processed_xmls()
            self.assertEqual(result, 0)

    def test_clears_xml_content_on_old_nfs(self):
        with patch("brazil_module.services.fiscal.processor.frappe") as mock_frappe:
            settings = MagicMock()
            settings.xml_retention_days = 90
            mock_frappe.get_single.return_value = settings
            mock_frappe.utils.add_days.return_value = "2024-01-01"
            mock_frappe.utils.today.return_value = "2024-04-01"
            mock_frappe.get_all.return_value = ["NF-001", "NF-002"]

            result = cleanup_processed_xmls()
            self.assertEqual(result, 2)
            self.assertEqual(mock_frappe.db.set_value.call_count, 2)
            mock_frappe.db.commit.assert_called_once()

    def test_default_retention_when_not_configured(self):
        with patch("brazil_module.services.fiscal.processor.frappe") as mock_frappe:
            settings = MagicMock(spec=[])  # No xml_retention_days attribute
            mock_frappe.get_single.return_value = settings
            mock_frappe.utils.add_days.return_value = "2024-01-01"
            mock_frappe.utils.today.return_value = "2024-04-01"
            mock_frappe.get_all.return_value = []

            result = cleanup_processed_xmls()
            # Should use default 90 days and call add_days with -90
            mock_frappe.utils.add_days.assert_called_once_with(
                mock_frappe.utils.today.return_value, -90
            )


class TestProcessPipeline(unittest.TestCase):
    def test_cancelled_document_raises(self):
        with patch("brazil_module.services.fiscal.processor.frappe") as mock_frappe:
            mock_frappe.get_single.return_value = MagicMock()
            mock_frappe.throw = MagicMock(side_effect=Exception("Cancelled"))
            processor = NFProcessor()

            nf = MagicMock()
            nf.cancelada = True
            nf.processing_status = "Cancelled"

            with self.assertRaises(Exception):
                processor.process(nf)

    def test_error_during_processing_sets_error_status(self):
        with patch("brazil_module.services.fiscal.processor.frappe") as mock_frappe:
            settings = MagicMock()
            settings.enable_po_matching = False
            settings.auto_create_invoice = False
            mock_frappe.get_single.return_value = settings

            processor = NFProcessor()

            nf = MagicMock()
            nf.cancelada = False
            nf.processing_status = ""
            nf.emitente_cnpj = "12345678000195"
            nf.document_type = "NF-e"

            # Make supplier processing raise
            with patch.object(processor, "_process_supplier", side_effect=Exception("DB error")):
                result = processor.process(nf)
                self.assertEqual(nf.processing_status, "Error")


if __name__ == "__main__":
    unittest.main()
