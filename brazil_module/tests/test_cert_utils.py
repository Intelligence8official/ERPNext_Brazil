"""Tests for certificate utilities (PFX/PKCS12 handling)."""

import unittest
from unittest.mock import MagicMock, patch, call
import sys
from datetime import datetime, timezone, timedelta

# Mock frappe and its submodules before importing the module under test
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils
# Always use the actual mock from sys.modules (may have been set by other test files)
frappe_mock = sys.modules["frappe"]

# Mock cryptography modules before importing
crypto_mock = MagicMock()
pkcs12_mock = MagicMock()
serialization_mock = MagicMock()
x509_mock = MagicMock()

sys.modules.setdefault("cryptography", crypto_mock)
sys.modules.setdefault("cryptography.hazmat", MagicMock())
sys.modules.setdefault("cryptography.hazmat.primitives", MagicMock())
sys.modules.setdefault("cryptography.hazmat.primitives.serialization", serialization_mock)
sys.modules.setdefault("cryptography.hazmat.primitives.serialization.pkcs12", pkcs12_mock)
sys.modules.setdefault("cryptography.x509", x509_mock)

from brazil_module.services.fiscal.cert_utils import (
    cleanup_temp_files,
    resolve_frappe_file_path,
    extract_cert_and_key_from_pfx_bytes,
    validate_pfx_certificate,
    get_certificate_info,
    CertificateContext,
)


def _make_mock_cert(not_before=None, not_after=None, common_name="Test Cert",
                    serial_number=123456789, issuer_cn="Test CA"):
    """Helper to create a mock certificate with configurable dates and attributes."""
    now = datetime.now(timezone.utc)
    if not_before is None:
        not_before = now - timedelta(days=365)
    if not_after is None:
        not_after = now + timedelta(days=365)

    cert = MagicMock()
    cert.not_valid_before_utc = not_before
    cert.not_valid_after_utc = not_after
    cert.serial_number = serial_number
    cert.public_bytes = MagicMock(return_value=b"-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n")

    # Build subject attributes
    cn_attr = MagicMock()
    cn_attr.oid._name = "commonName"
    cn_attr.value = common_name

    org_attr = MagicMock()
    org_attr.oid._name = "organizationName"
    org_attr.value = "Test Org"

    cert.subject = [cn_attr, org_attr]

    # Build issuer attributes
    issuer_cn_attr = MagicMock()
    issuer_cn_attr.oid._name = "commonName"
    issuer_cn_attr.value = issuer_cn

    cert.issuer = [issuer_cn_attr]

    return cert


def _make_mock_key():
    """Helper to create a mock private key with proper private_bytes method."""
    key = MagicMock()
    key.private_bytes = MagicMock(return_value=b"-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n")
    return key


class TestCleanupTempFiles(unittest.TestCase):
    """Tests for cleanup_temp_files function."""

    @patch("brazil_module.services.fiscal.cert_utils.os.unlink")
    @patch("brazil_module.services.fiscal.cert_utils.os.path.exists", return_value=True)
    def test_cleans_existing_files(self, mock_exists, mock_unlink):
        """Existing temp files should be deleted via os.unlink."""
        cleanup_temp_files("/tmp/cert.pem", "/tmp/key.pem")

        mock_exists.assert_any_call("/tmp/cert.pem")
        mock_exists.assert_any_call("/tmp/key.pem")
        mock_unlink.assert_any_call("/tmp/cert.pem")
        mock_unlink.assert_any_call("/tmp/key.pem")
        self.assertEqual(mock_unlink.call_count, 2)

    @patch("brazil_module.services.fiscal.cert_utils.os.unlink")
    @patch("brazil_module.services.fiscal.cert_utils.os.path.exists", return_value=False)
    def test_ignores_missing_files(self, mock_exists, mock_unlink):
        """Non-existent files should be silently skipped without calling unlink."""
        cleanup_temp_files("/tmp/gone.pem", None)

        mock_unlink.assert_not_called()

    @patch("brazil_module.services.fiscal.cert_utils.os.unlink", side_effect=OSError("Permission denied"))
    @patch("brazil_module.services.fiscal.cert_utils.os.path.exists", return_value=True)
    def test_handles_oserror_gracefully(self, mock_exists, mock_unlink):
        """OSError during unlink should be caught and silently ignored."""
        # Should not raise
        cleanup_temp_files("/tmp/locked.pem")
        mock_unlink.assert_called_once_with("/tmp/locked.pem")


