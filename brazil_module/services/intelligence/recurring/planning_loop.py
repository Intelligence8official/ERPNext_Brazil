"""
Planning Loop — Proactive agent that reviews pending tasks every hour.

Checks for:
1. Unreconciled bank transactions -> auto-reconcile
2. Pending NFs without invoices -> trigger processing
3. Overdue follow-ups -> send reminders
4. Overdue payments -> alert via Telegram
"""

from datetime import date, timedelta

import frappe

from brazil_module.services.banking.payment_common import JobTimeoutException, bank_gl_account
from brazil_module.services.banking.payment_guards import (
    DOCTYPE as PAYMENT_ORDER_DOCTYPE,
    NON_BLOCKING_STATUSES,
    check_invoice_payable,
    get_inter_account_for_company,
    is_integration_enabled,
)

# "Nothing is paying this invoice": no Payment Entry (draft or submitted) and no *blocking*
# Inter Payment Order. The order half is payment_guards.is_blocking() written in SQL: an order
# blocks unless it is Failed / Cancelled, or Completed with a Payment Entry. A Failed order must
# neither stop a new attempt nor silence an alert; an unknown (NULL) status blocks.
_NON_BLOCKING_SQL = ", ".join(f"'{status}'" for status in NON_BLOCKING_STATUSES)
_NO_PAYMENT_UNDER_WAY_SQL = f"""
    AND NOT EXISTS (
        SELECT 1 FROM `tabPayment Entry Reference` per
        JOIN `tabPayment Entry` pe ON pe.name = per.parent
        WHERE per.reference_name = pi.name
        AND per.reference_doctype = 'Purchase Invoice'
        AND pe.docstatus < 2
    )
    AND NOT EXISTS (
        SELECT 1 FROM `tabInter Payment Order` ipo
        WHERE ipo.purchase_invoice = pi.name
        AND ipo.docstatus < 2
        AND IFNULL(ipo.status, '') NOT IN ({_NON_BLOCKING_SQL})
        AND NOT (IFNULL(ipo.status, '') = 'Completed' AND IFNULL(ipo.payment_entry, '') != '')
    )
"""

SUMMARY_TEXT_LIMIT = 160


