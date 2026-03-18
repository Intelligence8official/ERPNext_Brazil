"""Tests for SEFAZ DF-e client functions."""

import base64
import gzip
import unittest
from unittest.mock import MagicMock, patch
import sys
import xml.etree.ElementTree as ET

# Mock frappe and its submodules before importing
frappe_mock = MagicMock()
frappe_mock._ = lambda x: x
frappe_mock.utils.now_datetime = MagicMock(return_value="2024-01-01 12:00:00")
frappe_mock.utils.get_datetime = MagicMock(side_effect=lambda x: x)
sys.modules.setdefault("frappe", frappe_mock)
sys.modules.setdefault("frappe.utils", frappe_mock.utils)

# Mock cert_utils
cert_mock = MagicMock()
sys.modules.setdefault("brazil_module.services.fiscal.cert_utils", cert_mock)

from brazil_module.services.fiscal.dfe_client import (
    _build_dist_dfe_request,
    _decode_xml,
    _extract_chave_from_xml,
    _parse_dist_dfe_response,
)


class TestBuildDistDfeRequest(unittest.TestCase):
    def test_valid_soap_envelope(self):
        result = _build_dist_dfe_request(
            "1", "35", "12345678000195", "000000000000001",
            "http://www.portalfiscal.inf.br/nfe"
        )
        # Should be valid XML
        root = ET.fromstring(result)
        self.assertIn("Envelope", root.tag)

    def test_contains_tpAmb(self):
        result = _build_dist_dfe_request(
            "2", "35", "12345678000195", "000000000000001",
            "http://www.portalfiscal.inf.br/nfe"
        )
        self.assertIn("<tpAmb>2</tpAmb>", result)

    def test_contains_cUFAutor(self):
        result = _build_dist_dfe_request(
            "1", "33", "12345678000195", "000000000000001",
            "http://www.portalfiscal.inf.br/nfe"
        )
        self.assertIn("<cUFAutor>33</cUFAutor>", result)

    def test_contains_cnpj(self):
        result = _build_dist_dfe_request(
            "1", "35", "12345678000195", "000000000000001",
            "http://www.portalfiscal.inf.br/nfe"
        )
        self.assertIn("<CNPJ>12345678000195</CNPJ>", result)

    def test_contains_ultNSU(self):
        result = _build_dist_dfe_request(
            "1", "35", "12345678000195", "000000000000123",
            "http://www.portalfiscal.inf.br/nfe"
        )
        self.assertIn("<ultNSU>000000000000123</ultNSU>", result)

    def test_nfe_namespace(self):
        result = _build_dist_dfe_request(
            "1", "35", "12345678000195", "000000000000001",
            "http://www.portalfiscal.inf.br/nfe"
        )
        self.assertIn("portalfiscal.inf.br/nfe", result)

    def test_cte_namespace(self):
        result = _build_dist_dfe_request(
            "1", "35", "12345678000195", "000000000000001",
            "http://www.portalfiscal.inf.br/cte"
        )
        self.assertIn("portalfiscal.inf.br/cte", result)

    def test_soap12_namespace(self):
        result = _build_dist_dfe_request(
            "1", "35", "12345678000195", "000000000000001",
            "http://www.portalfiscal.inf.br/nfe"
        )
        self.assertIn("w3.org/2003/05/soap-envelope", result)


