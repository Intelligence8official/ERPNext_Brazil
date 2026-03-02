"""
Boleto / BoletoPIX billing service.

Handles boleto creation from Sales Invoices, status polling, cancellation,
PDF download, and auto Payment Entry creation on payment.
"""

from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import now_datetime, getdate, flt, today


def on_invoice_submit(doc, method=None):
    """Hook: called when a Sales Invoice is submitted.

    Currently a no-op -- boleto generation is triggered manually via button.
    Can be enabled for auto-generation by uncommenting the logic below.
    """
    # Uncomment to auto-generate BoletoPIX on invoice submission:
    # settings = frappe.get_single("Banco Inter Settings")
    # if settings.enabled and settings.enable_pix_on_boleto:
    #     frappe.enqueue(
    #         create_boleto_from_invoice,
    #         sales_invoice_name=doc.name,
    #         queue="short",
    #     )
    pass


def create_boleto_from_invoice(
    sales_invoice_name: str,
    due_date: date | None = None,
    discount: dict | None = None,
    fine: dict | None = None,
) -> str:
    """Create a BoletoPIX from a Sales Invoice.

    Args:
        sales_invoice_name: Name of the Sales Invoice.
        due_date: Custom due date (defaults to settings.default_days_to_due).
        discount: Optional discount config {valor, dataLimite}.
        fine: Optional fine config {multa_percentual, mora_percentual}.

    Returns:
        Name of the created Inter Boleto document.
    """
    from brazil.services.banking.inter_client import InterAPIClient

    invoice = frappe.get_doc("Sales Invoice", sales_invoice_name)

    if invoice.docstatus != 1:
        frappe.throw(_("Sales Invoice must be submitted"))
    if invoice.outstanding_amount <= 0:
        frappe.throw(_("Sales Invoice has no outstanding amount"))

    settings = frappe.get_single("Banco Inter Settings")

    # Find Inter Company Account for this company
    account_name = frappe.db.get_value(
        "Inter Company Account",
        {"company": invoice.company, "sync_enabled": 1},
        "name",
    )
    if not account_name:
        frappe.throw(_("No Inter Company Account found for company {0}").format(invoice.company))

    # Calculate due date
    if not due_date:
        days = settings.default_days_to_due or 30
        due_date = date.today() + timedelta(days=days)

    # Get customer/payer info
    customer = frappe.get_doc("Customer", invoice.customer)
    payer_info = _get_payer_info(customer, invoice)

    # Build API request
    boleto_data = {
        "seuNumero": sales_invoice_name,
        "valorNominal": flt(invoice.outstanding_amount, 2),
        "dataVencimento": due_date.isoformat() if isinstance(due_date, date) else str(due_date),
        "numDiasAgenda": settings.auto_cancel_expired_days or 5,
        "pagador": {
            "cpfCnpj": payer_info["cpf_cnpj"],
            "tipoPessoa": "JURIDICA" if len(payer_info["cpf_cnpj"]) > 11 else "FISICA",
            "nome": payer_info["name"][:100],
            "endereco": payer_info.get("address", "")[:100],
            "cidade": payer_info.get("city", "")[:60],
            "uf": payer_info.get("state", "")[:2],
            "cep": payer_info.get("cep", "")[:8],
        },
    }

    # Add discount if provided
    if discount:
        boleto_data["desconto1"] = {
            "codigoDesconto": "VALORFIXODATAINFORMADA",
            "data": discount.get("dataLimite", ""),
            "valor": flt(discount.get("valor", 0), 2),
        }

    # Add fine/interest if provided
    if fine:
        if fine.get("multa_percentual"):
            boleto_data["multa"] = {
                "codigoMulta": "PERCENTUAL",
                "valor": flt(fine["multa_percentual"], 2),
            }
        if fine.get("mora_percentual"):
            boleto_data["mora"] = {
                "codigoMora": "TAXAMENSAL",
                "valor": flt(fine["mora_percentual"], 2),
            }

    # Call API
    client = InterAPIClient(account_name)
    response = client.create_boleto(boleto_data)

    # Create Inter Boleto document
    boleto = frappe.new_doc("Inter Boleto")
    boleto.status = "Registered"
    boleto.company = invoice.company
    boleto.inter_company_account = account_name
    boleto.sales_invoice = sales_invoice_name
    boleto.seu_numero = sales_invoice_name
    boleto.nosso_numero = response.get("nossoNumero", "")
    boleto.codigo_barras = response.get("codigoBarras", "")
    boleto.linha_digitavel = response.get("linhaDigitavel", "")
    boleto.valor_nominal = invoice.outstanding_amount
    boleto.data_emissao = date.today()
    boleto.data_vencimento = due_date
    boleto.pagador_nome = payer_info["name"]
    boleto.pagador_cpf_cnpj = payer_info["cpf_cnpj"]
    boleto.pagador_endereco = payer_info.get("address", "")
    boleto.pagador_cidade = payer_info.get("city", "")
    boleto.pagador_uf = payer_info.get("state", "")
    boleto.pagador_cep = payer_info.get("cep", "")
    boleto.pix_enabled = 1 if settings.enable_pix_on_boleto else 0
    boleto.pix_copia_cola = response.get("pixCopiaECola", "")
    boleto.inter_request_code = response.get("codigoSolicitacao", "")

    # Add discount/fine if set
    if discount:
        boleto.desconto_valor = discount.get("valor", 0)
        boleto.desconto_data_limite = discount.get("dataLimite")
    if fine:
        boleto.multa_percentual = fine.get("multa_percentual", 0)
        boleto.mora_percentual = fine.get("mora_percentual", 0)

    boleto.inter_response = frappe.as_json(response)
    boleto.insert(ignore_permissions=True)

    # Generate QR code if PIX enabled
    if boleto.pix_copia_cola:
        try:
            from brazil.utils.qrcode_gen import generate_qrcode_for_doc
            generate_qrcode_for_doc(boleto)
        except Exception as e:
            frappe.log_error(str(e), "QR Code Generation Error")

    # Download PDF
    try:
        download_boleto_pdf(boleto.name)
    except Exception as e:
        frappe.log_error(str(e), "Boleto PDF Download Error")

    # Link boleto to Sales Invoice
    frappe.db.set_value(
        "Sales Invoice",
        sales_invoice_name,
        "inter_boleto",
        boleto.name,
        update_modified=False,
    )
    frappe.db.commit()

    return boleto.name


