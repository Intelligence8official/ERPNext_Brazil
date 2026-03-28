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
    """Alert via Telegram if there are invoices overdue today."""
    try:
        today = date.today()
        newly_overdue = frappe.get_all(
            "Purchase Invoice",
            filters={
                "docstatus": 1,
                "outstanding_amount": [">", 0],
                "due_date": today.isoformat(),
            },
            fields=["name", "supplier_name", "outstanding_amount"],
            limit=10,
        )

        if newly_overdue:
            total = sum(float(inv.get("outstanding_amount") or 0) for inv in newly_overdue)
            lines = [f"Pagamentos vencendo hoje: R$ {total:,.2f}\n"]
            for inv in newly_overdue:
                supplier = (inv.get("supplier_name") or "")[:30]
                lines.append(f"- {inv['name']}: {supplier} R$ {float(inv['outstanding_amount']):,.2f}")
            _notify_telegram("\n".join(lines))

    except Exception as e:
        frappe.log_error(str(e), "I8 Planning Loop: Overdue Check Error")


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
                frappe.log_error(str(e), f"I8 NF Processing Error: {nf['name']}")

        if errors > 0:
            _notify_telegram(f"NFs enfileiradas: {processed} ok, {errors} erros. O agente processara em background.")
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(str(e), "I8 Planning Loop: NF Processing Error")
        _notify_telegram(f"Erro ao processar NFs: {str(e)[:100]}")