class TestResolveFrappeFilePath(unittest.TestCase):
    """Tests for resolve_frappe_file_path function."""

    def tearDown(self):
        frappe_mock.get_doc.side_effect = None
        frappe_mock.get_doc.reset_mock()
        frappe_mock.get_site_path.side_effect = None
        frappe_mock.get_site_path.reset_mock()

    def test_empty_path_raises_value_error(self):
        """An empty string or None file_url should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            resolve_frappe_file_path("")
        self.assertIn("empty", str(ctx.exception).lower())

        with self.assertRaises(ValueError):
            resolve_frappe_file_path(None)

    @patch("brazil_module.services.fiscal.cert_utils.os.path.exists", return_value=True)
    @patch("brazil_module.services.fiscal.cert_utils.os.path.isabs", return_value=True)
    def test_absolute_path_returned_directly(self, mock_isabs, mock_exists):
        """An absolute path that exists should be returned as-is."""
        result = resolve_frappe_file_path("/opt/certs/my_cert.pfx")
        self.assertEqual(result, "/opt/certs/my_cert.pfx")

    @patch("brazil_module.services.fiscal.cert_utils.os.path.exists")
    @patch("brazil_module.services.fiscal.cert_utils.os.path.isabs", return_value=False)
    def test_files_prefix_resolves_via_site_path(self, mock_isabs, mock_exists):
        """A /files/ prefix URL should try frappe.get_site_path resolutions."""
        frappe_mock.get_site_path.reset_mock()
        frappe_mock.get_doc.side_effect = Exception("not found")

        # The first call to os.path.exists (for isabs path check) returns False,
        # then the site path resolution calls return True on the first match.
        mock_exists.side_effect = lambda p: p == "/site/public/files/cert.pfx"
        frappe_mock.get_site_path.side_effect = lambda *args: "/site/" + "/".join(args)

        result = resolve_frappe_file_path("/files/cert.pfx")
        self.assertEqual(result, "/site/public/files/cert.pfx")

    @patch("brazil_module.services.fiscal.cert_utils.os.path.exists")
    @patch("brazil_module.services.fiscal.cert_utils.os.path.isabs", return_value=False)
    def test_private_files_prefix_resolves_via_site_path(self, mock_isabs, mock_exists):
        """/private/files/ prefix should resolve via frappe.get_site_path."""
        frappe_mock.get_site_path.reset_mock()
        frappe_mock.get_doc.side_effect = Exception("not found")

        mock_exists.side_effect = lambda p: p == "/site/private/files/cert.pfx"
        frappe_mock.get_site_path.side_effect = lambda *args: "/site/" + "/".join(args)

        result = resolve_frappe_file_path("/private/files/cert.pfx")
        self.assertEqual(result, "/site/private/files/cert.pfx")


class TestExtractCertAndKeyFromPfxBytes(unittest.TestCase):
    """Tests for extract_cert_and_key_from_pfx_bytes function."""

    @patch("brazil_module.services.fiscal.cert_utils.tempfile.NamedTemporaryFile")
    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    def test_returns_cert_and_key_paths(self, mock_load, mock_tmpfile):
        """Should return a tuple of (cert_path, key_path) temp file paths."""
        mock_cert = _make_mock_cert()
        mock_key = _make_mock_key()
        mock_load.return_value = (mock_key, mock_cert, None)

        cert_file = MagicMock()
        cert_file.name = "/tmp/cert_abc.pem"
        key_file = MagicMock()
        key_file.name = "/tmp/key_abc.pem"
        mock_tmpfile.side_effect = [cert_file, key_file]

        cert_path, key_path = extract_cert_and_key_from_pfx_bytes(b"pfx_data", "password123")

        self.assertEqual(cert_path, "/tmp/cert_abc.pem")
        self.assertEqual(key_path, "/tmp/key_abc.pem")

    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    def test_no_cert_raises_value_error(self, mock_load):
        """If PFX has no certificate, ValueError should be raised."""
        mock_key = _make_mock_key()
        mock_load.return_value = (mock_key, None, None)

        with self.assertRaises(ValueError) as ctx:
            extract_cert_and_key_from_pfx_bytes(b"pfx_data", "password")
        self.assertIn("certificate", str(ctx.exception).lower())

    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    def test_no_key_raises_value_error(self, mock_load):
        """If PFX has no private key, ValueError should be raised."""
        mock_cert = _make_mock_cert()
        mock_load.return_value = (None, mock_cert, None)

        with self.assertRaises(ValueError) as ctx:
            extract_cert_and_key_from_pfx_bytes(b"pfx_data", "password")
        self.assertIn("private key", str(ctx.exception).lower())

    @patch("brazil_module.services.fiscal.cert_utils.tempfile.NamedTemporaryFile")
    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    def test_writes_pem_to_temp_files(self, mock_load, mock_tmpfile):
        """PEM-encoded certificate and key bytes should be written to temp files."""
        mock_cert = _make_mock_cert()
        mock_key = _make_mock_key()
        mock_load.return_value = (mock_key, mock_cert, None)

        cert_file = MagicMock()
        cert_file.name = "/tmp/cert_write.pem"
        key_file = MagicMock()
        key_file.name = "/tmp/key_write.pem"
        mock_tmpfile.side_effect = [cert_file, key_file]

        extract_cert_and_key_from_pfx_bytes(b"pfx_data", "mypass")

        # Verify PEM bytes were written
        cert_file.write.assert_called_once_with(mock_cert.public_bytes.return_value)
        key_file.write.assert_called_once_with(mock_key.private_bytes.return_value)

        # Verify files were closed
        cert_file.close.assert_called_once()
        key_file.close.assert_called_once()


class TestValidatePfxCertificate(unittest.TestCase):
    """Tests for validate_pfx_certificate function."""

    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    @patch("brazil_module.services.fiscal.cert_utils.get_pfx_bytes_from_file", return_value=b"pfx_data")
    def test_returns_expiry_date_string(self, mock_get_bytes, mock_load):
        """A valid certificate should return its expiry date as YYYY-MM-DD."""
        expiry = datetime(2027, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        not_before = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        mock_cert = _make_mock_cert(not_before=not_before, not_after=expiry)
        mock_key = _make_mock_key()
        mock_load.return_value = (mock_key, mock_cert, None)

        result = validate_pfx_certificate("/path/cert.pfx", "password")
        self.assertEqual(result, "2027-06-15")

    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    @patch("brazil_module.services.fiscal.cert_utils.get_pfx_bytes_from_file", return_value=b"pfx_data")
    def test_expired_cert_raises_value_error(self, mock_get_bytes, mock_load):
        """An expired certificate should raise ValueError with the expiry date."""
        expiry = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        not_before = datetime(2019, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mock_cert = _make_mock_cert(not_before=not_before, not_after=expiry)
        mock_key = _make_mock_key()
        mock_load.return_value = (mock_key, mock_cert, None)

        with self.assertRaises(ValueError) as ctx:
            validate_pfx_certificate("/path/cert.pfx", "password")
        self.assertIn("expired", str(ctx.exception).lower())
        self.assertIn("2020-01-01", str(ctx.exception))

    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    @patch("brazil_module.services.fiscal.cert_utils.get_pfx_bytes_from_file", return_value=b"pfx_data")
    def test_not_yet_valid_raises_value_error(self, mock_get_bytes, mock_load):
        """A certificate whose not_valid_before is in the future should raise ValueError."""
        not_before = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        not_after = datetime(2100, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mock_cert = _make_mock_cert(not_before=not_before, not_after=not_after)
        mock_key = _make_mock_key()
        mock_load.return_value = (mock_key, mock_cert, None)

        with self.assertRaises(ValueError) as ctx:
            validate_pfx_certificate("/path/cert.pfx", "password")
        self.assertIn("not valid until", str(ctx.exception).lower())
        self.assertIn("2099-01-01", str(ctx.exception))

    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    @patch("brazil_module.services.fiscal.cert_utils.get_pfx_bytes_from_file", return_value=b"pfx_data")
    def test_invalid_password_raises_value_error(self, mock_get_bytes, mock_load):
        """An invalid password should raise ValueError with descriptive message."""
        mock_load.side_effect = Exception("Could not deserialize key data: bad password or pkcs12 data")

        with self.assertRaises(ValueError) as ctx:
            validate_pfx_certificate("/path/cert.pfx", "wrong_pass")
        self.assertIn("invalid password", str(ctx.exception).lower())


class TestGetCertificateInfo(unittest.TestCase):
    """Tests for get_certificate_info function."""

    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    @patch("brazil_module.services.fiscal.cert_utils.get_pfx_bytes_from_file", return_value=b"pfx_data")
    def test_returns_complete_info_dict(self, mock_get_bytes, mock_load):
        """Should return a dict with all expected keys for a valid certificate."""
        not_before = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        not_after = datetime(2027, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        mock_cert = _make_mock_cert(
            not_before=not_before,
            not_after=not_after,
            common_name="Empresa Teste",
            serial_number=999888777,
        )
        mock_key = _make_mock_key()
        chain = [MagicMock(), MagicMock()]
        mock_load.return_value = (mock_key, mock_cert, chain)

        result = get_certificate_info("/path/cert.pfx", "password")

        self.assertEqual(result["common_name"], "Empresa Teste")
        self.assertEqual(result["serial_number"], "999888777")
        self.assertEqual(result["not_valid_before"], "2024-01-01 00:00:00")
        self.assertEqual(result["not_valid_after"], "2027-12-31 23:59:59")
        self.assertTrue(result["is_valid"])
        self.assertFalse(result["is_expired"])
        self.assertGreater(result["days_until_expiry"], 0)
        self.assertTrue(result["has_chain"])
        self.assertEqual(result["chain_length"], 2)
        self.assertIn("subject", result)
        self.assertIn("issuer", result)
        self.assertIsNone(result["cnpj_cpf"])

    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    @patch("brazil_module.services.fiscal.cert_utils.get_pfx_bytes_from_file", return_value=b"pfx_data")
    def test_expired_flags_set_correctly(self, mock_get_bytes, mock_load):
        """An expired cert should have is_expired=True, is_valid=False, days_until_expiry=0."""
        not_before = datetime(2019, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        not_after = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mock_cert = _make_mock_cert(not_before=not_before, not_after=not_after)
        mock_key = _make_mock_key()
        mock_load.return_value = (mock_key, mock_cert, None)

        result = get_certificate_info("/path/cert.pfx", "password")

        self.assertTrue(result["is_expired"])
        self.assertFalse(result["is_valid"])
        self.assertEqual(result["days_until_expiry"], 0)
        self.assertFalse(result["has_chain"])
        self.assertEqual(result["chain_length"], 0)

    @patch("brazil_module.services.fiscal.cert_utils.load_key_and_certificates")
    @patch("brazil_module.services.fiscal.cert_utils.get_pfx_bytes_from_file", return_value=b"pfx_data")
    def test_cnpj_extracted_from_common_name(self, mock_get_bytes, mock_load):
        """CNPJ should be extracted from common_name when format is 'Name:CNPJ'."""
        mock_cert = _make_mock_cert(common_name="Empresa XYZ Ltda:12345678000195")
        mock_key = _make_mock_key()
        mock_load.return_value = (mock_key, mock_cert, [])

        result = get_certificate_info("/path/cert.pfx", "password")

        self.assertEqual(result["cnpj_cpf"], "12345678000195")
        self.assertEqual(result["common_name"], "Empresa XYZ Ltda:12345678000195")


class TestCertificateContext(unittest.TestCase):
    """Tests for the CertificateContext context manager."""

    @patch("brazil_module.services.fiscal.cert_utils.cleanup_temp_files")
    @patch("brazil_module.services.fiscal.cert_utils.extract_cert_and_key_from_file")
    def test_returns_paths_and_cleans_up_on_exit(self, mock_extract, mock_cleanup):
        """Context manager should yield cert/key paths and clean up on normal exit."""
        mock_extract.return_value = ("/tmp/ctx_cert.pem", "/tmp/ctx_key.pem")

        with CertificateContext("/path/cert.pfx", "password") as (cert_path, key_path):
            self.assertEqual(cert_path, "/tmp/ctx_cert.pem")
            self.assertEqual(key_path, "/tmp/ctx_key.pem")

        # cleanup_temp_files should be called with both paths after exiting
        mock_cleanup.assert_called_once_with("/tmp/ctx_cert.pem", "/tmp/ctx_key.pem")

    @patch("brazil_module.services.fiscal.cert_utils.cleanup_temp_files")
    @patch("brazil_module.services.fiscal.cert_utils.extract_cert_and_key_from_file")
    def test_cleans_up_on_exception(self, mock_extract, mock_cleanup):
        """Temp files should be cleaned up even if an exception occurs inside the context."""
        mock_extract.return_value = ("/tmp/exc_cert.pem", "/tmp/exc_key.pem")

        with self.assertRaises(RuntimeError):
            with CertificateContext("/path/cert.pfx", "password") as (cert_path, key_path):
                raise RuntimeError("Something went wrong during request")

        # cleanup should still be called despite the exception
        mock_cleanup.assert_called_once_with("/tmp/exc_cert.pem", "/tmp/exc_key.pem")


if __name__ == "__main__":
    unittest.main()
