"""
PIX charge service.

Handles standalone PIX charge creation (immediate and scheduled),
QR code generation, status polling, and payment registration.
"""

import uuid
from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import now_datetime, flt, today


def create_pix_charge_from_invoice(
    sales_invoice_name: str,
    expiration_seconds: int | None = None,
) -> str:
    """Create an immediate PIX charge from a Sales Invoice.

    Args:
        sales_invoice_name: Name of the Sales Invoice.
        expiration_seconds: QR code validity in seconds.

    Returns:
        Name of the created Inter PIX Charge document.
    """
    from Brazil_Module.services.banking.inter_client import InterAPIClient

    invoice = frappe.get_doc("Sales Invoice", sales_invoice_name)

    if invoice.docstatus != 1:
        frappe.throw(_("Sales Invoice must be submitted"))
    if invoice.outstanding_amount <= 0:
        frappe.throw(_("Sales Invoice has no outstanding amount"))

    settings = frappe.get_single("Banco Inter Settings")

    account_name = frappe.db.get_value(
        "Inter Company Account",
        {"company": invoice.company, "sync_enabled": 1},
        "name",
    )
    if not account_name:
        frappe.throw(_("No Inter Company Account found for company {0}").format(invoice.company))

    if not expiration_seconds:
        expiration_seconds = settings.pix_expiration_seconds or 3600

    # Generate unique txid (max 35 chars, alphanumeric)
    txid = uuid.uuid4().hex[:35]

    # Get customer info
    customer = frappe.get_doc("Customer", invoice.customer)

    charge_data = {
        "calendario": {
            "expiracao": expiration_seconds,
        },
        "valor": {
            "original": f"{flt(invoice.outstanding_amount, 2):.2f}",
        },
        "solicitacaoPagador": f"Fatura {sales_invoice_name}",
    }

    # Add payer info if available
    if customer.tax_id:
        cpf_cnpj = customer.tax_id.replace(".", "").replace("/", "").replace("-", "")
        charge_data["devedor"] = {
            "nome": customer.customer_name[:200],
        }
        if len(cpf_cnpj) <= 11:
            charge_data["devedor"]["cpf"] = cpf_cnpj
        else:
            charge_data["devedor"]["cnpj"] = cpf_cnpj

    client = InterAPIClient(account_name)
    response = client.create_pix_charge(txid, charge_data)

    # Create Inter PIX Charge document
    pix_charge = frappe.new_doc("Inter PIX Charge")
    pix_charge.status = "Active"
    pix_charge.charge_type = "Immediate"
    pix_charge.company = invoice.company
    pix_charge.inter_company_account = account_name
    pix_charge.sales_invoice = sales_invoice_name
    pix_charge.txid = response.get("txid", txid)
    pix_charge.chave_pix = response.get("chave", "")
    pix_charge.valor = invoice.outstanding_amount
    pix_charge.data_criacao = now_datetime()
    pix_charge.calendario_expiracao = expiration_seconds
    pix_charge.pix_copia_cola = response.get("pixCopiaECola", "")
    pix_charge.pagador_nome = customer.customer_name
    pix_charge.pagador_cpf_cnpj = customer.tax_id or ""

    # Calculate expiration
    from datetime import datetime, timedelta as td
    pix_charge.data_expiracao = now_datetime() + td(seconds=expiration_seconds)

    pix_charge.inter_response = frappe.as_json(response)
    pix_charge.insert(ignore_permissions=True)

    # Generate QR code
    if pix_charge.pix_copia_cola:
        try:
            from Brazil_Module.utils.qrcode_gen import generate_qrcode_for_doc
            generate_qrcode_for_doc(pix_charge)
        except Exception as e:
            frappe.log_error(str(e), "PIX QR Code Generation Error")

    # Link to Sales Invoice
    frappe.db.set_value(
        "Sales Invoice",
        sales_invoice_name,
        "inter_pix_charge",
        pix_charge.name,
        update_modified=False,
    )
    frappe.db.commit()

    return pix_charge.name