def check_urgent_payments():
    """Alert via Telegram if there are invoices due today or tomorrow without payment scheduled."""
    try:
        today = date.today()
        tomorrow = today + timedelta(days=1)

        urgent = frappe.db.sql("""
            SELECT pi.name, pi.supplier_name, pi.outstanding_amount, pi.due_date
            FROM `tabPurchase Invoice` pi
            WHERE pi.docstatus = 1
            AND pi.outstanding_amount > 0
            AND pi.due_date IN (%s, %s)
            AND NOT EXISTS (
                SELECT 1 FROM `tabPayment Entry Reference` per
                JOIN `tabPayment Entry` pe ON pe.name = per.parent
                WHERE per.reference_name = pi.name
                AND pe.docstatus < 2
            )
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
        frappe.log_error(str(e), "I8 Planning Loop: Urgent Payment Check Error")


def schedule_weekly_payments():
    """Schedule payments for invoices due this week. Runs on configured day."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    if not frappe.db.get_single_value("I8 Agent Settings", "auto_schedule_payments"):
        return

    today = date.today()
    # Calculate end of week (Sunday)
    days_until_sunday = 6 - today.weekday()
    week_end = today + timedelta(days=days_until_sunday)

    # Find outstanding PIs due this week
    invoices = frappe.get_all(
        "Purchase Invoice",
        filters={
            "docstatus": 1,
            "outstanding_amount": [">", 0],
            "due_date": ["between", [today.isoformat(), week_end.isoformat()]],
        },
        fields=["name", "supplier", "supplier_name", "outstanding_amount", "due_date"],
        order_by="due_date asc",
    )

    if not invoices:
        return

    scheduled = []
    errors = []
    credit_card_paid = []

    for inv in invoices:
        try:
            result = _schedule_single_payment(inv)
            if result["status"] == "scheduled":
                scheduled.append(result)
            elif result["status"] == "credit_card":
                credit_card_paid.append(result)
            elif result["status"] == "error":
                errors.append(result)
        except Exception as e:
            errors.append({"invoice": inv["name"], "error": str(e)})

    # Send summary to Telegram
    _send_payment_summary(scheduled, credit_card_paid, errors)
    frappe.db.commit()


def _schedule_single_payment(inv: dict) -> dict:
    """Schedule payment for a single invoice based on its payment method."""
    mode = _get_payment_mode(inv["name"])
    supplier = inv["supplier"]

    if mode in ("Pix", "PIX"):
        return _schedule_pix_payment(inv, supplier)
    elif mode in ("Boleto",):
        return _schedule_boleto_payment(inv)
    elif mode in ("Credit Card",):
        return _handle_credit_card_payment(inv)
    elif mode in ("Wire Transfer", "TED"):
        return _schedule_ted_payment(inv, supplier)
    else:
        return {"status": "skipped", "invoice": inv["name"], "reason": f"Unknown payment mode: {mode}"}


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
    """Schedule a PIX payment via Inter API."""
    pix_key = frappe.db.get_value("Supplier", supplier, "pix_key")
    if not pix_key:
        return {"status": "error", "invoice": inv["name"], "error": "Supplier has no PIX key"}

    try:
        from brazil_module.services.banking.inter_client import InterAPIClient

        inter_account = frappe.get_all(
            "Inter Company Account",
            filters={"enabled": 1},
            fields=["name"],
            limit=1,
        )
        if not inter_account:
            return {"status": "error", "invoice": inv["name"], "error": "No Inter account configured"}

        client = InterAPIClient(inter_account[0]["name"])
        payment_data = {
            "valor": f"{float(inv['outstanding_amount']):.2f}",
            "descricao": f"Payment {inv['name']}",
            "destinatario": {
                "tipo": "CHAVE",
                "chave": pix_key,
            },
            "dataAgendamento": str(inv["due_date"]),
        }

        response = client.send_pix(payment_data)

        pe_name = _create_payment_entry_draft(inv, "Pix", response)

        return {
            "status": "scheduled",
            "invoice": inv["name"],
            "supplier": inv.get("supplier_name", ""),
            "amount": float(inv["outstanding_amount"]),
            "due_date": str(inv["due_date"]),
            "method": "PIX",
            "payment_entry": pe_name,
            "transaction_id": response.get("codigoSolicitacao", ""),
        }
    except Exception as e:
        return {"status": "error", "invoice": inv["name"], "error": str(e)}


def _schedule_boleto_payment(inv: dict) -> dict:
    """Schedule a boleto payment via Inter API."""
    barcode = frappe.db.get_value("Purchase Invoice", inv["name"], "boleto_barcode")
    if not barcode:
        return {"status": "error", "invoice": inv["name"], "error": "Falta linha digitavel do boleto"}

    try:
        from brazil_module.services.banking.inter_client import InterAPIClient

        inter_account = frappe.get_all(
            "Inter Company Account", filters={"enabled": 1}, fields=["name"], limit=1,
        )
        if not inter_account:
            return {"status": "error", "invoice": inv["name"], "error": "No Inter account configured"}

        client = InterAPIClient(inter_account[0]["name"])
        payment_data = {
            "codBarraLinhaDigitavel": barcode,
            "valorPagar": float(inv["outstanding_amount"]),
            "dataPagamento": str(inv["due_date"]),
        }

        response = client.pay_barcode(payment_data)
        pe_name = _create_payment_entry_draft(inv, "Boleto", response)

        return {
            "status": "scheduled",
            "invoice": inv["name"],
            "supplier": inv.get("supplier_name", ""),
            "amount": float(inv["outstanding_amount"]),
            "due_date": str(inv["due_date"]),
            "method": "Boleto",
            "payment_entry": pe_name,
        }
    except Exception as e:
        return {"status": "error", "invoice": inv["name"], "error": str(e)}


def _schedule_ted_payment(inv: dict, supplier: str) -> dict:
    """Schedule a TED payment -- needs bank details, currently just flags for review."""
    return {"status": "error", "invoice": inv["name"], "error": "TED requires bank details (not yet automated)"}


def _handle_credit_card_payment(inv: dict) -> dict:
    """Handle credit card payment -- create and submit Payment Entry immediately."""
    try:
        pe_name = _create_payment_entry_draft(inv, "Credit Card", {})
        pe = frappe.get_doc("Payment Entry", pe_name)
        pe.submit()

        return {
            "status": "credit_card",
            "invoice": inv["name"],
            "supplier": inv.get("supplier_name", ""),
            "amount": float(inv["outstanding_amount"]),
            "payment_entry": pe_name,
        }
    except Exception as e:
        return {"status": "error", "invoice": inv["name"], "error": str(e)}


def _create_payment_entry_draft(inv: dict, mode: str, api_response: dict) -> str:
    """Create a Payment Entry draft for a scheduled payment."""
    invoice = frappe.get_doc("Purchase Invoice", inv["name"])

    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Pay"
    pe.company = invoice.company
    pe.party_type = "Supplier"
    pe.party = invoice.supplier
    pe.paid_amount = float(inv["outstanding_amount"])
    pe.received_amount = pe.paid_amount
    pe.reference_no = api_response.get("codigoSolicitacao", inv["name"])
    pe.reference_date = inv.get("due_date") or frappe.utils.today()
    pe.mode_of_payment = mode
    pe.paid_to = invoice.credit_to

    # Get bank account
    try:
        inter_account = frappe.get_all(
            "Inter Company Account", filters={"enabled": 1}, fields=["name"], limit=1,
        )
        if inter_account:
            bank_account = frappe.db.get_value(
                "Inter Company Account", inter_account[0]["name"], "bank_account",
            )
            if bank_account:
                gl_account = frappe.db.get_value("Bank Account", bank_account, "account")
                if gl_account:
                    pe.paid_from = gl_account
    except Exception:
        pass

    pe.append("references", {
        "reference_doctype": "Purchase Invoice",
        "reference_name": inv["name"],
        "allocated_amount": pe.paid_amount,
    })

    pe.insert(ignore_permissions=True)
    return pe.name


def _send_payment_summary(scheduled: list, credit_card: list, errors: list) -> None:
    """Send payment scheduling summary via Telegram."""
    lines = ["*Agendamento de pagamentos (semanal):*\n"]

    if scheduled:
        total = sum(s["amount"] for s in scheduled)
        lines.append(f"*Agendados: {len(scheduled)} pagamentos — R$ {total:,.2f}*")
        for s in scheduled:
            supplier = (s.get("supplier") or "")[:30]
            lines.append(f"  - {s['invoice']}: {supplier} R$ {s['amount']:,.2f} ({s['method']}, venc. {s['due_date']})")
        lines.append("  Acesse o banco para as devidas aprovacoes.\n")

    if credit_card:
        total = sum(c["amount"] for c in credit_card)
        lines.append(f"*Cartao de credito: {len(credit_card)} — R$ {total:,.2f} (baixa automatica)*")
        for c in credit_card:
            supplier = (c.get("supplier") or "")[:30]
            lines.append(f"  - {c['invoice']}: {supplier} R$ {c['amount']:,.2f}")
        lines.append("")

    if errors:
        lines.append(f"*Erros: {len(errors)}*")
        for e in errors:
            lines.append(f"  - {e['invoice']}: {e.get('error', 'Unknown error')[:60]}")

    if not scheduled and not credit_card and not errors:
        lines.append("Nenhum pagamento para esta semana.")

    _notify_telegram("\n".join(lines))


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