class TestParseDistDfeResponse(unittest.TestCase):
    def _make_response(self, cStat, xMotivo, documents=None, maxNSU="0", ultNSU="0"):
        """Build a sample SOAP response."""
        docs_xml = ""
        if documents:
            doc_entries = ""
            for doc in documents:
                nsu = doc.get("NSU", "")
                schema = doc.get("schema", "")
                xml_b64 = doc.get("xml_b64", "")
                doc_entries += f'<docZip NSU="{nsu}" schema="{schema}">{xml_b64}</docZip>'
            docs_xml = f"<loteDistDFeInt>{doc_entries}</loteDistDFeInt>"

        ns = "http://www.portalfiscal.inf.br/nfe"
        return (
            f'<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
            f'<soap12:Body>'
            f'<nfeDistDFeInteresseResponse xmlns="{ns}">'
            f'<retDistDFeInt xmlns="{ns}">'
            f'<cStat>{cStat}</cStat>'
            f'<xMotivo>{xMotivo}</xMotivo>'
            f'<maxNSU>{maxNSU}</maxNSU>'
            f'<ultNSU>{ultNSU}</ultNSU>'
            f'{docs_xml}'
            f'</retDistDFeInt>'
            f'</nfeDistDFeInteresseResponse>'
            f'</soap12:Body>'
            f'</soap12:Envelope>'
        ).encode("utf-8")

    def _make_gzipped_b64(self, xml_str):
        """Compress XML string to gzip+base64."""
        compressed = gzip.compress(xml_str.encode("utf-8"))
        return base64.b64encode(compressed).decode("utf-8")

    def test_parse_success_with_docs(self):
        nfe_xml = '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe123"></infNFe></NFe></nfeProc>'
        xml_b64 = self._make_gzipped_b64(nfe_xml)
        response = self._make_response(
            "137", "Lote com documentos",
            documents=[{"NSU": "100", "schema": "procNFe_v4.00.xsd", "xml_b64": xml_b64}],
            maxNSU="500", ultNSU="100"
        )
        result = _parse_dist_dfe_response(response, "http://www.portalfiscal.inf.br/nfe")
        self.assertEqual(result["cStat"], "137")
        self.assertEqual(result["xMotivo"], "Lote com documentos")
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(result["documents"][0]["NSU"], "100")
        self.assertEqual(result["maxNSU"], "500")
        self.assertEqual(result["ultNSU"], "100")

    def test_parse_no_docs(self):
        response = self._make_response("138", "Nao ha documentos novos")
        result = _parse_dist_dfe_response(response, "http://www.portalfiscal.inf.br/nfe")
        self.assertEqual(result["cStat"], "138")
        self.assertEqual(len(result["documents"]), 0)

    def test_parse_error_response(self):
        response = self._make_response("656", "Certificado invalido")
        result = _parse_dist_dfe_response(response, "http://www.portalfiscal.inf.br/nfe")
        self.assertEqual(result["cStat"], "656")
        self.assertEqual(result["xMotivo"], "Certificado invalido")

    def test_parse_invalid_xml(self):
        result = _parse_dist_dfe_response(b"not xml", "http://www.portalfiscal.inf.br/nfe")
        self.assertEqual(result["cStat"], "999")
        self.assertIn("parse error", result["xMotivo"].lower())

    def test_parse_missing_ret_element(self):
        response = b'<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"><soap12:Body><other/></soap12:Body></soap12:Envelope>'
        result = _parse_dist_dfe_response(response, "http://www.portalfiscal.inf.br/nfe")
        self.assertEqual(result["cStat"], "999")

    def test_multiple_documents(self):
        nfe1 = '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe111"></infNFe></NFe></nfeProc>'
        nfe2 = '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe222"></infNFe></NFe></nfeProc>'
        response = self._make_response(
            "137", "OK",
            documents=[
                {"NSU": "10", "schema": "procNFe_v4.00.xsd", "xml_b64": self._make_gzipped_b64(nfe1)},
                {"NSU": "11", "schema": "procNFe_v4.00.xsd", "xml_b64": self._make_gzipped_b64(nfe2)},
            ]
        )
        result = _parse_dist_dfe_response(response, "http://www.portalfiscal.inf.br/nfe")
        self.assertEqual(len(result["documents"]), 2)


class TestDecodeXml(unittest.TestCase):
    def test_decode_gzipped(self):
        xml = "<root><data>test</data></root>"
        compressed = gzip.compress(xml.encode("utf-8"))
        encoded = base64.b64encode(compressed).decode("utf-8")
        result = _decode_xml(encoded)
        self.assertEqual(result, xml)

    def test_decode_plain_base64(self):
        xml = "<root><data>test</data></root>"
        encoded = base64.b64encode(xml.encode("utf-8")).decode("utf-8")
        result = _decode_xml(encoded)
        self.assertEqual(result, xml)

    def test_decode_none(self):
        self.assertIsNone(_decode_xml(None))

    def test_decode_empty(self):
        self.assertIsNone(_decode_xml(""))


class TestExtractChaveFromXml(unittest.TestCase):
    def test_extract_nfe_chave(self):
        xml = '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe35220612345678000155550010000000011000000019"></infNFe></NFe></nfeProc>'
        result = _extract_chave_from_xml(xml)
        self.assertEqual(result, "35220612345678000155550010000000011000000019")

    def test_extract_cte_chave(self):
        xml = '<cteProc xmlns="http://www.portalfiscal.inf.br/cte"><CTe><infCte Id="CTe35220698765432000100570010000005001000000005"></infCte></CTe></cteProc>'
        result = _extract_chave_from_xml(xml)
        self.assertEqual(result, "35220698765432000100570010000005001000000005")

    def test_extract_none_for_no_id(self):
        xml = "<root><data>no id here</data></root>"
        self.assertIsNone(_extract_chave_from_xml(xml))

    def test_extract_none_for_empty(self):
        self.assertIsNone(_extract_chave_from_xml(None))
        self.assertIsNone(_extract_chave_from_xml(""))

    def test_extract_invalid_xml(self):
        self.assertIsNone(_extract_chave_from_xml("<not valid<<<"))


if __name__ == "__main__":
    unittest.main()
