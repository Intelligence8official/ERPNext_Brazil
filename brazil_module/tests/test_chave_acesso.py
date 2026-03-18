"""Tests for Chave de Acesso (Access Key) parsing and validation utilities."""

import unittest

from brazil_module.utils.chave_acesso import (
    clean_chave,
    extract_info_from_chave,
    format_chave_acesso,
    get_document_type_from_modelo,
    get_tipo_emissao_name,
    get_uf_name,
    parse_chave_acesso,
    validate_chave_acesso,
)


def _make_valid_chave(base_43_digits="3522061222333300015500100000000011000000001"):
    """Generate a valid 44-digit chave with correct check digit (mod-11)."""
    chave = base_43_digits
    assert len(chave) == 43, f"Base must be 43 digits, got {len(chave)}"

    weights = [2, 3, 4, 5, 6, 7, 8, 9]
    total = 0
    for i in range(42, -1, -1):
        weight_idx = (42 - i) % 8
        total += int(chave[i]) * weights[weight_idx]

    remainder = total % 11
    dv = 0 if remainder < 2 else 11 - remainder
    return chave + str(dv)


# Pre-compute some valid keys
# Structure: UF(2) AAMM(4) CNPJ(14) Modelo(2) Serie(3) Numero(9) TipoEmissao(1) Codigo(8)
# SP(35) 2206 12223333000155 55 001 000000001 1 00000000
VALID_NFE_CHAVE = _make_valid_chave("3522061222333300015555001000000001100000000")
# CT-e: modelo=57
VALID_CTE_CHAVE = _make_valid_chave("3522061222333300015557001000000001100000000")


class TestCleanChave(unittest.TestCase):
    def test_clean_with_spaces(self):
        spaced = " ".join([VALID_NFE_CHAVE[i:i+4] for i in range(0, 44, 4)])
        self.assertEqual(clean_chave(spaced), VALID_NFE_CHAVE)

    def test_clean_already_clean(self):
        self.assertEqual(clean_chave(VALID_NFE_CHAVE), VALID_NFE_CHAVE)

    def test_clean_with_dashes(self):
        dashed = VALID_NFE_CHAVE[:22] + "-" + VALID_NFE_CHAVE[22:]
        self.assertEqual(clean_chave(dashed), VALID_NFE_CHAVE)

    def test_clean_empty(self):
        self.assertEqual(clean_chave(""), "")

    def test_clean_none(self):
        self.assertEqual(clean_chave(None), "")

    def test_clean_non_digit_chars(self):
        self.assertEqual(clean_chave("abc123def456"), "123456")


class TestParseChaveAcesso(unittest.TestCase):
    def test_parse_valid_44(self):
        result = parse_chave_acesso(VALID_NFE_CHAVE)
        self.assertIsNotNone(result)
        self.assertEqual(result["uf"], VALID_NFE_CHAVE[0:2])
        self.assertEqual(result["ano_mes"], VALID_NFE_CHAVE[2:6])
        self.assertEqual(result["cnpj"], VALID_NFE_CHAVE[6:20])
        self.assertEqual(result["modelo"], VALID_NFE_CHAVE[20:22])
        self.assertEqual(result["serie"], VALID_NFE_CHAVE[22:25])
        self.assertEqual(result["numero"], VALID_NFE_CHAVE[25:34])
        self.assertEqual(result["tipo_emissao"], VALID_NFE_CHAVE[34:35])
        self.assertEqual(result["codigo"], VALID_NFE_CHAVE[35:43])
        self.assertEqual(result["dv"], VALID_NFE_CHAVE[43:44])

    def test_parse_43_digits(self):
        self.assertIsNone(parse_chave_acesso("1234567890123456789012345678901234567890123"))

    def test_parse_45_digits(self):
        self.assertIsNone(parse_chave_acesso("12345678901234567890123456789012345678901234" + "5"))

    def test_parse_empty(self):
        self.assertIsNone(parse_chave_acesso(""))

    def test_parse_none(self):
        self.assertIsNone(parse_chave_acesso(None))

    def test_parse_with_formatting(self):
        # With spaces
        spaced = " ".join([VALID_NFE_CHAVE[i:i+4] for i in range(0, 44, 4)])
        result = parse_chave_acesso(spaced)
        self.assertIsNotNone(result)
        self.assertEqual(result["uf"], VALID_NFE_CHAVE[0:2])

    def test_parse_specific_fields(self):
        # Use the known key: UF=35 (SP), AAMM=2206, CNPJ=12223333000155
        result = parse_chave_acesso(VALID_NFE_CHAVE)
        self.assertEqual(result["uf"], "35")
        self.assertEqual(result["ano_mes"], "2206")


