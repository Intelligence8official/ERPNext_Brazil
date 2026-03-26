# Brazil API Endpoints
# Combined Fiscal + Banking endpoints
import hmac
import json

import frappe
from frappe import _


# ── Fiscal ────────────────────────────────────────────────────────────

@frappe.whitelist()
def fetch_documents(company=None, document_type=None):
    """
    Manually trigger document fetch from SEFAZ.

    Args:
        company: Company name (optional, fetches for all if not specified)
        document_type: NF-e, CT-e, or NFS-e (optional, fetches all types if not specified)

    Returns:
        dict: Result of fetch operation
    """
    from brazil_module.services.fiscal.dfe_client import fetch_documents_for_company

    if company:
        return fetch_documents_for_company(company, document_type)
    else:
        from brazil_module.services.fiscal.dfe_client import scheduled_fetch
        scheduled_fetch()
        return {"status": "success", "message": _("Fetch initiated for all companies")}


@frappe.whitelist()
def process_nota_fiscal(nota_fiscal_name):
    """
    Manually trigger processing of a Nota Fiscal.

    Args:
        nota_fiscal_name: Name of the Nota Fiscal document

    Returns:
        dict: Processing result
    """
    from brazil_module.services.fiscal.processor import NFProcessor

    nf_doc = frappe.get_doc("Nota Fiscal", nota_fiscal_name)
    processor = NFProcessor()
    result = processor.process(nf_doc)

    return result


@frappe.whitelist()
def link_purchase_order(nota_fiscal_name, purchase_order_name):
    """
    Manually link a Nota Fiscal to a Purchase Order.

    Args:
        nota_fiscal_name: Name of the Nota Fiscal document
        purchase_order_name: Name of the Purchase Order document

    Returns:
        dict: Link result
    """
    nf_doc = frappe.get_doc("Nota Fiscal", nota_fiscal_name)
    nf_doc.purchase_order = purchase_order_name
    nf_doc.po_status = "Linked"
    nf_doc.save()

    return {"status": "success", "message": _("Purchase Order linked successfully")}


@frappe.whitelist()
def create_purchase_invoice(nota_fiscal_name, submit=False):
    """
    Create a Purchase Invoice from a Nota Fiscal.

    Args:
        nota_fiscal_name: Name of the Nota Fiscal document
        submit: Whether to submit the invoice after creation

    Returns:
        dict: Created invoice details
    """
    from brazil_module.services.fiscal.invoice_creator import InvoiceCreator

    nf_doc = frappe.get_doc("Nota Fiscal", nota_fiscal_name)
    creator = InvoiceCreator()
    invoice_name = creator.create_purchase_invoice(nf_doc, submit=submit)

    return {"status": "success", "invoice": invoice_name}


@frappe.whitelist()
def validate_chave_acesso(chave):
    """
    Validate a chave de acesso (access key).

    Args:
        chave: 44-digit access key

    Returns:
        dict: Validation result with parsed components
    """
    from brazil_module.utils.chave_acesso import validate_chave_acesso as validate, parse_chave_acesso

    is_valid = validate(chave)
    components = parse_chave_acesso(chave) if is_valid else None

    return {
        "valid": is_valid,
        "components": components
    }


@frappe.whitelist()
def get_enabled_companies():
    """
    Get list of companies with valid certificates for SEFAZ integration.

    Returns:
        list: List of company settings with sync info
    """
    companies = frappe.get_all(
        "NF Company Settings",
        filters={"certificate_valid": 1},
        fields=[
            "name", "company", "cnpj", "sync_enabled",
            "last_nsu_nfse", "last_sync", "sefaz_environment"
        ]
    )

    return companies


@frappe.whitelist()
def test_company_connection(company_settings_name):
    """
    Test SEFAZ connection for a specific company.

    Args:
        company_settings_name: Name of NF Company Settings document

    Returns:
        dict: Test result
    """
    from brazil_module.services.fiscal.dfe_client import test_sefaz_connection
    return test_sefaz_connection(company_settings_name)


@frappe.whitelist()
def fetch_for_company(company_settings_name, document_type=None):
    """
    Fetch documents from SEFAZ for a specific company.

    Args:
        company_settings_name: Name of NF Company Settings document
        document_type: Optional specific document type

    Returns:
        dict: Fetch results
    """
    from brazil_module.services.fiscal.dfe_client import fetch_documents_for_company
    return fetch_documents_for_company(company_settings_name, document_type)