def hourly_check():
    """Scheduled job: runs every hour to check for pending work."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return

    run_reconciliation()
    check_overdue_payments()
    check_urgent_payments()

    # Run anomaly detection once per day (at first hourly check)
    _run_daily_checks_if_needed()


def _run_daily_checks_if_needed():
    """Run daily checks (anomaly detection, supplier scoring) if not run today."""
    cache_key = f"i8:daily_checks:{date.today().isoformat()}"
    if frappe.cache.get_value(cache_key):
        return

    try:
        from brazil_module.services.intelligence.analytics.anomaly_detector import daily_anomaly_check
        daily_anomaly_check()
    except Exception as e:
        frappe.log_error(str(e), "I8 Daily Anomaly Check Error")

    try:
        from brazil_module.services.intelligence.analytics.supplier_intelligence import update_supplier_scores
        update_supplier_scores()
    except Exception as e:
        frappe.log_error(str(e), "I8 Supplier Score Update Error")

    try:
        from brazil_module.services.intelligence.analytics.compliance import check_nf_cancellations, check_tax_anomalies
        check_nf_cancellations()
        check_tax_anomalies()
    except Exception as e:
        frappe.log_error(str(e), "I8 Compliance Check Error")

    frappe.cache.set_value(cache_key, 1, expires_in_sec=86400)


def run_reconciliation(notify_always: bool = False):
    """Auto-reconcile unmatched bank transactions for all Inter accounts.

    Args:
        notify_always: If True, send Telegram notification even if nothing was reconciled.
                       Set to True when triggered manually from briefing button.
    """
    try:
        accounts = frappe.get_all(
            "Bank Account",
            filters={"is_company_account": 1},
            fields=["name", "account_name"],
        )

        if not accounts:
            if notify_always:
                _notify_telegram("Conciliacao: Nenhuma conta bancaria configurada.")
            return

        total_matched = 0
        total_unmatched = 0
        total_errors = 0
        account_details = []

        for account in accounts:
            try:
                from brazil_module.services.banking.reconciliation import batch_reconcile
                result = batch_reconcile(account["name"])
                matched = result.get("matched", 0)
                unmatched = result.get("unmatched", 0)
                errors = result.get("errors", 0)
                total_matched += matched
                total_unmatched += unmatched
                total_errors += errors

                if matched > 0 or unmatched > 0:
                    acc_name = (account.get("account_name") or account["name"])[:25]
                    account_details.append(f"  {acc_name}: {matched} conciliadas, {unmatched} pendentes")
            except Exception as e:
                total_errors += 1
                frappe.log_error(str(e), f"I8 Reconciliation Error: {account['name']}")
                acc_name = (account.get("account_name") or account["name"])[:25]
                account_details.append(f"  {acc_name}: erro - {str(e)[:50]}")

        # Always notify when triggered manually, or when there are results
        if notify_always or total_matched > 0 or total_errors > 0:
            lines = ["*Conciliacao bancaria concluida:*\n"]
            if total_matched > 0:
                lines.append(f"  {total_matched} transacoes conciliadas")
            if total_unmatched > 0:
                lines.append(f"  {total_unmatched} transacoes sem match")
            if total_errors > 0:
                lines.append(f"  {total_errors} erros")
            if total_matched == 0 and total_unmatched == 0 and total_errors == 0:
                lines.append("  Nenhuma transacao pendente para conciliar")
            if account_details:
                lines.append("\n*Por conta:*")
                lines.extend(account_details)
            _notify_telegram("\n".join(lines))

            if total_matched > 0 or notify_always:
                try:
                    from brazil_module.services.intelligence.notifications import notify_desk
                    notify_desk(
                        title="I8: Bank Reconciliation",
                        message=f"{total_matched} transactions reconciled, {total_unmatched} pending",
                    )
                except Exception:
                    pass

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(str(e), "I8 Planning Loop: Reconciliation Error")
        _notify_telegram(f"Erro na conciliacao bancaria: {str(e)[:100]}")


def check_overdue_payments():
    """Alert via Telegram if there are invoices overdue today WITHOUT any payment.

    Excludes invoices that already have a Payment Entry (draft or submitted) or a blocking
    Inter Payment Order. A Failed order does not silence the alert.
    """
    try:
        today = date.today()
        newly_overdue = frappe.db.sql(f"""
            SELECT pi.name, pi.supplier_name, pi.outstanding_amount
            FROM `tabPurchase Invoice` pi
            WHERE pi.docstatus = 1
            AND pi.outstanding_amount > 0
            AND pi.due_date = %s
            {_NO_PAYMENT_UNDER_WAY_SQL}
        """, today.isoformat(), as_dict=True)

        if newly_overdue:
            total = sum(float(inv.get("outstanding_amount") or 0) for inv in newly_overdue)
            lines = [f"Pagamentos vencendo hoje: R$ {total:,.2f}\n"]
            for inv in newly_overdue:
                supplier = (inv.get("supplier_name") or "")[:30]
                lines.append(f"- {inv['name']}: {supplier} R$ {float(inv['outstanding_amount']):,.2f}")
            _notify_telegram("\n".join(lines))

    except Exception as e:
        frappe.log_error(title="I8 Planning Loop: Overdue Check Error", message=str(e))


def process_pending_nfs():
    """Process Nota Fiscals that haven't been matched to invoices yet.

    Called from Telegram briefing button or planning loop.
    """
    try:
        pending_nfs = frappe.get_all(
            "Nota Fiscal",
            filters={
                "invoice_status": ["in", ["Pending", "New", ""]],
                "processing_status": ["!=", "Cancelled"],
            },
            fields=["name"],
            limit=10,
        )

        if not pending_nfs:
            _notify_telegram("Nenhuma NF pendente para processar.")
            return

        # Load full docs to get field values safely
        lines = [f"*Processando {len(pending_nfs)} NFs pendentes:*\n"]
        nf_docs = []
        for nf_ref in pending_nfs:
            try:
                nf_doc = frappe.get_doc("Nota Fiscal", nf_ref["name"])
                supplier = (nf_doc.get("razao_social") or nf_doc.get("cnpj") or nf_doc.name)[:35]
                valor = float(nf_doc.get("valor_total") or 0)
                doc_type = nf_doc.get("document_type") or "NF"
                lines.append(f"  - {nf_doc.name}: {supplier} R$ {valor:,.2f} ({doc_type})")
                nf_docs.append(nf_doc)
            except Exception:
                nf_docs.append(None)
        _notify_telegram("\n".join(lines))

        processed = 0
        errors = 0
        for nf_doc in nf_docs:
            if not nf_doc:
                errors += 1
                continue
            try:
                frappe.enqueue(
                    "brazil_module.services.intelligence.agent.process_single_event",
                    queue="long",
                    job_id=f"i8:nf_process:{nf_doc.name}",
                    event_type="nf_received",
                    event_id=nf_doc.name,
                    event_data={
                        "module": "fiscal",
                        "nota_fiscal": nf_doc.name,
                        "supplier": nf_doc.get("cnpj") or nf_doc.get("cnpj_emitente") or "",
                    },
                    deduplicate=True,
                )
                processed += 1
            except Exception as e:
                errors += 1
                frappe.log_error(str(e), f"I8 NF Processing Error: {nf_doc.name}")

        if errors > 0:
            _notify_telegram(f"NFs enfileiradas: {processed} ok, {errors} erros. O agente processara em background.")
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(str(e), "I8 Planning Loop: NF Processing Error")
        _notify_telegram(f"Erro ao processar NFs: {str(e)[:100]}")


def check_urgent_payments():
    """Alert via Telegram if there are invoices due today or tomorrow without payment scheduled.

    Excludes invoices that already have a Payment Entry or a blocking Inter Payment Order.
    A Failed order does not silence the alert.
    """
    try:
        today = date.today()
        tomorrow = today + timedelta(days=1)

        urgent = frappe.db.sql(f"""
            SELECT pi.name, pi.supplier_name, pi.outstanding_amount, pi.due_date
            FROM `tabPurchase Invoice` pi
            WHERE pi.docstatus = 1
            AND pi.outstanding_amount > 0
            AND pi.due_date IN (%s, %s)
            {_NO_PAYMENT_UNDER_WAY_SQL}
        """, (today.isoformat(), tomorrow.isoformat()), as_dict=True)

        if urgent:
            total = sum(float(inv["outstanding_amount"]) for inv in urgent)
            lines = [f"*URGENTE: {len(urgent)} pagamentos vencem hoje/amanha — R$ {total:,.2f}*\n"]
            for inv in urgent:
                supplier = (inv.get("supplier_name") or "")[:30]
                day_label = "HOJE" if str(inv["due_date"]) == today.isoformat() else "AMANHA"
                lines.append(f"  - {inv['name']}: {supplier} R$ {float(inv['outstanding_amount']):,.2f} ({day_label})")
            _notify_telegram("\n".join(lines))
            try:
                from brazil_module.services.intelligence.notifications import notify_desk
                notify_desk(
                    title="I8: Urgent Payments",
                    message=f"{len(urgent)} payments due today/tomorrow totaling R$ {total:,.2f}",
                )
            except Exception:
                pass
    except Exception as e:
        frappe.log_error(title="I8 Planning Loop: Urgent Payment Check Error", message=str(e))


def schedule_weekly_payments():
    """Create and queue the payments of the invoices due this week. Runs on the configured day.

    The scheduler never talks to the bank. Every invoice becomes an Inter Payment Order through
    ``payment_service.create_payment_order_for_invoice`` and, when the order comes out
    ``Approved``, its own execution job - which reports through its own alerts.
    A cache-based lock prevents concurrent execution from cron + manual trigger.
    """
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    if not frappe.db.get_single_value("I8 Agent Settings", "auto_schedule_payments"):
        return
    if not is_integration_enabled():  # the kill switch: Banco Inter Settings.enabled
        return
    if not _acquire_scheduling_lock():
        return

    invoices = _invoices_due_this_week(date.today())
    if not invoices:
        return

    results = [_schedule_in_own_transaction(inv) for inv in invoices]
    _send_payment_summary(results)
    frappe.db.commit()


def _acquire_scheduling_lock() -> bool:
    """Prevent concurrent/duplicate execution (lock for 30 minutes)."""
    lock_key = f"i8:payment_scheduling:lock:{date.today().isoformat()}"
    if frappe.cache.get_value(lock_key):
        frappe.log_error(
            "schedule_weekly_payments skipped: already running or recently completed",
            "I8 Payment Scheduling Lock",
        )
        return False
    frappe.cache.set_value(lock_key, 1, expires_in_sec=1800)
    return True


def _invoices_due_this_week(today: date) -> list:
    """Outstanding Purchase Invoices due until Sunday that nothing is paying yet."""
    week_end = today + timedelta(days=6 - today.weekday())
    return frappe.db.sql(f"""
        SELECT pi.name, pi.supplier, pi.supplier_name, pi.outstanding_amount, pi.due_date
        FROM `tabPurchase Invoice` pi
        WHERE pi.docstatus = 1
        AND pi.outstanding_amount > 0
        AND pi.due_date BETWEEN %s AND %s
        {_NO_PAYMENT_UNDER_WAY_SQL}
        ORDER BY pi.due_date ASC
    """, (today.isoformat(), week_end.isoformat()), as_dict=True)


def _schedule_in_own_transaction(inv: dict) -> dict:
    """One invoice, one transaction: commit what worked, roll back what did not.

    Without the rollback, an order that was inserted but refused on submit would be committed
    by the next invoice and stay behind as a draft that blocks its own invoice.
    """
    try:
        result = _schedule_single_payment(inv)
        frappe.db.commit()
        return result
    except JobTimeoutException:
        raise  # the job ran out of time: stop here, never go on to the next invoice
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(title=f"I8 Payment Scheduling Error: {inv['name']}", message=frappe.get_traceback())
        return {"status": "error", "invoice": inv["name"], "error": str(e)}


def _schedule_single_payment(inv: dict) -> dict:
    """Schedule payment for a single invoice based on its payment method.

    ``check_invoice_payable`` is the same guard the order, the API and the execution use. The
    database (unique ``invoice_lock``) is the backstop for two runs racing past it.
    """
    invoice_name = inv["name"]
    reason = check_invoice_payable(invoice_name, float(inv["outstanding_amount"]))
    if reason:
        return {"status": "skipped", "invoice": invoice_name, "reason": reason}

    mode = _get_payment_mode(invoice_name)
    if mode in ("Pix", "PIX"):
        return _schedule_pix_payment(inv, inv["supplier"])
    if mode in ("Boleto",):
        return _schedule_boleto_payment(inv)
    if mode in ("Credit Card",):
        return _handle_credit_card_payment(inv)
    if mode in ("Wire Transfer", "TED"):
        return _schedule_ted_payment(inv)
    return {"status": "skipped", "invoice": invoice_name, "reason": f"Unknown payment mode: {mode}"}


def _get_payment_mode(invoice_name: str) -> str:
    """Get the mode of payment from a Purchase Invoice's payment schedule."""
    schedule = frappe.get_all(
        "Payment Schedule",
        filters={"parent": invoice_name, "parenttype": "Purchase Invoice"},
        fields=["mode_of_payment"],
        limit=1,
    )
    if schedule and schedule[0].get("mode_of_payment"):
        return schedule[0]["mode_of_payment"]

    # Fallback: check supplier's default payment terms
    supplier = frappe.db.get_value("Purchase Invoice", invoice_name, "supplier")
    if supplier:
        template = frappe.db.get_value("Supplier", supplier, "payment_terms")
        if template:
            terms = frappe.get_all(
                "Payment Terms Template Detail",
                filters={"parent": template},
                fields=["mode_of_payment"],
                limit=1,
            )
            if terms and terms[0].get("mode_of_payment"):
                return terms[0]["mode_of_payment"]

    return ""