class TestValidateChaveAcesso(unittest.TestCase):
    def test_valid_nfe_key(self):
        self.assertTrue(validate_chave_acesso(VALID_NFE_CHAVE))

    def test_invalid_check_digit(self):
        # Change last digit
        bad_dv = VALID_NFE_CHAVE[:43] + str((int(VALID_NFE_CHAVE[43]) + 1) % 10)
        self.assertFalse(validate_chave_acesso(bad_dv))

    def test_valid_nfse_50_digit(self):
        # NFS-e with 50 digits - format-only check
        nfse_key = "0" * 50
        self.assertTrue(validate_chave_acesso(nfse_key))

    def test_enforce_nfe_type_with_44_digit(self):
        self.assertTrue(validate_chave_acesso(VALID_NFE_CHAVE, document_type="NF-e"))

    def test_enforce_nfe_type_with_50_digit(self):
        nfse_key = "0" * 50
        self.assertFalse(validate_chave_acesso(nfse_key, document_type="NF-e"))

    def test_enforce_nfse_type_with_44_digit(self):
        self.assertFalse(validate_chave_acesso(VALID_NFE_CHAVE, document_type="NFS-e"))

    def test_enforce_nfse_type_with_50_digit(self):
        nfse_key = "0" * 50
        self.assertTrue(validate_chave_acesso(nfse_key, document_type="NFS-e"))

    def test_enforce_cte_type(self):
        self.assertTrue(validate_chave_acesso(VALID_NFE_CHAVE, document_type="CT-e"))

    def test_enforce_nfce_type(self):
        self.assertTrue(validate_chave_acesso(VALID_NFE_CHAVE, document_type="NFC-e"))

    def test_empty(self):
        self.assertFalse(validate_chave_acesso(""))

    def test_none(self):
        self.assertFalse(validate_chave_acesso(None))

    def test_wrong_length_30(self):
        self.assertFalse(validate_chave_acesso("0" * 30))

    def test_with_spaces(self):
        spaced = " ".join([VALID_NFE_CHAVE[i:i+4] for i in range(0, 44, 4)])
        self.assertTrue(validate_chave_acesso(spaced))

    def test_dv_zero_when_remainder_0(self):
        # Build a key where remainder is 0, so DV should be 0
        # We try different codigo values until we get remainder 0
        for code_num in range(100):
            code = str(code_num).zfill(8)
            base = "35220612223333000155001000000001" + "1" + code
            if len(base) == 43:
                weights = [2, 3, 4, 5, 6, 7, 8, 9]
                total = sum(int(base[i]) * weights[(42 - i) % 8] for i in range(42, -1, -1))
                if total % 11 == 0:
                    chave = base + "0"
                    self.assertTrue(validate_chave_acesso(chave))
                    return
        # If no remainder=0 found in range, skip (unlikely)

    def test_dv_zero_when_remainder_1(self):
        # Build a key where remainder is 1, DV should also be 0
        for code_num in range(100):
            code = str(code_num).zfill(8)
            base = "35220612223333000155001000000001" + "1" + code
            if len(base) == 43:
                weights = [2, 3, 4, 5, 6, 7, 8, 9]
                total = sum(int(base[i]) * weights[(42 - i) % 8] for i in range(42, -1, -1))
                if total % 11 == 1:
                    chave = base + "0"
                    self.assertTrue(validate_chave_acesso(chave))
                    return


class TestFormatChaveAcesso(unittest.TestCase):
    def test_format_44_digits(self):
        result = format_chave_acesso(VALID_NFE_CHAVE)
        groups = result.split(" ")
        self.assertEqual(len(groups), 11)
        for g in groups:
            self.assertEqual(len(g), 4)

    def test_format_wrong_length(self):
        self.assertEqual(format_chave_acesso("12345"), "12345")

    def test_format_empty(self):
        self.assertEqual(format_chave_acesso(""), "")

    def test_format_roundtrip(self):
        formatted = format_chave_acesso(VALID_NFE_CHAVE)
        cleaned = clean_chave(formatted)
        self.assertEqual(cleaned, VALID_NFE_CHAVE)


