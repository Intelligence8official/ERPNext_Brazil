"""Tests for NF-e, CT-e, and NFS-e XML parser."""

import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

# Mock frappe before importing the parser
import sys
frappe_mock = MagicMock()
sys.modules.setdefault("frappe", frappe_mock)

from brazil.services.fiscal.xml_parser import NFXMLParser

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _read_fixture(filename):
    with open(os.path.join(FIXTURES_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()


class TestDocumentTypeDetection(unittest.TestCase):
    def test_detect_nfe(self):
        xml = _read_fixture("nfe_sample.xml")
        parser = NFXMLParser()
        result = parser.parse(xml)
        self.assertIsNotNone(result)
        self.assertEqual(result["document_type"], "NF-e")

    def test_detect_nfse_sped(self):
        xml = _read_fixture("nfse_sped_sample.xml")
        parser = NFXMLParser()
        result = parser.parse(xml)
        self.assertIsNotNone(result)
        self.assertEqual(result["document_type"], "NFS-e")

    def test_detect_nfse_abrasf(self):
        xml = _read_fixture("nfse_abrasf_sample.xml")
        parser = NFXMLParser()
        result = parser.parse(xml)
        self.assertIsNotNone(result)
        self.assertEqual(result["document_type"], "NFS-e")

    def test_detect_unknown(self):
        xml = '<?xml version="1.0"?><root><data>test</data></root>'
        parser = NFXMLParser()
        result = parser.parse(xml)
        self.assertIsNone(result)

    def test_invalid_xml(self):
        parser = NFXMLParser()
        result = parser.parse("<not valid xml><<<")
        self.assertIsNone(result)

    def test_empty_xml(self):
        parser = NFXMLParser()
        result = parser.parse("")
        self.assertIsNone(result)

    def test_none_xml(self):
        parser = NFXMLParser()
        result = parser.parse(None)
        self.assertIsNone(result)


class TestNFeParsingHeader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml = _read_fixture("nfe_sample.xml")
        parser = NFXMLParser()
        cls.data = parser.parse(xml)

    def test_document_type(self):
        self.assertEqual(self.data["document_type"], "NF-e")

    def test_chave_de_acesso(self):
        self.assertEqual(
            self.data["chave_de_acesso"],
            "35220612223333000155550010000000011000000019"
        )

    def test_numero(self):
        self.assertEqual(self.data["numero"], "1")

    def test_serie(self):
        self.assertEqual(self.data["serie"], "1")

    def test_data_emissao(self):
        self.assertEqual(self.data["data_emissao"], date(2022, 6, 15))


class TestNFeParsingEmitente(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml = _read_fixture("nfe_sample.xml")
        parser = NFXMLParser()
        cls.data = parser.parse(xml)

    def test_emitente_cnpj(self):
        self.assertEqual(self.data["emitente_cnpj"], "12223333000155")

    def test_emitente_razao_social(self):
        self.assertEqual(self.data["emitente_razao_social"], "Empresa Teste Ltda")

    def test_emitente_ie(self):
        self.assertEqual(self.data["emitente_ie"], "123456789")

    def test_emitente_uf(self):
        self.assertEqual(self.data["emitente_uf"], "SP")


class TestNFeParsingDestinatario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml = _read_fixture("nfe_sample.xml")
        parser = NFXMLParser()
        cls.data = parser.parse(xml)

    def test_tomador_cnpj(self):
        self.assertEqual(self.data["tomador_cnpj"], "98765432000100")

    def test_tomador_razao_social(self):
        self.assertEqual(self.data["tomador_razao_social"], "Destinatario Teste SA")


class TestNFeParsingTotals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml = _read_fixture("nfe_sample.xml")
        parser = NFXMLParser()
        cls.data = parser.parse(xml)

    def test_valor_total(self):
        self.assertEqual(self.data["valor_total"], 2020.00)

    def test_valor_produtos(self):
        self.assertEqual(self.data["valor_produtos"], 2000.00)

    def test_valor_frete(self):
        self.assertEqual(self.data["valor_frete"], 50.00)

    def test_valor_desconto(self):
        self.assertEqual(self.data["valor_desconto"], 30.00)


class TestNFeParsingTaxes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml = _read_fixture("nfe_sample.xml")
        parser = NFXMLParser()
        cls.data = parser.parse(xml)

    def test_valor_bc_icms(self):
        self.assertEqual(self.data["valor_bc_icms"], 2000.00)

    def test_valor_icms(self):
        self.assertEqual(self.data["valor_icms"], 360.00)

    def test_valor_ipi(self):
        self.assertEqual(self.data["valor_ipi"], 0.00)

    def test_valor_pis(self):
        self.assertEqual(self.data["valor_pis"], 13.00)

    def test_valor_cofins(self):
        self.assertEqual(self.data["valor_cofins"], 60.00)


class TestNFeParsingItems(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml = _read_fixture("nfe_sample.xml")
        parser = NFXMLParser()
        cls.data = parser.parse(xml)

    def test_item_count(self):
        self.assertEqual(len(self.data["items"]), 2)

    def test_first_item_numero(self):
        self.assertEqual(self.data["items"][0]["numero_item"], "1")

    def test_first_item_codigo(self):
        self.assertEqual(self.data["items"][0]["codigo_produto"], "PROD001")

    def test_first_item_descricao(self):
        self.assertEqual(self.data["items"][0]["descricao"], "Produto Teste A")

    def test_first_item_ncm(self):
        self.assertEqual(self.data["items"][0]["ncm"], "84719012")

    def test_first_item_cfop(self):
        self.assertEqual(self.data["items"][0]["cfop"], "5102")

    def test_first_item_unidade(self):
        self.assertEqual(self.data["items"][0]["unidade"], "UN")

    def test_first_item_quantidade(self):
        self.assertEqual(self.data["items"][0]["quantidade"], 10.0)

    def test_first_item_valor_unitario(self):
        self.assertEqual(self.data["items"][0]["valor_unitario"], 150.00)

    def test_first_item_valor_total(self):
        self.assertEqual(self.data["items"][0]["valor_total"], 1500.00)

    def test_first_item_icms_cst(self):
        self.assertEqual(self.data["items"][0]["icms_cst"], "00")

    def test_first_item_icms_aliquota(self):
        self.assertEqual(self.data["items"][0]["icms_aliquota"], 18.00)

    def test_first_item_icms_valor(self):
        self.assertEqual(self.data["items"][0]["icms_valor"], 270.00)

    def test_second_item_descricao(self):
        self.assertEqual(self.data["items"][1]["descricao"], "Produto Teste B")

    def test_second_item_unidade(self):
        self.assertEqual(self.data["items"][1]["unidade"], "CX")


class TestNFSeSpedParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml = _read_fixture("nfse_sped_sample.xml")
        parser = NFXMLParser()
        cls.data = parser.parse(xml)

    def test_document_type(self):
        self.assertEqual(self.data["document_type"], "NFS-e")

    def test_numero(self):
        self.assertEqual(self.data["numero"], "12345")

    def test_emitente_cnpj(self):
        self.assertEqual(self.data["emitente_cnpj"], "11222333000181")

    def test_emitente_razao_social(self):
        self.assertEqual(self.data["emitente_razao_social"], "Prestador Servico Ltda")

    def test_emitente_municipio(self):
        self.assertEqual(self.data["emitente_municipio"], "3550308")

    def test_tomador_cnpj(self):
        self.assertEqual(self.data["tomador_cnpj"], "98765432000100")

    def test_tomador_razao_social(self):
        self.assertEqual(self.data["tomador_razao_social"], "Tomador Servico SA")

    def test_valor_servicos(self):
        self.assertEqual(self.data["valor_servicos"], 5000.00)

    def test_valor_total_equals_servicos(self):
        self.assertEqual(self.data["valor_total"], self.data["valor_servicos"])

    def test_valor_issqn(self):
        self.assertEqual(self.data["valor_issqn"], 250.00)

    def test_aliquota_issqn(self):
        self.assertEqual(self.data["aliquota_issqn"], 5.00)

    def test_valor_liquido(self):
        self.assertEqual(self.data["valor_liquido"], 4750.00)

    def test_codigo_tributacao_nacional(self):
        self.assertEqual(self.data["codigo_tributacao_nacional"], "01.01")

    def test_descricao_servico(self):
        self.assertEqual(self.data["descricao_servico"], "Servico de Consultoria em TI")

    def test_single_item(self):
        self.assertEqual(len(self.data["items"]), 1)

    def test_item_descricao(self):
        self.assertEqual(self.data["items"][0]["descricao"], "Servico de Consultoria em TI")

    def test_item_valor(self):
        self.assertEqual(self.data["items"][0]["valor_total"], 5000.00)

    def test_tributos_federais(self):
        self.assertEqual(self.data["valor_total_tributos_federais"], 325.00)

    def test_tributos_municipais(self):
        self.assertEqual(self.data["valor_total_tributos_municipais"], 250.00)


class TestNFSeAbrasfParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml = _read_fixture("nfse_abrasf_sample.xml")
        parser = NFXMLParser()
        cls.data = parser.parse(xml)

    def test_document_type(self):
        self.assertEqual(self.data["document_type"], "NFS-e")

    def test_numero(self):
        self.assertEqual(self.data["numero"], "54321")

    def test_emitente_cnpj(self):
        self.assertEqual(self.data["emitente_cnpj"], "11222333000181")

    def test_emitente_razao_social(self):
        self.assertEqual(self.data["emitente_razao_social"], "Empresa Prestadora ABRASF Ltda")

    def test_tomador_cnpj(self):
        self.assertEqual(self.data["tomador_cnpj"], "44556677000188")

    def test_tomador_razao_social(self):
        self.assertEqual(self.data["tomador_razao_social"], "Tomador ABRASF SA")

    def test_valor_servicos(self):
        self.assertEqual(self.data["valor_servicos"], 3500.00)

    def test_valor_issqn(self):
        self.assertEqual(self.data["valor_issqn"], 175.00)

    def test_aliquota_issqn(self):
        self.assertEqual(self.data["aliquota_issqn"], 5.00)

    def test_descricao_servico(self):
        self.assertEqual(self.data["descricao_servico"], "Servico de Manutencao de Software")

    def test_single_item(self):
        self.assertEqual(len(self.data["items"]), 1)

    def test_item_codigo(self):
        self.assertEqual(self.data["items"][0]["codigo_produto"], "14.01")


class TestParseCurrency(unittest.TestCase):
    def setUp(self):
        self.parser = NFXMLParser()

    def test_xml_format(self):
        self.assertEqual(self.parser._parse_currency("16800.00"), 16800.00)

    def test_brazilian_format_with_thousands(self):
        self.assertEqual(self.parser._parse_currency("16.800,00"), 16800.00)

    def test_brazilian_format_no_thousands(self):
        self.assertEqual(self.parser._parse_currency("1500,50"), 1500.50)

    def test_integer_string(self):
        self.assertEqual(self.parser._parse_currency("100"), 100.0)

    def test_empty(self):
        self.assertEqual(self.parser._parse_currency(""), 0.0)

    def test_none(self):
        self.assertEqual(self.parser._parse_currency(None), 0.0)

    def test_with_whitespace(self):
        self.assertEqual(self.parser._parse_currency("  1500.00  "), 1500.00)

    def test_invalid(self):
        self.assertEqual(self.parser._parse_currency("abc"), 0.0)

    def test_zero(self):
        self.assertEqual(self.parser._parse_currency("0.00"), 0.0)


class TestParseFloat(unittest.TestCase):
    def setUp(self):
        self.parser = NFXMLParser()

    def test_dot_decimal(self):
        self.assertEqual(self.parser._parse_float("12.5"), 12.5)

    def test_comma_decimal(self):
        self.assertEqual(self.parser._parse_float("12,5"), 12.5)

    def test_integer(self):
        self.assertEqual(self.parser._parse_float("100"), 100.0)

    def test_empty(self):
        self.assertEqual(self.parser._parse_float(""), 0.0)

    def test_none(self):
        self.assertEqual(self.parser._parse_float(None), 0.0)

    def test_invalid(self):
        self.assertEqual(self.parser._parse_float("abc"), 0.0)


class TestParseDate(unittest.TestCase):
    def setUp(self):
        self.parser = NFXMLParser()

    def test_iso_with_timezone(self):
        result = self.parser._parse_date("2022-06-15T10:30:00-03:00")
        self.assertEqual(result, date(2022, 6, 15))

    def test_iso_without_timezone(self):
        result = self.parser._parse_date("2022-06-15T10:30:00")
        self.assertEqual(result, date(2022, 6, 15))

    def test_date_only(self):
        result = self.parser._parse_date("2022-06-15")
        self.assertEqual(result, date(2022, 6, 15))

    def test_empty(self):
        self.assertIsNone(self.parser._parse_date(""))

    def test_none(self):
        self.assertIsNone(self.parser._parse_date(None))

    def test_invalid(self):
        self.assertIsNone(self.parser._parse_date("not-a-date"))


class TestCTeParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml = _read_fixture("cte_sample.xml")
        parser = NFXMLParser()
        cls.data = parser.parse(xml)

    def test_document_type(self):
        self.assertEqual(self.data["document_type"], "CT-e")

    def test_chave_de_acesso(self):
        self.assertEqual(
            self.data["chave_de_acesso"],
            "35220698765432000100570010000005001000000005"
        )

    def test_numero(self):
        self.assertEqual(self.data["numero"], "500")

    def test_serie(self):
        self.assertEqual(self.data["serie"], "1")

    def test_data_emissao(self):
        self.assertEqual(self.data["data_emissao"], date(2022, 6, 20))

    def test_cfop(self):
        self.assertEqual(self.data["cfop"], "6353")

    def test_natureza_operacao(self):
        self.assertEqual(self.data["natureza_operacao"], "Prestacao de Servico de Transporte")

    def test_modal(self):
        self.assertEqual(self.data["modal"], "01")

    def test_emitente_cnpj(self):
        self.assertEqual(self.data["emitente_cnpj"], "98765432000100")

    def test_emitente_razao_social(self):
        self.assertEqual(self.data["emitente_razao_social"], "Transportadora Exemplo Ltda")

    def test_emitente_ie(self):
        self.assertEqual(self.data["emitente_ie"], "111222333")

    def test_emitente_uf(self):
        self.assertEqual(self.data["emitente_uf"], "SP")

    def test_remetente_cnpj(self):
        self.assertEqual(self.data["remetente_cnpj"], "12223333000155")

    def test_remetente_razao_social(self):
        self.assertEqual(self.data["remetente_razao_social"], "Remetente Industria SA")

    def test_tomador_cnpj(self):
        self.assertEqual(self.data["tomador_cnpj"], "44556677000188")

    def test_tomador_razao_social(self):
        self.assertEqual(self.data["tomador_razao_social"], "Destinatario Comercio Ltda")

    def test_tomador_tipo(self):
        self.assertEqual(self.data["tomador_tipo"], "3")

    def test_valor_total(self):
        self.assertEqual(self.data["valor_total"], 1500.00)

    def test_valor_receber(self):
        self.assertEqual(self.data["valor_receber"], 1500.00)

    def test_valor_icms(self):
        self.assertEqual(self.data["valor_icms"], 180.00)

    def test_valor_bc_icms(self):
        self.assertEqual(self.data["valor_bc_icms"], 1500.00)

    def test_icms_aliquota(self):
        self.assertEqual(self.data["icms_aliquota"], 12.00)

    def test_valor_carga(self):
        self.assertEqual(self.data["valor_carga"], 50000.00)

    def test_produto_predominante(self):
        self.assertEqual(self.data["produto_predominante"], "Pecas Automotivas")

    def test_single_item(self):
        self.assertEqual(len(self.data["items"]), 1)

    def test_item_descricao(self):
        self.assertEqual(self.data["items"][0]["descricao"], "Frete - CT-e 500")

    def test_item_valor(self):
        self.assertEqual(self.data["items"][0]["valor_total"], 1500.00)


if __name__ == "__main__":
    unittest.main()