def create_scheduled_pix_charge(
    sales_invoice_name: str,
    due_date: date,
    fine_percent: float = 0,
    interest_percent: float = 0,
) -> str:
    """Create a scheduled PIX charge with a due date.

    Args:
        sales_invoice_name: Sales Invoice name.
        due_date: Payment due date.
        fine_percent: Late payment fine percentage.
        interest_percent: Daily interest rate percentage.

    Returns:
        Name of the created Inter PIX Charge.
    """
    from Brazil_Module.services.banking.inter_client import InterAPIClient

    invoice = frappe.get_doc("Sales Invoice", sales_invoice_name)

    if invoice.docstatus != 1:
        frappe.throw(_("Sales Invoice must be submitted"))

    account_name = frappe.db.get_value(
        "Inter Company Account",
        {"company": invoice.company, "sync_enabled": 1},
        "name",
    )
    if not account_name:
        frappe.throw(_("No Inter Company Account found for company {0}").format(invoice.company))

    txid = uuid.uuid4().hex[:35]
    customer = frappe.get_doc("Customer", invoice.customer)

    charge_data = {
        "calendario": {
            "dataDeVencimento": due_date.isoformat() if isinstance(due_date, date) else str(due_date),
            "validadeAposVencimento": 30,
        },
        "valor": {
            "original": f"{flt(invoice.outstanding_amount, 2):.2f}",
        },
        "solicitacaoPagador": f"Fatura {sales_invoice_name}",
    }

    if customer.tax_id:
        cpf_cnpj = customer.tax_id.replace(".", "").replace("/", "").replace("-", "")
        charge_data["devedor"] = {"nome": customer.customer_name[:200]}
        if len(cpf_cnpj) <= 11:
            charge_data["devedor"]["cpf"] = cpf_cnpj
        else:
            charge_data["devedor"]["cnpj"] = cpf_cnpj

    if fine_percent:
        charge_data["valor"]["multa"] = {
            "modalidade": 2,  # Percentual
            "valorPerc": f"{fine_percent:.2f}",
        }
    if interest_percent:
        charge_data["valor"]["juros"] = {
            "modalidade": 2,
            "valorPerc": f"{interest_percent:.2f}",
        }

    client = InterAPIClient(account_name)
    response = client.create_pix_charge_with_due_date(txid, charge_data)

    pix_charge = frappe.new_doc("Inter PIX Charge")
    pix_charge.status = "Active"
    pix_charge.charge_type = "Scheduled"
    pix_charge.company = invoice.company
    pix_charge.inter_company_account = account_name
    pix_charge.sales_invoice = sales_invoice_name
    pix_charge.txid = response.get("txid", txid)
    pix_charge.valor = invoice.outstanding_amount
    pix_charge.data_criacao = now_datetime()
    pix_charge.pix_copia_cola = response.get("pixCopiaECola", "")
    pix_charge.pagador_nome = customer.customer_name
    pix_charge.pagador_cpf_cnpj = customer.tax_id or ""
    pix_charge.inter_response = frappe.as_json(response)
    pix_charge.insert(ignore_permissions=True)

    if pix_charge.pix_copia_cola:
        try:
            from Brazil_Module.utils.qrcode_gen import generate_qrcode_for_doc
            generate_qrcode_for_doc(pix_charge)
        except Exception as e:
            frappe.log_error(str(e), "PIX QR Code Generation Error")

    frappe.db.set_value(
        "Sales Invoice", sales_invoice_name, "inter_pix_charge",
        pix_charge.name, update_modified=False,
    )
    frappe.db.commit()

    return pix_charge.name


def poll_pix_charge_status(charge_name: str | None = None) -> dict:
    """Check PIX charge status at the bank."""
    from Brazil_Module.services.banking.inter_client import InterAPIClient

    if charge_name:
        charges = [frappe.get_doc("Inter PIX Charge", charge_name)]
    else:
        charges = frappe.get_all(
            "Inter PIX Charge",
            filters={"status": ["in", ["Pending", "Active"]]},
            fields=["name"],
        )
        charges = [frappe.get_doc("Inter PIX Charge", c["name"]) for c in charges]

    results = {"checked": 0, "paid": 0, "expired": 0}

    for charge in charges:
        if not charge.txid:
            continue

        try:
            client = InterAPIClient(charge.inter_company_account)

            if charge.charge_type == "Scheduled":
                response = client.get_pix_charge_with_due_date(charge.txid)
            else:
                response = client.get_pix_charge(charge.txid)

            status_api = response.get("status", "").upper()
            results["checked"] += 1

            if status_api == "CONCLUIDA":
                charge.status = "Paid"
                # Get payment info from pix array
                pix_list = response.get("pix", [])
                if pix_list:
                    pix_payment = pix_list[0]
                    charge.valor_pago = flt(pix_payment.get("valor", 0))
                    charge.data_pagamento = pix_payment.get("horario")
                    charge.e2e_id = pix_payment.get("endToEndId", "")
                charge.save(ignore_permissions=True)
                results["paid"] += 1

                _handle_pix_payment(charge)

            elif status_api in ("REMOVIDA_PELO_USUARIO_RECEBEDOR", "REMOVIDA_PELO_PSP"):
                charge.status = "Cancelled"
                charge.save(ignore_permissions=True)

            # Check expiration for active charges
            elif charge.data_expiracao and frappe.utils.now_datetime() > frappe.utils.get_datetime(charge.data_expiracao):
                charge.status = "Expired"
                charge.save(ignore_permissions=True)
                results["expired"] += 1

        except Exception as e:
            frappe.log_error(str(e), f"PIX Status Check Error: {charge.name}")

    frappe.db.commit()
    return results


def scheduled_pix_status_check():
    """Scheduler entry point: check status of all pending PIX charges."""
    if not frappe.db.get_single_value("Banco Inter Settings", "enabled"):
        return
    poll_pix_charge_status()


def _handle_pix_payment(charge):
    """Create Payment Entry when PIX is paid."""
    settings = frappe.get_single("Banco Inter Settings")
    if not settings.auto_create_payment_entry:
        return

    if charge.payment_entry:
        return

    if not charge.sales_invoice:
        return

    try:
        invoice = frappe.get_doc("Sales Invoice", charge.sales_invoice)
        if invoice.outstanding_amount <= 0:
            return

        account_doc = frappe.get_doc("Inter Company Account", charge.inter_company_account)

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Receive"
        pe.party_type = "Customer"
        pe.party = invoice.customer
        pe.company = invoice.company
        pe.paid_amount = flt(charge.valor_pago or charge.valor)
        pe.received_amount = pe.paid_amount
        pe.reference_no = charge.txid or charge.name
        pe.reference_date = charge.data_pagamento or today()

        bank_gl_account = frappe.db.get_value(
            "Bank Account", account_doc.bank_account, "account"
        )
        if bank_gl_account:
            pe.paid_from = bank_gl_account
        pe.paid_to = invoice.debit_to

        pe.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": charge.sales_invoice,
            "allocated_amount": pe.paid_amount,
        })

        pe.insert(ignore_permissions=True)
        pe.submit()

        charge.payment_entry = pe.name
        charge.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(str(e), f"Auto Payment Entry Error (PIX): {charge.name}")
