"""
Webhook handler for incoming notifications from Banco Inter.

Processes payment confirmations for boletos and PIX charges.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime, flt


def process_webhook(request_data: dict, source_ip: str = "") -> dict:
    """Main webhook entry point.

    Steps:
    1. Create Webhook Log immediately (for audit)
    2. Identify event type
    3. Route to handler
    4. Update related documents
    """
    # Create log entry first
    log = frappe.new_doc("Inter Webhook Log")
    log.received_at = now_datetime()
    log.source_ip = source_ip
    log.request_body = json.dumps(request_data, default=str)
    log.event_type = "unknown"
    log.processed = 0

    try:
        # Identify event type
        event_type = _identify_event_type(request_data)
        log.event_type = event_type

        if event_type == "pix_received":
            result = _handle_pix_received(request_data, log)
        elif event_type == "boleto_paid":
            result = _handle_boleto_paid(request_data, log)
        else:
            result = {"status": "unknown_event", "message": "Unrecognized event type"}

        log.processed = 1
        log.processing_result = json.dumps(result, default=str)[:500]

    except Exception as e:
        log.processed = 0
        log.error_message = str(e)[:500]
        frappe.log_error(str(e), "Inter Webhook Processing Error")

    log.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"status": "received"}


def _identify_event_type(data: dict) -> str:
    """Determine the webhook event type from the payload."""
    # PIX webhook format includes "pix" array
    if "pix" in data:
        return "pix_received"

    # Boleto webhook includes situacao field
    if "situacao" in data or "codigoSolicitacao" in data:
        return "boleto_paid"

    return "unknown"


def _handle_pix_received(data: dict, log) -> dict:
    """Process a PIX payment notification."""
    pix_list = data.get("pix", [])

    results = []
    for pix in pix_list:
        txid = pix.get("txid", "")
        e2e_id = pix.get("endToEndId", "")
        valor = flt(pix.get("valor", 0))

        # Find matching PIX Charge
        charge_name = frappe.db.get_value(
            "Inter PIX Charge",
            {"txid": txid, "status": ["in", ["Pending", "Active"]]},
            "name",
        )

        if charge_name:
            charge = frappe.get_doc("Inter PIX Charge", charge_name)
            charge.status = "Paid"
            charge.valor_pago = valor
            charge.data_pagamento = pix.get("horario", now_datetime())
            charge.e2e_id = e2e_id
            charge.save(ignore_permissions=True)

            log.related_doctype = "Inter PIX Charge"
            log.related_document = charge_name

            # Create Payment Entry
            from Brazil_Module.services.banking.pix_service import _handle_pix_payment
            _handle_pix_payment(charge)

            results.append({"txid": txid, "matched": charge_name, "status": "paid"})
        else:
            results.append({"txid": txid, "matched": None, "status": "no_match"})

    return {"pix_payments": results}


def _handle_boleto_paid(data: dict, log) -> dict:
    """Process a boleto payment notification."""
    request_code = data.get("codigoSolicitacao", "")
    nosso_numero = data.get("nossoNumero", "")

    # Find matching boleto
    boleto_name = None
    if request_code:
        boleto_name = frappe.db.get_value(
            "Inter Boleto",
            {"inter_request_code": request_code, "status": ["in", ["Pending", "Registered"]]},
            "name",
        )
    if not boleto_name and nosso_numero:
        boleto_name = frappe.db.get_value(
            "Inter Boleto",
            {"nosso_numero": nosso_numero, "status": ["in", ["Pending", "Registered"]]},
            "name",
        )

    if boleto_name:
        boleto = frappe.get_doc("Inter Boleto", boleto_name)
        boleto.status = "Paid"
        boleto.valor_pago = flt(data.get("valorTotalRecebimento", 0))
        boleto.data_pagamento = data.get("dataPagamento", now_datetime())
        boleto.save(ignore_permissions=True)

        log.related_doctype = "Inter Boleto"
        log.related_document = boleto_name

        from Brazil_Module.services.banking.boleto_service import _handle_boleto_payment
        _handle_boleto_payment(boleto)

        return {"boleto": boleto_name, "status": "paid"}
    else:
        return {"boleto": None, "status": "no_match", "request_code": request_code}


def register_webhook_for_account(company_account_name: str) -> dict:
    """Register webhook URL with Banco Inter for an account."""
    from Brazil_Module.services.banking.inter_client import InterAPIClient

    site_url = frappe.utils.get_url()
    webhook_url = f"{site_url}/api/method/Brazil_Module.api.webhook_receiver"

    client = InterAPIClient(company_account_name)

    # Register for both PIX and Cobranca
    results = {}

    try:
        results["pix"] = client.register_webhook(webhook_url, "pix")
    except Exception as e:
        results["pix_error"] = str(e)

    try:
        results["cobranca"] = client.register_webhook(webhook_url, "cobranca")
    except Exception as e:
        results["cobranca_error"] = str(e)

    return results
