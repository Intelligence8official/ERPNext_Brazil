"""
Formatting utilities for Brazilian banking data.
"""

import re


def format_cnpj(cnpj: str) -> str:
    """Format a 14-digit CNPJ with punctuation.

    Example: 12345678000195 -> 12.345.678/0001-95
    """
    cnpj = re.sub(r"[^\d]", "", cnpj)
    if len(cnpj) != 14:
        return cnpj
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def format_cpf(cpf: str) -> str:
    """Format an 11-digit CPF with punctuation.

    Example: 12345678901 -> 123.456.789-01
    """
    cpf = re.sub(r"[^\d]", "", cpf)
    if len(cpf) != 11:
        return cpf
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def clean_cpf_cnpj(value: str) -> str:
    """Remove all non-digit characters from CPF/CNPJ."""
    return re.sub(r"[^\d]", "", value)


def format_currency_brl(value: float) -> str:
    """Format value as Brazilian Real.

    Example: 1234.56 -> R$ 1.234,56
    """
    formatted = f"{value:,.2f}"
    # Swap . and , for Brazilian format
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def format_phone_br(phone: str) -> str:
    """Format a Brazilian phone number.

    Example: 11999887766 -> (11) 99988-7766
    """
    phone = re.sub(r"[^\d]", "", phone)
    if len(phone) == 11:
        return f"({phone[:2]}) {phone[2:7]}-{phone[7:]}"
    elif len(phone) == 10:
        return f"({phone[:2]}) {phone[2:6]}-{phone[6:]}"
    return phone