def _schedule_pix_payment(inv: dict, supplier: str) -> dict:
    """Create the PIX order of an invoice (key from the supplier) and queue its execution."""
    pix_key = frappe.db.get_value("Supplier", supplier, "pix_key")
    if not pix_key:
        return {"status": "error", "invoice": inv["name"], "error": "Supplier has no PIX key"}
    return _create_and_queue_order(inv, "PIX", "PIX", pix_key=pix_key)


def _schedule_boleto_payment(inv: dict) -> dict:
    """Create the boleto order of an invoice (barcode and due date from it) and queue its execution."""
    barcode = frappe.db.get_value("Purchase Invoice", inv["name"], "boleto_barcode")
    if not barcode:
        return {"status": "error", "invoice": inv["name"], "error": "The Purchase Invoice has no boleto barcode"}
    return _create_and_queue_order(
        inv, "Boleto Payment", "Boleto", barcode=barcode, boleto_due_date=inv["due_date"],
    )


def _create_and_queue_order(inv: dict, payment_type: str, method: str, **details) -> dict:
    """The order first, committed; only then - and only when it is ``Approved`` - its job."""
    from brazil_module.services.banking.payment_service import create_payment_order_for_invoice

    order_name = create_payment_order_for_invoice(
        inv["name"], payment_type, scheduled_date=inv["due_date"], **details,
    )
    # frappe.enqueue is immediate: a worker that claimed the order before this transaction is
    # committed would not find an Approved row, skip it, and nothing would ever send it.
    frappe.db.commit()

    result = {
        "invoice": inv["name"],
        "supplier": inv.get("supplier_name", ""),
        "amount": float(inv["outstanding_amount"]),
        "due_date": str(inv["due_date"]),
        "method": method,
        "payment_order": order_name,
    }
    try:
        return _queue_when_approved(result)
    except JobTimeoutException:
        raise
    except Exception as e:
        # The order exists and protects its invoice either way: tell the operator which one it is.
        frappe.log_error(title=f"I8 Payment Scheduling Error: {order_name}", message=frappe.get_traceback())
        return {
            "status": "error",
            "invoice": inv["name"],
            "error": f"Order {order_name} was created but could not be queued ({e}). Execute it from the ERP.",
        }