@frappe.whitelist()
def unlink_purchase_invoice(nota_fiscal_name):
    """
    Unlink a Purchase Invoice from a Nota Fiscal.

    Args:
        nota_fiscal_name: Name of the Nota Fiscal document

    Returns:
        dict: Result of the operation
    """
    nf_doc = frappe.get_doc("Nota Fiscal", nota_fiscal_name)

    if not nf_doc.purchase_invoice:
        return {"status": "error", "message": _("No Purchase Invoice linked")}

    purchase_invoice_name = nf_doc.purchase_invoice

    # Clear the reference in Purchase Invoice
    frappe.db.set_value(
        "Purchase Invoice",
        purchase_invoice_name,
        {
            "nota_fiscal": None,
            "chave_de_acesso": None
        },
        update_modified=True
    )

    # Clear the reference in Nota Fiscal
    nf_doc.purchase_invoice = None
    nf_doc.invoice_status = "Pending"
    nf_doc.save()

    return {
        "status": "success",
        "message": _("Purchase Invoice {0} unlinked successfully").format(purchase_invoice_name)
    }


@frappe.whitelist()
def link_purchase_invoice(nota_fiscal_name, purchase_invoice_name):
    """
    Link a Nota Fiscal to an existing Purchase Invoice.

    Args:
        nota_fiscal_name: Name of the Nota Fiscal document
        purchase_invoice_name: Name of the Purchase Invoice document

    Returns:
        dict: Result of the operation
    """
    nf_doc = frappe.get_doc("Nota Fiscal", nota_fiscal_name)

    # Update Purchase Invoice with NF reference
    frappe.db.set_value(
        "Purchase Invoice",
        purchase_invoice_name,
        {
            "nota_fiscal": nota_fiscal_name,
            "chave_de_acesso": nf_doc.chave_de_acesso
        },
        update_modified=True
    )

    # Update Nota Fiscal
    nf_doc.purchase_invoice = purchase_invoice_name
    nf_doc.invoice_status = "Linked"
    nf_doc.save()

    return {"status": "success", "message": _("Purchase Invoice linked successfully")}


@frappe.whitelist()
def find_matching_documents(nota_fiscal_name):
    """
    Find existing Purchase Invoices and Purchase Orders that might match a Nota Fiscal.

    Args:
        nota_fiscal_name: Name of the Nota Fiscal document

    Returns:
        dict: Lists of matching invoices and orders
    """
    from frappe.utils import flt, add_days

    nf_doc = frappe.get_doc("Nota Fiscal", nota_fiscal_name)

    result = {
        "invoices": [],
        "orders": []
    }

    # Value tolerance: 5% or R$10, whichever is greater
    value_tolerance = max(flt(nf_doc.valor_total or 0) * 0.05, 10)
    min_value = flt(nf_doc.valor_total or 0) - value_tolerance
    max_value = flt(nf_doc.valor_total or 0) + value_tolerance

    # Date range: 30 days before and after
    if nf_doc.data_emissao:
        date_from = add_days(nf_doc.data_emissao, -30)
        date_to = add_days(nf_doc.data_emissao, 30)
    else:
        date_from = None
        date_to = None

    # Build supplier filter
    supplier_filter = ""
    if nf_doc.supplier:
        supplier_filter = f"AND supplier = '{nf_doc.supplier}'"

    # Find matching Purchase Invoices
    invoice_query = f"""
        SELECT name, posting_date, grand_total, bill_no, supplier, nota_fiscal
        FROM `tabPurchase Invoice`
        WHERE docstatus < 2
        {supplier_filter}
        AND grand_total BETWEEN %(min_value)s AND %(max_value)s
        {"AND posting_date BETWEEN %(date_from)s AND %(date_to)s" if date_from else ""}
        ORDER BY ABS(grand_total - %(value)s) ASC
        LIMIT 10
    """

    params = {
        "min_value": min_value,
        "max_value": max_value,
        "value": nf_doc.valor_total or 0
    }
    if date_from:
        params["date_from"] = date_from
        params["date_to"] = date_to

    result["invoices"] = frappe.db.sql(invoice_query, params, as_dict=True)

    # Find matching Purchase Orders
    order_query = f"""
        SELECT name, transaction_date, grand_total, supplier, status
        FROM `tabPurchase Order`
        WHERE docstatus < 2
        {supplier_filter}
        AND grand_total BETWEEN %(min_value)s AND %(max_value)s
        {"AND transaction_date BETWEEN %(date_from)s AND %(date_to)s" if date_from else ""}
        ORDER BY ABS(grand_total - %(value)s) ASC
        LIMIT 10
    """

    result["orders"] = frappe.db.sql(order_query, params, as_dict=True)

    return result


