from datetime import date, timedelta

import frappe


def scheduled_briefing():
    """Scheduled job: send daily briefing via Telegram if enabled."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    if not frappe.db.get_single_value("I8 Agent Settings", "briefing_enabled"):
        return

    briefing = build_briefing()
    _send_via_telegram(briefing)


def build_briefing() -> str:
    """Build the daily briefing message with key metrics."""
    today = date.today()
    sections = [
        f"*Daily Briefing — {today.strftime('%d/%m/%Y')}*\n",
        _bank_balance_section(),
        _receivables_section(today),
        _payables_section(today),
        _pending_actions_section(),
        _recurring_expenses_section(),
        _agent_cost_section(today),
    ]
    return "\n".join(s for s in sections if s)


def _bank_balance_section() -> str:
    """Bank account balances."""
    accounts = frappe.get_all(
        "Bank Account",
        filters={"is_company_account": 1},
        fields=["name", "account_name", "bank_balance"],
        limit=5,
    )
    if not accounts:
        return ""
    lines = ["*Saldo Bancario:*"]
    for acc in accounts:
        balance = float(acc.get("bank_balance") or 0)
        lines.append(f"  {acc['account_name']}: R$ {balance:,.2f}")
    return "\n".join(lines)


def _receivables_section(today: date) -> str:
    """Sales Invoices: outstanding amounts."""
    # Due today
    due_today = frappe.get_all(
        "Sales Invoice",
        filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": today.isoformat()},
        fields=["SUM(outstanding_amount) as total"],
    )
    total_today = float(due_today[0].get("total") or 0) if due_today else 0

    # Overdue
    overdue = frappe.get_all(
        "Sales Invoice",
        filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": ["<", today.isoformat()]},
        fields=["SUM(outstanding_amount) as total", "COUNT(name) as count"],
    )
    total_overdue = float(overdue[0].get("total") or 0) if overdue else 0
    count_overdue = int(overdue[0].get("count") or 0) if overdue else 0

    lines = ["*Contas a Receber:*"]
    if total_today > 0:
        lines.append(f"  Vencendo hoje: R$ {total_today:,.2f}")
    if total_overdue > 0:
        lines.append(f"  Vencido: R$ {total_overdue:,.2f} ({count_overdue} faturas)")
    if total_today == 0 and total_overdue == 0:
        lines.append("  Nenhuma pendencia")
    return "\n".join(lines)


def _payables_section(today: date) -> str:
    """Purchase Invoices: outstanding amounts."""
    next_7 = (today + timedelta(days=7)).isoformat()

    # Due in next 7 days
    upcoming = frappe.get_all(
        "Purchase Invoice",
        filters={
            "docstatus": 1,
            "outstanding_amount": [">", 0],
            "due_date": ["between", [today.isoformat(), next_7]],
        },
        fields=["SUM(outstanding_amount) as total", "COUNT(name) as count"],
    )
    total_upcoming = float(upcoming[0].get("total") or 0) if upcoming else 0
    count_upcoming = int(upcoming[0].get("count") or 0) if upcoming else 0

    # Overdue
    overdue = frappe.get_all(
        "Purchase Invoice",
        filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": ["<", today.isoformat()]},
        fields=["SUM(outstanding_amount) as total", "COUNT(name) as count"],
    )
    total_overdue = float(overdue[0].get("total") or 0) if overdue else 0
    count_overdue = int(overdue[0].get("count") or 0) if overdue else 0

    lines = ["*Contas a Pagar:*"]
    if total_upcoming > 0:
        lines.append(f"  Proximos 7 dias: R$ {total_upcoming:,.2f} ({count_upcoming} faturas)")
    if total_overdue > 0:
        lines.append(f"  Vencido: R$ {total_overdue:,.2f} ({count_overdue} faturas)")
    if total_upcoming == 0 and total_overdue == 0:
        lines.append("  Nenhuma pendencia")
    return "\n".join(lines)


def _pending_actions_section() -> str:
    """Pending I8 approvals and NFs without invoice."""
    pending_approvals = frappe.db.count("I8 Decision Log", {"result": "Pending", "docstatus": 0})

    nf_pending = frappe.db.count(
        "Nota Fiscal",
        {"invoice_status": ["in", ["Pending", "New", ""]], "processing_status": ["!=", "Cancelled"]},
    )

    lines = ["*Pendencias:*"]
    if pending_approvals > 0:
        lines.append(f"  Aprovacoes pendentes: {pending_approvals}")
    if nf_pending > 0:
        lines.append(f"  NFs sem fatura: {nf_pending}")
    if pending_approvals == 0 and nf_pending == 0:
        lines.append("  Nenhuma pendencia")
    return "\n".join(lines)


def _recurring_expenses_section() -> str:
    """Recurring expenses due in the next 7 days."""
    today = date.today()
    next_7 = today + timedelta(days=7)

    due_soon = frappe.get_all(
        "I8 Recurring Expense",
        filters={
            "active": 1,
            "next_due": ["between", [today.isoformat(), next_7.isoformat()]],
        },
        fields=["title", "estimated_amount", "next_due"],
        order_by="next_due asc",
    )
    if not due_soon:
        return ""

    lines = ["*Despesas Recorrentes (proximos 7 dias):*"]
    for exp in due_soon:
        lines.append(f"  {exp['title']}: R$ {float(exp['estimated_amount']):,.2f} (vence {exp['next_due']})")
    return "\n".join(lines)


def _agent_cost_section(today: date) -> str:
    """Yesterday's I8 agent costs."""
    yesterday = (today - timedelta(days=1)).isoformat()
    result = frappe.db.sql(
        "SELECT COALESCE(SUM(cost_usd), 0) as total, COUNT(*) as calls "
        "FROM `tabI8 Cost Log` WHERE DATE(timestamp) = %s",
        (yesterday,),
        as_dict=True,
    )
    total = float(result[0]["total"]) if result else 0
    calls = int(result[0]["calls"]) if result else 0

    if calls == 0:
        return "*Custo I8 ontem:* Nenhuma chamada"
    return f"*Custo I8 ontem:* USD {total:.4f} ({calls} chamadas)"


def _send_via_telegram(message: str) -> None:
    """Send the briefing via Telegram."""
    try:
        from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot
        bot = TelegramBot()
        chat_id = frappe.db.get_single_value("I8 Agent Settings", "telegram_chat_id")
        if chat_id:
            bot.send_message(chat_id, message)
    except Exception as e:
        frappe.log_error(str(e), "I8 Daily Briefing Error")