def poll_boleto_status(boleto_name: str | None = None) -> dict:
    """Check payment status of a boleto at the bank.

    Args:
        boleto_name: Specific boleto to check. If None, checks all pending.

    Returns:
        Dict with status update results.
    """
    from brazil.services.banking.inter_client import InterAPIClient

    if boleto_name:
        boletos = [frappe.get_doc("Inter Boleto", boleto_name)]
    else:
        boletos = frappe.get_all(
            "Inter Boleto",
            filters={"status": ["in", ["Pending", "Registered"]]},
            fields=["name"],
        )
        boletos = [frappe.get_doc("Inter Boleto", b["name"]) for b in boletos]

    results = {"checked": 0, "updated": 0, "paid": 0}

    for boleto in boletos:
        if not boleto.inter_request_code:
            continue

        try:
            client = InterAPIClient(boleto.inter_company_account)
            response = client.get_boleto(boleto.inter_request_code)

            situacao = response.get("situacao", "").upper()
            results["checked"] += 1

            if situacao in ("PAGO", "RECEBIDO"):
                boleto.status = "Paid"
                boleto.valor_pago = flt(response.get("valorTotalRecebimento", 0))
                boleto.data_pagamento = response.get("dataPagamento")
                boleto.save(ignore_permissions=True)
                results["paid"] += 1
                results["updated"] += 1

                # Auto-create Payment Entry
                _handle_boleto_payment(boleto)

            elif situacao == "VENCIDO":
                if boleto.status != "Overdue":
                    boleto.status = "Overdue"
                    boleto.save(ignore_permissions=True)
                    results["updated"] += 1

            elif situacao in ("CANCELADO", "BAIXADO"):
                if boleto.status != "Cancelled":
                    boleto.status = "Cancelled"
                    boleto.save(ignore_permissions=True)
                    results["updated"] += 1

        except Exception as e:
            frappe.log_error(str(e), f"Boleto Status Check Error: {boleto.name}")

    frappe.db.commit()
    return results


def cancel_boleto(boleto_name: str, reason: str = "Cancelled by user") -> dict:
    """Cancel a boleto at the bank."""
    from brazil.services.banking.inter_client import InterAPIClient

    boleto = frappe.get_doc("Inter Boleto", boleto_name)

    if boleto.status not in ("Pending", "Registered"):
        frappe.throw(_("Only pending or registered boletos can be cancelled"))

    if not boleto.inter_request_code:
        frappe.throw(_("Boleto has no request code - cannot cancel at bank"))

    client = InterAPIClient(boleto.inter_company_account)
    response = client.cancel_boleto(boleto.inter_request_code, reason)

    boleto.status = "Cancelled"
    boleto.save(ignore_permissions=True)
    frappe.db.commit()

    return {"status": "success", "response": response}