@frappe.whitelist()
def batch_process(documents):
    """
    Process multiple Nota Fiscal documents in batch.

    Args:
        documents: List of Nota Fiscal document names

    Returns:
        dict: Processing results summary
    """
    from brazil_module.services.fiscal.processor import NFProcessor

    if isinstance(documents, str):
        documents = json.loads(documents)

    results = {
        "processed": 0,
        "completed": 0,
        "errors": 0,
        "skipped": 0,
        "details": []
    }

    processor = NFProcessor()

    for doc_name in documents:
        try:
            nf_doc = frappe.get_doc("Nota Fiscal", doc_name)

            # Skip cancelled documents
            if nf_doc.cancelada or nf_doc.processing_status == "Cancelled":
                results["skipped"] += 1
                results["details"].append({
                    "name": doc_name,
                    "status": "skipped",
                    "message": _("Document is cancelled")
                })
                continue

            # Skip already completed documents
            if nf_doc.processing_status == "Completed":
                results["skipped"] += 1
                results["details"].append({
                    "name": doc_name,
                    "status": "skipped",
                    "message": _("Already completed")
                })
                continue

            result = processor.process(nf_doc)
            results["processed"] += 1

            if result.get("processing_status") == "Completed":
                results["completed"] += 1
                results["details"].append({
                    "name": doc_name,
                    "status": "completed"
                })
            else:
                results["details"].append({
                    "name": doc_name,
                    "status": result.get("processing_status", "Error"),
                    "message": nf_doc.processing_error if hasattr(nf_doc, "processing_error") else None
                })

        except Exception as e:
            results["errors"] += 1
            results["details"].append({
                "name": doc_name,
                "status": "error",
                "message": str(e)
            })
            frappe.log_error(str(e), f"Batch Processing Error: {doc_name}")

    return results