def _queue_when_approved(result: dict) -> dict:
    """Hand an ``Approved`` order to its own job; anything else waits for a human in the ERP."""
    from brazil_module.services.banking.payment_service import enqueue_payment_execution

    order_name = result["payment_order"]
    order_status = frappe.db.get_value(PAYMENT_ORDER_DOCTYPE, order_name, "status")
    if order_status != "Approved":
        return {**result, "status": "pending_approval", "order_status": order_status}
    # False means a job for this order is already queued or running: it is queued either way.
    enqueue_payment_execution(order_name)
    return {**result, "status": "queued"}


def _schedule_ted_payment(inv: dict) -> dict:
    """TED cannot be automated: Banco Inter's Banking API has no TED endpoint."""
    return {
        "status": "error",
        "invoice": inv["name"],
        "error": "TED is not available (Banco Inter's Banking API has no TED endpoint). Pay by PIX or boleto.",
    }


def _handle_credit_card_payment(inv: dict) -> dict:
    """Handle credit card payment -- create and submit Payment Entry immediately."""
    pe_name = _create_payment_entry_draft(inv, "Credit Card")
    pe = frappe.get_doc("Payment Entry", pe_name)
    pe.submit()

    return {
        "status": "credit_card",
        "invoice": inv["name"],
        "supplier": inv.get("supplier_name", ""),
        "amount": float(inv["outstanding_amount"]),
        "payment_entry": pe_name,
    }


