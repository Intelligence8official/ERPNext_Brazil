"""
Outbound payment service.

Handles PIX send, TED transfer, and boleto payment via Banco Inter API.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, flt, today


def execute_payment_order(payment_order_name: str) -> dict:
    """Execute an approved payment order via Banco Inter API.

    Args:
        payment_order_name: Name of the Inter Payment Order.

    Returns:
        Dict with execution result.
    """
    from brazil_module.services.banking.inter_client import InterAPIClient

    order = frappe.get_doc("Inter Payment Order", payment_order_name)

    if order.status != "Processing":
        # Set to processing if called directly
        if order.status == "Approved":
            order.db_set("status", "Processing")
        else:
            frappe.throw(_("Payment order must be in 'Approved' or 'Processing' status"))

    try:
        client = InterAPIClient(order.inter_company_account)

        if order.payment_type == "PIX":
            result = _execute_pix_payment(client, order)
        elif order.payment_type == "TED":
            result = _execute_ted_payment(client, order)
        elif order.payment_type == "Boleto Payment":
            result = _execute_boleto_payment(client, order)
        else:
            frappe.throw(_("Unknown payment type: {0}").format(order.payment_type))

        # Update order with result
        order.reload()
        order.status = "Completed"
        order.transaction_id = result.get("transaction_id", "")
        order.approval_code = result.get("approval_code", "")
        order.execution_date = now_datetime()
        order.inter_response = frappe.as_json(result.get("response", {}))
        order.save(ignore_permissions=True)

        # Create Payment Entry
        _create_payment_entry_for_outbound(order)

        frappe.db.commit()
        return {"status": "success", "transaction_id": order.transaction_id}

    except Exception as e:
        order.reload()
        order.status = "Failed"
        order.inter_response = frappe.as_json({"error": str(e)})
        order.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.log_error(str(e), f"Payment Execution Error: {payment_order_name}")
        return {"status": "error", "message": str(e)}


def _execute_pix_payment(client, order) -> dict:
    """Execute a PIX payment."""
    payment_data = {
        "valor": f"{flt(order.amount, 2):.2f}",
        "descricao": f"Payment {order.purchase_invoice or order.name}",
        "destinatario": {
            "tipo": "CHAVE",
            "chave": order.pix_key,
        },
    }

    if order.scheduled_date:
        payment_data["dataAgendamento"] = str(order.scheduled_date)

    response = client.send_pix(payment_data)

    return {
        "transaction_id": response.get("endToEndId", response.get("codigoSolicitacao", "")),
        "approval_code": response.get("codigoSolicitacao", ""),
        "response": response,
    }


def _execute_ted_payment(client, order) -> dict:
    """Execute a TED transfer."""
    account_type_map = {
        "Conta Corrente": "CONTA_CORRENTE",
        "Conta Poupanca": "CONTA_POUPANCA",
    }

    payment_data = {
        "valor": flt(order.amount, 2),
        "descricao": f"Payment {order.purchase_invoice or order.name}",
        "destinatario": {
            "nome": order.recipient_name or "",
            "cpfCnpj": order.recipient_cpf_cnpj or "",
            "banco": order.recipient_bank_code or "",
            "agencia": order.recipient_agency or "",
            "conta": order.recipient_account or "",
            "tipoConta": account_type_map.get(order.recipient_account_type, "CONTA_CORRENTE"),
        },
    }

    response = client.send_ted(payment_data)

    return {
        "transaction_id": response.get("codigoTransacao", ""),
        "approval_code": response.get("codigoSolicitacao", ""),
        "response": response,
    }


def _execute_boleto_payment(client, order) -> dict:
    """Pay a boleto by barcode."""
    payment_data = {
        "codBarraLinhaDigitavel": order.barcode,
        "valorPagar": flt(order.amount, 2),
        "dataVencimento": str(order.boleto_due_date) if order.boleto_due_date else "",
    }

    if order.scheduled_date:
        payment_data["dataPagamento"] = str(order.scheduled_date)

    response = client.pay_barcode(payment_data)

    return {
        "transaction_id": response.get("codigoTransacao", ""),
        "approval_code": response.get("codigoSolicitacao", ""),
        "response": response,
    }


def _create_payment_entry_for_outbound(order):
    """Create ERPNext Payment Entry for a completed outbound payment."""
    if order.payment_entry:
        return

    if not order.purchase_invoice and not order.party:
        return

    try:
        account_doc = frappe.get_doc("Inter Company Account", order.inter_company_account)

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Pay"
        pe.company = order.company
        pe.paid_amount = flt(order.amount)
        pe.received_amount = pe.paid_amount
        pe.reference_no = order.transaction_id or order.name
        pe.reference_date = order.execution_date or today()

        # Set party
        if order.party_type and order.party:
            pe.party_type = order.party_type
            pe.party = order.party

        # Set bank account
        bank_gl_account = frappe.db.get_value(
            "Bank Account", account_doc.bank_account, "account"
        )
        if bank_gl_account:
            pe.paid_from = bank_gl_account

        # Link to Purchase Invoice if available
        if order.purchase_invoice:
            invoice = frappe.get_doc("Purchase Invoice", order.purchase_invoice)
            pe.paid_to = invoice.credit_to

            pe.append("references", {
                "reference_doctype": "Purchase Invoice",
                "reference_name": order.purchase_invoice,
                "allocated_amount": pe.paid_amount,
            })

        pe.inter_payment_order = order.name
        pe.insert(ignore_permissions=True)
        pe.submit()

        order.payment_entry = pe.name
        order.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(str(e), f"Outbound Payment Entry Error: {order.name}")


def scheduled_payment_status_check():
    """Scheduler entry point: check status of processing payments."""
    if not frappe.db.get_single_value("Banco Inter Settings", "enabled"):
        return

    # Check payments stuck in Processing (timeout scenario)
    processing = frappe.get_all(
        "Inter Payment Order",
        filters={"status": "Processing", "docstatus": 1},
        pluck="name",
    )

    for order_name in processing:
        try:
            order = frappe.get_doc("Inter Payment Order", order_name)
            # If stuck for more than 1 hour, retry
            if order.modified:
                from datetime import timedelta
                if now_datetime() - frappe.utils.get_datetime(order.modified) > timedelta(hours=1):
                    execute_payment_order(order_name)
        except Exception as e:
            frappe.log_error(str(e), f"Payment Status Check Error: {order_name}")