# ── Banking ────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_balance(company_account_name: str) -> dict:
    """Get current account balance."""
    from brazil_module.services.banking.statement_sync import update_balance

    try:
        balance = update_balance(company_account_name)
        return {"status": "success", "balance": balance}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def sync_statements(
    company_account_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Trigger statement sync (enqueued as background job)."""
    from brazil_module.services.banking.statement_sync import sync_statements_for_company
    from datetime import date

    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None

    frappe.enqueue(
        sync_statements_for_company,
        company_account_name=company_account_name,
        start_date=start,
        end_date=end,
        queue="short",
    )

    return {"status": "queued", "message": _("Statement sync initiated")}


@frappe.whitelist()
def reconcile_transactions(bank_account: str, date_from: str | None = None) -> dict:
    """Run auto-reconciliation on unmatched transactions."""
    from brazil_module.services.banking.reconciliation import batch_reconcile
    from datetime import date

    df = date.fromisoformat(date_from) if date_from else None
    return batch_reconcile(bank_account, df)


# ── Boleto / BoletoPIX ────────────────────────────────────────────────

@frappe.whitelist()
def create_boleto(
    sales_invoice: str,
    due_date: str | None = None,
) -> dict:
    """Create a BoletoPIX from a Sales Invoice."""
    from brazil_module.services.banking.boleto_service import create_boleto_from_invoice
    from datetime import date

    dd = date.fromisoformat(due_date) if due_date else None

    try:
        boleto_name = create_boleto_from_invoice(sales_invoice, due_date=dd)
        return {"status": "success", "boleto": boleto_name}
    except Exception as e:
        frappe.log_error(str(e), "Boleto Creation Error")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def cancel_boleto_api(boleto_name: str, reason: str = "Cancelled by user") -> dict:
    """Cancel a boleto at the bank."""
    from brazil_module.services.banking.boleto_service import cancel_boleto

    try:
        return cancel_boleto(boleto_name, reason)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def check_boleto_status(boleto_name: str) -> dict:
    """Check boleto payment status."""
    from brazil_module.services.banking.boleto_service import poll_boleto_status

    return poll_boleto_status(boleto_name)


@frappe.whitelist()
def download_boleto_pdf_api(boleto_name: str) -> dict:
    """Download boleto PDF."""
    from brazil_module.services.banking.boleto_service import download_boleto_pdf

    try:
        file_url = download_boleto_pdf(boleto_name)
        return {"status": "success", "file_url": file_url}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── PIX ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def create_pix_charge(
    sales_invoice: str,
    expiration_seconds: int | None = None,
) -> dict:
    """Create a PIX charge from a Sales Invoice."""
    from brazil_module.services.banking.pix_service import create_pix_charge_from_invoice

    try:
        charge_name = create_pix_charge_from_invoice(
            sales_invoice,
            expiration_seconds=int(expiration_seconds) if expiration_seconds else None,
        )
        return {"status": "success", "pix_charge": charge_name}
    except Exception as e:
        frappe.log_error(str(e), "PIX Charge Creation Error")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def check_pix_status(charge_name: str) -> dict:
    """Check PIX charge status."""
    from brazil_module.services.banking.pix_service import poll_pix_charge_status

    return poll_pix_charge_status(charge_name)


# ── Payments ───────────────────────────────────────────────────────────

@frappe.whitelist()
def create_payment_order(
    payment_type: str,
    amount: float,
    company: str,
    purchase_invoice: str = "",
    party_type: str = "",
    party: str = "",
    pix_key: str = "",
    barcode: str = "",
    **kwargs,
) -> dict:
    """Create an outbound payment order."""
    try:
        account_name = frappe.db.get_value(
            "Inter Company Account",
            {"company": company, "sync_enabled": 1},
            "name",
        )
        if not account_name:
            return {"status": "error", "message": _("No Inter Company Account for this company")}

        order = frappe.new_doc("Inter Payment Order")
        order.payment_type = payment_type
        order.company = company
        order.inter_company_account = account_name
        order.amount = float(amount)
        order.purchase_invoice = purchase_invoice or None
        order.party_type = party_type or None
        order.party = party or None

        if payment_type == "PIX":
            order.pix_key = pix_key
        elif payment_type == "Boleto Payment":
            order.barcode = barcode

        # Set recipient info from party
        if party_type and party:
            if party_type == "Supplier":
                supplier = frappe.get_doc("Supplier", party)
                order.recipient_name = supplier.supplier_name
                order.recipient_cpf_cnpj = supplier.tax_id or ""

        order.insert()
        return {"status": "success", "payment_order": order.name}

    except Exception as e:
        frappe.log_error(str(e), "Payment Order Creation Error")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def execute_payment(payment_order_name: str) -> dict:
    """Execute an approved payment order."""
    from brazil_module.services.banking.payment_service import execute_payment_order

    frappe.enqueue(
        execute_payment_order,
        payment_order_name=payment_order_name,
        queue="short",
    )
    return {"status": "queued", "message": _("Payment execution initiated")}


# ── Webhooks ───────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook_receiver() -> dict:
    """Receive webhook notifications from Banco Inter.

    Must be allow_guest=True since Inter's server calls this endpoint.
    """
    from brazil_module.services.banking.webhook_handler import process_webhook

    # Check if webhooks are enabled
    if not frappe.db.get_single_value("Banco Inter Settings", "webhook_enabled"):
        return {"status": "disabled"}

    # Validate webhook secret if configured
    webhook_secret = frappe.db.get_single_value("Banco Inter Settings", "webhook_secret")
    if webhook_secret:
        request_secret = frappe.request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(request_secret, webhook_secret):
            frappe.local.response["http_status_code"] = 403
            return {"status": "forbidden"}

    # Parse request
    try:
        if frappe.request.data:
            data = json.loads(frappe.request.data)
        else:
            data = frappe.form_dict
    except json.JSONDecodeError:
        data = {"raw": frappe.request.data.decode("utf-8", errors="replace")[:5000]}

    source_ip = frappe.request.remote_addr or ""

    return process_webhook(data, source_ip)


# ── Admin ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def test_connection(company_account_name: str) -> dict:
    """Test API connection with stored credentials."""
    from brazil_module.services.banking.auth_manager import InterAuthManager

    try:
        auth = InterAuthManager(company_account_name)
        result = auth.validate_credentials()
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def register_webhook(company_account_name: str) -> dict:
    """Register webhook URL with Banco Inter."""
    from brazil_module.services.banking.webhook_handler import register_webhook_for_account

    try:
        return register_webhook_for_account(company_account_name)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def get_dashboard_data(company: str | None = None) -> dict:
    """Get dashboard data: balance, recent transactions, pending items."""
    filters = {}
    if company:
        filters["company"] = company

    # Get accounts
    accounts = frappe.get_all(
        "Inter Company Account",
        filters={"sync_enabled": 1},
        fields=["name", "company", "current_balance", "last_statement_sync"],
    )

    # Get pending boletos count
    boleto_filters = {"status": ["in", ["Pending", "Registered"]]}
    if company:
        boleto_filters["company"] = company
    pending_boletos = frappe.db.count("Inter Boleto", boleto_filters)

    # Get pending PIX charges count
    pix_filters = {"status": ["in", ["Pending", "Active"]]}
    if company:
        pix_filters["company"] = company
    pending_pix = frappe.db.count("Inter PIX Charge", pix_filters)

    # Get pending payment orders
    payment_filters = {"status": ["in", ["Draft", "Pending Approval", "Approved"]], "docstatus": ["<", 2]}
    if company:
        payment_filters["company"] = company
    pending_payments = frappe.db.count("Inter Payment Order", payment_filters)

    return {
        "accounts": accounts,
        "pending_boletos": pending_boletos,
        "pending_pix": pending_pix,
        "pending_payments": pending_payments,
    }


# ============================================================
# Intelligence8 API
# ============================================================

@frappe.whitelist(allow_guest=True, methods=["POST"])
def telegram_webhook():
    """Receive Telegram Bot webhook updates."""
    from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot

    bot = TelegramBot()
    secret = frappe.request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not bot.validate_webhook(secret):
        frappe.throw("Unauthorized", frappe.AuthenticationError)

    import json
    raw = frappe.request.data
    update = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    bot.handle_update(update)
    return {"status": "ok"}


@frappe.whitelist()
def i8_chat_send(message, conversation=None):
    """Send a message to Intelligence8 via ERP Chat."""
    from brazil_module.services.intelligence.channels.erp_chat import send_message

    return send_message(frappe.session.user, message, conversation)


@frappe.whitelist()
def i8_chat_history(conversation):
    """Get conversation history for ERP Chat widget."""
    from brazil_module.services.intelligence.channels.erp_chat import get_conversation_history

    return get_conversation_history(conversation)


@frappe.whitelist()
def i8_dashboard_data():
    """Get Intelligence8 dashboard data."""
    from brazil_module.services.intelligence.cost_tracker import CostTracker

    tracker = CostTracker()
    today = frappe.utils.today()

    return {
        "status": "success",
        "data": {
            "daily_cost": tracker.get_daily_total(),
            "pending_approvals": frappe.db.count(
                "I8 Decision Log", {"result": "Pending", "docstatus": 0}
            ),
            "decisions_today": frappe.db.count(
                "I8 Decision Log", {"timestamp": [">=", today]}
            ),
            "agent_enabled": bool(
                frappe.db.get_single_value("I8 Agent Settings", "enabled")
            ),
        },
    }


@frappe.whitelist()
def i8_run_briefing():
    """Manually trigger the daily briefing."""
    frappe.enqueue(
        "brazil_module.services.intelligence.recurring.daily_briefing.scheduled_briefing",
        queue="short",
    )
    return {"status": "queued"}


@frappe.whitelist()
def i8_run_expense_scheduler():
    """Manually trigger the recurring expense scheduler."""
    frappe.enqueue(
        "brazil_module.services.intelligence.recurring.expense_scheduler.daily_check",
        queue="long",
        timeout=120,
    )
    return {"status": "queued"}


@frappe.whitelist()
def i8_run_reconciliation():
    """Manually trigger bank reconciliation."""
    frappe.enqueue(
        "brazil_module.services.intelligence.recurring.planning_loop.run_reconciliation",
        queue="long",
        timeout=300,
        notify_always=True,
    )
    return {"status": "queued"}


@frappe.whitelist()
def i8_run_followup_check():
    """Manually trigger follow-up check."""
    frappe.enqueue(
        "brazil_module.services.intelligence.recurring.follow_up_manager.check_overdue",
        queue="long",
        timeout=120,
    )
    return {"status": "queued"}


@frappe.whitelist()
def i8_run_payment_scheduling():
    """Manually trigger weekly payment scheduling."""
    frappe.enqueue(
        "brazil_module.services.intelligence.recurring.planning_loop.schedule_weekly_payments",
        queue="long",
        timeout=300,
    )
    return {"status": "queued"}
