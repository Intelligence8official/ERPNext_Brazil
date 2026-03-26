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


def run_reconciliation():
    """Auto-reconcile unmatched bank transactions for all Inter accounts."""
    try:
        accounts = frappe.get_all(
            "Bank Account",
            filters={"is_company_account": 1},
            fields=["name"],
        )

        total_matched = 0
        total_errors = 0

        for account in accounts:
            try:
                from brazil_module.services.banking.reconciliation import batch_reconcile
                result = batch_reconcile(account["name"])
                matched = result.get("matched", 0)
                errors = result.get("errors", 0)
                total_matched += matched
                total_errors += errors

                if matched > 0:
                    frappe.logger().info(
                        f"I8 Auto-Reconciliation: {account['name']} - "
                        f"{matched} matched, {errors} errors"
                    )
            except Exception as e:
                total_errors += 1
                frappe.log_error(str(e), f"I8 Reconciliation Error: {account['name']}")

        # Notify via Telegram if any matches were made
        if total_matched > 0:
            _notify_telegram(
                f"Conciliacao automatica: {total_matched} transacoes conciliadas"
                f"{f', {total_errors} erros' if total_errors else ''}"
            )

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(str(e), "I8 Planning Loop: Reconciliation Error")


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
            fields=["name", "cnpj_emitente"],
            limit=10,
        )

        if not pending_nfs:
            _notify_telegram("Nenhuma NF pendente para processar.")
            return

        processed = 0
        for nf in pending_nfs:
            try:
                frappe.enqueue(
                    "brazil_module.services.intelligence.agent.process_single_event",
                    queue="long",
                    job_id=f"i8:nf_process:{nf['name']}",
                    event_type="nf_received",
                    event_id=nf["name"],
                    event_data={
                        "module": "fiscal",
                        "nota_fiscal": nf["name"],
                        "supplier": nf.get("cnpj_emitente", ""),
                    },
                    deduplicate=True,
                )
                processed += 1
            except Exception as e:
                frappe.log_error(str(e), f"I8 NF Processing Error: {nf['name']}")

        _notify_telegram(f"Enfileiradas {processed} NFs para processamento pelo agente.")
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(str(e), "I8 Planning Loop: NF Processing Error")


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