def _create_payment_entry_draft(inv: dict, mode: str) -> str:
    """Create a Payment Entry draft (credit-card branch only: PIX and boleto go through an order)."""
    invoice = frappe.get_doc("Purchase Invoice", inv["name"])

    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Pay"
    pe.company = invoice.company
    pe.party_type = "Supplier"
    pe.party = invoice.supplier
    pe.paid_amount = float(inv["outstanding_amount"])
    pe.received_amount = pe.paid_amount
    pe.reference_no = inv["name"]
    pe.reference_date = inv.get("due_date") or frappe.utils.today()
    pe.mode_of_payment = mode
    pe.paid_to = invoice.credit_to

    gl_account = _inter_gl_account(invoice.company)
    if gl_account:
        pe.paid_from = gl_account

    pe.append("references", {
        "reference_doctype": "Purchase Invoice",
        "reference_name": inv["name"],
        "allocated_amount": pe.paid_amount,
    })

    pe.insert(ignore_permissions=True)
    return pe.name


def _inter_gl_account(company: str) -> str | None:
    """GL account behind the company's Inter account, when there is one."""
    return bank_gl_account(get_inter_account_for_company(company))


def _send_payment_summary(results: list) -> None:
    """Send the weekly scheduling summary via Telegram: what was queued, what waits, what was left out."""
    lines = ["*Agendamento de pagamentos (semanal):*\n"]
    lines += _order_lines(
        results, "queued", "Na fila de execucao",
        "Cada ordem roda em seu proprio job e avisa o resultado. Aprove no banco quando ele pedir.",
    )
    lines += _order_lines(
        results, "pending_approval", "Aguardando aprovacao no ERP",
        "Nada foi enviado ao banco: aprove e execute cada ordem no ERP.",
    )
    lines += _credit_card_lines(results)
    lines += _reason_lines(results, "skipped", "Ignorados", "reason")
    lines += _reason_lines(results, "error", "Erros", "error")

    if len(lines) == 1:
        lines.append("Nenhum pagamento para esta semana.")

    _notify_telegram("\n".join(lines))