def download_boleto_pdf(boleto_name: str) -> str:
    """Download boleto PDF and attach to document.

    Returns:
        File URL of the attached PDF.
    """
    from brazil.services.banking.inter_client import InterAPIClient

    boleto = frappe.get_doc("Inter Boleto", boleto_name)

    if not boleto.inter_request_code:
        frappe.throw(_("Boleto has no request code"))

    client = InterAPIClient(boleto.inter_company_account)
    pdf_content = client.download_boleto_pdf(boleto.inter_request_code)

    # Save as Frappe File
    file_name = f"boleto_{boleto.name}.pdf"
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": file_name,
        "attached_to_doctype": "Inter Boleto",
        "attached_to_name": boleto.name,
        "content": pdf_content,
        "is_private": 1,
    })
    file_doc.save(ignore_permissions=True)

    boleto.boleto_pdf = file_doc.file_url
    boleto.save(ignore_permissions=True)
    frappe.db.commit()

    return file_doc.file_url


def scheduled_boleto_status_check():
    """Scheduler entry point: check status of all pending boletos."""
    if not frappe.db.get_single_value("Banco Inter Settings", "enabled"):
        return
    poll_boleto_status()


def cancel_expired_boletos():
    """Scheduler entry point: cancel boletos past their expiry + grace period."""
    if not frappe.db.get_single_value("Banco Inter Settings", "enabled"):
        return

    settings = frappe.get_single("Banco Inter Settings")
    grace_days = settings.auto_cancel_expired_days or 5

    cutoff_date = date.today() - timedelta(days=grace_days)

    expired = frappe.get_all(
        "Inter Boleto",
        filters={
            "status": ["in", ["Registered", "Overdue"]],
            "data_vencimento": ["<", cutoff_date],
        },
        pluck="name",
    )

    for boleto_name in expired:
        try:
            cancel_boleto(boleto_name, reason="Auto-cancelled: past expiry grace period")
        except Exception as e:
            frappe.log_error(str(e), f"Auto-cancel Boleto Error: {boleto_name}")


def _get_payer_info(customer, invoice) -> dict:
    """Extract payer information from Customer and Invoice."""
    info = {
        "name": customer.customer_name or invoice.customer_name or "",
        "cpf_cnpj": customer.tax_id or "",
    }

    # Try to get address from invoice or customer
    address_name = invoice.customer_address or frappe.db.get_value(
        "Dynamic Link",
        {"link_doctype": "Customer", "link_name": customer.name, "parenttype": "Address"},
        "parent",
    )

    if address_name:
        address = frappe.get_doc("Address", address_name)
        info["address"] = address.address_line1 or ""
        info["city"] = address.city or ""
        info["state"] = address.state or ""
        info["cep"] = (address.pincode or "").replace("-", "").replace(".", "")
    else:
        info["address"] = ""
        info["city"] = ""
        info["state"] = ""
        info["cep"] = ""

    return info


def _handle_boleto_payment(boleto):
    """Create Payment Entry when boleto is paid."""
    settings = frappe.get_single("Banco Inter Settings")
    if not settings.auto_create_payment_entry:
        return

    if boleto.payment_entry:
        return  # Already has a PE

    if not boleto.sales_invoice:
        return

    try:
        invoice = frappe.get_doc("Sales Invoice", boleto.sales_invoice)
        if invoice.outstanding_amount <= 0:
            return

        account_doc = frappe.get_doc("Inter Company Account", boleto.inter_company_account)

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Receive"
        pe.party_type = "Customer"
        pe.party = invoice.customer
        pe.company = invoice.company
        pe.paid_amount = flt(boleto.valor_pago or boleto.valor_nominal)
        pe.received_amount = pe.paid_amount
        pe.reference_no = boleto.nosso_numero or boleto.name
        pe.reference_date = boleto.data_pagamento or today()
        pe.paid_to = invoice.debit_to
        pe.paid_from = account_doc.bank_account

        # Get bank account's GL account
        bank_gl_account = frappe.db.get_value(
            "Bank Account", account_doc.bank_account, "account"
        )
        if bank_gl_account:
            pe.paid_from = bank_gl_account

        pe.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": boleto.sales_invoice,
            "allocated_amount": pe.paid_amount,
        })

        pe.insert(ignore_permissions=True)
        pe.submit()

        boleto.payment_entry = pe.name
        boleto.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(str(e), f"Auto Payment Entry Error: {boleto.name}")