class TestGetDocumentTypeFromModelo(unittest.TestCase):
    def test_nfe(self):
        self.assertEqual(get_document_type_from_modelo("55"), "NF-e")

    def test_cte(self):
        self.assertEqual(get_document_type_from_modelo("57"), "CT-e")

    def test_nfce(self):
        self.assertEqual(get_document_type_from_modelo("65"), "NFC-e")

    def test_cte_os(self):
        self.assertEqual(get_document_type_from_modelo("67"), "CT-e OS")

    def test_mdfe(self):
        self.assertEqual(get_document_type_from_modelo("58"), "MDF-e")

    def test_nfse(self):
        self.assertEqual(get_document_type_from_modelo("99"), "NFS-e")

    def test_unknown(self):
        self.assertEqual(get_document_type_from_modelo("00"), "Unknown")

    def test_empty(self):
        self.assertEqual(get_document_type_from_modelo(""), "Unknown")


class TestGetUfName(unittest.TestCase):
    def test_sp(self):
        self.assertEqual(get_uf_name("35"), "SP")

    def test_rj(self):
        self.assertEqual(get_uf_name("33"), "RJ")

    def test_mg(self):
        self.assertEqual(get_uf_name("31"), "MG")

    def test_df(self):
        self.assertEqual(get_uf_name("53"), "DF")

    def test_am(self):
        self.assertEqual(get_uf_name("13"), "AM")

    def test_unknown(self):
        self.assertEqual(get_uf_name("99"), "XX")

    def test_empty(self):
        self.assertEqual(get_uf_name(""), "XX")

    def test_all_states_covered(self):
        expected_codes = [
            "11", "12", "13", "14", "15", "16", "17",
            "21", "22", "23", "24", "25", "26", "27", "28", "29",
            "31", "32", "33", "35",
            "41", "42", "43",
            "50", "51", "52", "53"
        ]
        for code in expected_codes:
            result = get_uf_name(code)
            self.assertNotEqual(result, "XX", f"UF code {code} returned XX")


class TestGetTipoEmissaoName(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(get_tipo_emissao_name("1"), "Normal")

    def test_contingencia_fs_ia(self):
        self.assertEqual(get_tipo_emissao_name("2"), "Contingencia FS-IA")

    def test_contingencia_svc_an(self):
        self.assertEqual(get_tipo_emissao_name("6"), "Contingencia SVC-AN")

    def test_contingencia_svc_rs(self):
        self.assertEqual(get_tipo_emissao_name("7"), "Contingencia SVC-RS")

    def test_contingencia_offline(self):
        self.assertEqual(get_tipo_emissao_name("9"), "Contingencia Offline NFC-e")

    def test_unknown(self):
        self.assertEqual(get_tipo_emissao_name("0"), "Unknown")

    def test_empty(self):
        self.assertEqual(get_tipo_emissao_name(""), "Unknown")


class TestExtractInfoFromChave(unittest.TestCase):
    def test_full_extraction(self):
        result = extract_info_from_chave(VALID_NFE_CHAVE)
        self.assertIsNotNone(result)
        self.assertEqual(result["estado"], "SP")
        self.assertEqual(result["ano"], "2022")
        self.assertEqual(result["mes"], "06")
        self.assertIn(".", result["cnpj_formatado"])
        self.assertEqual(result["tipo_documento"], "NF-e")
        self.assertEqual(result["tipo_emissao"], "Normal")
        self.assertTrue(result["valido"])

    def test_invalid_chave(self):
        self.assertIsNone(extract_info_from_chave("12345"))

    def test_empty(self):
        self.assertIsNone(extract_info_from_chave(""))

    def test_none(self):
        self.assertIsNone(extract_info_from_chave(None))

    def test_serie_lstrip(self):
        result = extract_info_from_chave(VALID_NFE_CHAVE)
        # Serie "001" should become "1"
        self.assertEqual(result["serie"], "1")

    def test_numero_lstrip(self):
        result = extract_info_from_chave(VALID_NFE_CHAVE)
        # Numero with leading zeros stripped
        self.assertFalse(result["numero"].startswith("0") and len(result["numero"]) > 1)

    def test_digito_verificador(self):
        result = extract_info_from_chave(VALID_NFE_CHAVE)
        self.assertEqual(result["digito_verificador"], VALID_NFE_CHAVE[43])


if __name__ == "__main__":
    unittest.main()