def _order_lines(results: list, status: str, title: str, hint: str) -> list:
    orders = [r for r in results if r.get("status") == status]
    if not orders:
        return []
    total = sum(o["amount"] for o in orders)
    lines = [f"*{title}: {len(orders)} — R$ {total:,.2f}*"]
    for o in orders:
        supplier = (o.get("supplier") or "")[:30]
        state = f" [{o['order_status']}]" if o.get("order_status") else ""
        lines.append(
            f"  - {o['invoice']}: {supplier} R$ {o['amount']:,.2f} "
            f"({o['method']}, venc. {o['due_date']}) -> {o['payment_order']}{state}"
        )
    lines.append(f"  {hint}\n")
    return lines


def _credit_card_lines(results: list) -> list:
    credit_card = [r for r in results if r.get("status") == "credit_card"]
    if not credit_card:
        return []
    total = sum(c["amount"] for c in credit_card)
    lines = [f"*Cartao de credito: {len(credit_card)} — R$ {total:,.2f} (baixa automatica)*"]
    for c in credit_card:
        supplier = (c.get("supplier") or "")[:30]
        lines.append(f"  - {c['invoice']}: {supplier} R$ {c['amount']:,.2f}")
    lines.append("")
    return lines


def _reason_lines(results: list, status: str, title: str, key: str) -> list:
    entries = [r for r in results if r.get("status") == status]
    if not entries:
        return []
    lines = [f"*{title}: {len(entries)}*"]
    for entry in entries:
        text = str(entry.get(key) or "Unknown")[:SUMMARY_TEXT_LIMIT]
        lines.append(f"  - {entry['invoice']}: {text}")
    lines.append("")
    return lines


def _notify_telegram(message: str) -> None:
    """Send a notification via Telegram."""
    try:
        from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot
        bot = TelegramBot()
        chat_id = frappe.db.get_single_value("I8 Agent Settings", "telegram_chat_id")
        if chat_id:
            bot.send_message(chat_id, message)
    except Exception as e:
        frappe.log_error(str(e), "I8 Planning Loop Notification Error")
