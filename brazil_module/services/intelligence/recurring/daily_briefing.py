from datetime import date, timedelta

import anthropic
import frappe


JARVIS_PERSONALITY = """You are J.A.R.V.I.S., the AI financial assistant for Intelligence8.
Your personality: professional yet warm, subtly witty like the original JARVIS from Iron Man.
You address the user by their first name. You are their trusted right hand for ERP operations.

Format the daily briefing data below into a natural, conversational Telegram message.
Rules:
- Start with a warm greeting using the user's name and mention the day/date in Portuguese
- Use a natural flow — don't just list items mechanically
- Highlight what needs attention (overdue payments, pending approvals) with appropriate urgency
- If everything is fine, be reassuring
- Add subtle personality — a light observation or encouragement
- Use Markdown formatting (bold, italic) for Telegram
- Keep it concise but complete — max 2000 chars
- Write entirely in Brazilian Portuguese
- Sign off as "J.A.R.V.I.S." at the end
"""


def scheduled_briefing():
    """Scheduled job: send daily briefing via Telegram if enabled."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    if not frappe.db.get_single_value("I8 Agent Settings", "briefing_enabled"):
        return

    today = date.today()
    raw_data = build_briefing()
    user_name = _get_user_first_name()
    buttons = _build_briefing_buttons(today)

    # Use LLM to format the briefing with JARVIS personality
    formatted = _format_with_jarvis(raw_data, user_name, today)
    _send_via_telegram(formatted or raw_data, buttons)


def build_briefing() -> str:
    """Build the daily briefing message with key metrics.

    Monday: full briefing with 7-day payables, recurring expenses, and 30-day cash flow.
    Tue-Sun: compact briefing with today's payables only.

    Each section is wrapped in try/except so a failure in one section
    doesn't prevent the rest of the briefing from being sent.
    """
    today = date.today()
    is_monday = today.weekday() == 0

    section_funcs = [
        lambda: f"*Daily Briefing — {today.strftime('%d/%m/%Y')} ({'Segunda' if is_monday else _weekday_name(today)})*\n",
        _bank_balance_section,
        _reconciliation_status_section,
        lambda: _payables_section(today, is_monday),
        _pending_actions_section,
    ]

    if is_monday:
        section_funcs.append(_recurring_expenses_section)
        section_funcs.append(lambda: _cash_flow_section(today))

    section_funcs.append(lambda: _agent_cost_section(today))

    sections = []
    for func in section_funcs:
        try:
            result = func()
            if result:
                sections.append(result)
        except Exception as e:
            frappe.log_error(str(e), f"I8 Briefing Section Error: {func}")
    return "\n".join(sections)


def _weekday_name(d: date) -> str:
    names = ["Segunda", "Terca", "Quarta", "Quinta", "Sexta", "Sabado", "Domingo"]
    return names[d.weekday()]


def _bank_balance_section() -> str:
    """Bank account balances from Inter API and GL Entry."""
    lines = ["*Saldo Bancario:*"]

    try:
        inter_accounts = frappe.get_all(
            "Inter Company Account",
            filters={},
            fields=["name", "company", "current_balance", "balance_date"],
        )
        for acc in inter_accounts:
            balance = float(acc.get("current_balance") or 0)
            if balance > 0:
                balance_date = acc.get("balance_date") or ""
                lines.append(f"  Inter ({acc['company']}): R$ {balance:,.2f} ({balance_date})")
    except Exception:
        pass

    try:
        gl_balances = frappe.db.sql("""
            SELECT ba.account_name, SUM(gl.debit) - SUM(gl.credit) as balance
            FROM `tabGL Entry` gl
            JOIN `tabBank Account` ba ON ba.account = gl.account
            WHERE ba.is_company_account = 1 AND gl.is_cancelled = 0
            GROUP BY ba.account_name
            ORDER BY balance DESC
        """, as_dict=True)
        for row in gl_balances:
            lines.append(f"  {row['account_name']}: R$ {float(row['balance']):,.2f}")
    except Exception:
        pass

    if len(lines) == 1:
        lines.append("  Nenhuma conta configurada")

    return "\n".join(lines)


def _payables_section(today: date, is_monday: bool) -> str:
    """Purchase Invoices: outstanding amounts.

    Monday: shows next 7 days + overdue with detail per invoice.
    Tue-Sun: shows today only + overdue summary.
    """
    lines = ["*Contas a Pagar:*"]

    # Overdue (always shown)
    overdue = frappe.get_all(
        "Purchase Invoice",
        filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": ["<", today.isoformat()]},
        fields=["name", "supplier_name", "outstanding_amount", "due_date"],
        order_by="due_date asc",
        limit=20,
    )
    if overdue:
        total_overdue = sum(float(inv.get("outstanding_amount") or 0) for inv in overdue)
        lines.append(f"  *Vencido:* R$ {total_overdue:,.2f} ({len(overdue)} faturas)")
        for inv in overdue[:5]:
            supplier = (inv.get("supplier_name") or "")[:30]
            lines.append(f"    - {inv['name']}: {supplier} R$ {float(inv['outstanding_amount']):,.2f} (venc. {inv['due_date']})")
        if len(overdue) > 5:
            lines.append(f"    ... e mais {len(overdue) - 5}")

    if is_monday:
        # Monday: next 7 days with detail
        next_7 = (today + timedelta(days=7)).isoformat()
        upcoming = frappe.get_all(
            "Purchase Invoice",
            filters={
                "docstatus": 1,
                "outstanding_amount": [">", 0],
                "due_date": ["between", [today.isoformat(), next_7]],
            },
            fields=["name", "supplier_name", "outstanding_amount", "due_date"],
            order_by="due_date asc",
            limit=20,
        )
        if upcoming:
            total_upcoming = sum(float(inv.get("outstanding_amount") or 0) for inv in upcoming)
            lines.append(f"  *Proximos 7 dias:* R$ {total_upcoming:,.2f} ({len(upcoming)} faturas)")
            for inv in upcoming:
                supplier = (inv.get("supplier_name") or "")[:30]
                lines.append(f"    - {inv['name']}: {supplier} R$ {float(inv['outstanding_amount']):,.2f} (venc. {inv['due_date']})")
        elif not overdue:
            lines.append("  Nenhum pagamento nos proximos 7 dias")
    else:
        # Tue-Sun: today only
        due_today = frappe.get_all(
            "Purchase Invoice",
            filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": today.isoformat()},
            fields=["name", "supplier_name", "outstanding_amount"],
            order_by="outstanding_amount desc",
            limit=10,
        )
        if due_today:
            total_today = sum(float(inv.get("outstanding_amount") or 0) for inv in due_today)
            lines.append(f"  *Vencendo hoje:* R$ {total_today:,.2f} ({len(due_today)} faturas)")
            for inv in due_today:
                supplier = (inv.get("supplier_name") or "")[:30]
                lines.append(f"    - {inv['name']}: {supplier} R$ {float(inv['outstanding_amount']):,.2f}")
        elif not overdue:
            lines.append("  Nenhum pagamento para hoje")

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
    """Recurring expenses due in the next 7 days. Monday only."""
    today = date.today()
    next_7 = today + timedelta(days=7)

    due_soon = frappe.get_all(
        "I8 Recurring Expense",
        filters={
            "active": 1,
            "next_due": ["between", [today.isoformat(), next_7.isoformat()]],
        },
        fields=["title", "estimated_amount", "next_due", "supplier_name"],
        order_by="next_due asc",
    )
    if not due_soon:
        return ""

    lines = ["*Despesas Recorrentes (proximos 7 dias):*"]
    for exp in due_soon:
        supplier = (exp.get("supplier_name") or "")[:25]
        lines.append(
            f"  {exp['title']}: R$ {float(exp['estimated_amount']):,.2f}"
            f" (vence {exp['next_due']})"
            f"{f' - {supplier}' if supplier else ''}"
        )
    return "\n".join(lines)


def _cash_flow_section(today: date) -> str:
    """30-day cash flow projection. Monday only.

    Considers:
    - Current bank balance (GL)
    - Outstanding Purchase Invoices (payables)
    - Outstanding Sales Invoices (receivables)
    - Active recurring expenses not yet invoiced
    """
    next_30 = today + timedelta(days=30)

    # Current balance from GL
    try:
        gl_result = frappe.db.sql("""
            SELECT SUM(gl.debit) - SUM(gl.credit) as balance
            FROM `tabGL Entry` gl
            JOIN `tabBank Account` ba ON ba.account = gl.account
            WHERE ba.is_company_account = 1 AND gl.is_cancelled = 0
        """, as_dict=True)
        current_balance = float(gl_result[0]["balance"]) if gl_result and gl_result[0]["balance"] else 0
    except Exception:
        current_balance = 0

    # Receivables due in next 30 days
    try:
        recv = frappe.db.sql("""
            SELECT COALESCE(SUM(outstanding_amount), 0) as total
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND outstanding_amount > 0
            AND due_date BETWEEN %s AND %s
        """, (today.isoformat(), next_30.isoformat()), as_dict=True)
        total_receivable = float(recv[0]["total"]) if recv else 0
    except Exception:
        total_receivable = 0

    # Payables due in next 30 days
    try:
        paybl = frappe.db.sql("""
            SELECT COALESCE(SUM(outstanding_amount), 0) as total
            FROM `tabPurchase Invoice`
            WHERE docstatus = 1 AND outstanding_amount > 0
            AND due_date BETWEEN %s AND %s
        """, (today.isoformat(), next_30.isoformat()), as_dict=True)
        total_payable = float(paybl[0]["total"]) if paybl else 0
    except Exception:
        total_payable = 0

    # Recurring expenses for next 30 days (not yet invoiced)
    try:
        recurring = frappe.get_all(
            "I8 Recurring Expense",
            filters={
                "active": 1,
                "next_due": ["between", [today.isoformat(), next_30.isoformat()]],
            },
            fields=["estimated_amount"],
        )
        total_recurring = sum(float(r.get("estimated_amount") or 0) for r in recurring)
    except Exception:
        total_recurring = 0

    total_outflow = total_payable + total_recurring
    projected_balance = current_balance + total_receivable - total_outflow

    lines = [
        "*Fluxo de Caixa (30 dias):*",
        f"  Saldo atual: R$ {current_balance:,.2f}",
        f"  (+) A receber: R$ {total_receivable:,.2f}",
        f"  (-) A pagar (faturas): R$ {total_payable:,.2f}",
    ]
    if total_recurring > 0:
        lines.append(f"  (-) Despesas recorrentes: R$ {total_recurring:,.2f}")
    lines.append(f"  *Saldo projetado: R$ {projected_balance:,.2f}*")

    if projected_balance < 0:
        lines.append(f"  ⚠ ATENCAO: Saldo projetado negativo!")
    elif projected_balance < 5000:
        lines.append(f"  ⚠ Saldo projetado baixo")

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


def _build_briefing_buttons(today: date) -> dict | None:
    """Build inline keyboard buttons for actionable items in the briefing."""
    buttons = []

    # Pending approvals
    pending = frappe.db.count("I8 Decision Log", {"result": "Pending", "docstatus": 0})
    if pending > 0:
        buttons.append([
            {"text": f"Ver {pending} aprovacoes pendentes", "callback_data": "briefing:list_approvals"},
        ])

    # Overdue payables
    overdue_count = frappe.db.count("Purchase Invoice", {
        "docstatus": 1, "outstanding_amount": [">", 0], "due_date": ["<", today.isoformat()]
    })
    if overdue_count > 0:
        buttons.append([
            {"text": f"Ver {overdue_count} pagamentos vencidos", "callback_data": "briefing:list_overdue"},
        ])

    # NFs pending
    try:
        nf_pending = frappe.db.count("Nota Fiscal", {
            "invoice_status": ["in", ["Pending", "New", ""]],
            "processing_status": ["!=", "Cancelled"],
        })
        if nf_pending > 0:
            buttons.append([
                {"text": f"Processar {nf_pending} NFs pendentes", "callback_data": "briefing:process_nfs"},
            ])
    except Exception:
        pass

    # Reconciliation
    buttons.append([
        {"text": "Executar conciliacao bancaria", "callback_data": "briefing:reconcile"},
    ])

    if not buttons:
        return None

    return {"inline_keyboard": buttons}


def _reconciliation_status_section() -> str:
    """Bank reconciliation status."""
    try:
        unreconciled = frappe.db.count("Bank Transaction", {
            "docstatus": 1,
            "unallocated_amount": [">", 0],
        })
        total = frappe.db.count("Bank Transaction", {"docstatus": 1})

        if total == 0:
            return ""

        reconciled = total - unreconciled
        pct = (reconciled / total * 100) if total > 0 else 0

        lines = ["*Conciliacao Bancaria:*"]
        if unreconciled == 0:
            lines.append("  Em dia (100% conciliado)")
        else:
            lines.append(f"  {reconciled}/{total} transacoes conciliadas ({pct:.0f}%)")
            lines.append(f"  {unreconciled} transacoes pendentes")
        return "\n".join(lines)
    except Exception:
        return ""


def _get_user_first_name() -> str:
    """Get the first name of the primary Telegram user from I8 Agent Settings."""
    try:
        settings = frappe.get_single("I8 Agent Settings")
        for user_row in (settings.telegram_users or []):
            if user_row.active and user_row.user:
                first_name = frappe.db.get_value("User", user_row.user, "first_name")
                if first_name:
                    return first_name
    except Exception:
        pass
    return "chefe"


def _format_with_jarvis(raw_data: str, user_name: str, today: date) -> str | None:
    """Use Haiku to format the briefing with JARVIS personality."""
    try:
        from brazil_module.intelligence8.doctype.i8_agent_settings.i8_agent_settings import I8AgentSettings
        settings = I8AgentSettings.get_settings()

        client = anthropic.Anthropic(api_key=I8AgentSettings.get_api_key())
        response = client.messages.create(
            model=settings.haiku_model or "claude-haiku-4-5-20251001",
            max_tokens=2000,
            system=JARVIS_PERSONALITY,
            messages=[{
                "role": "user",
                "content": (
                    f"User name: {user_name}\n"
                    f"Today: {today.strftime('%A, %d de %B de %Y')} "
                    f"({_weekday_name(today)}, {today.strftime('%d/%m/%Y')})\n\n"
                    f"Raw briefing data:\n{raw_data}"
                ),
            }],
        )

        formatted = response.content[0].text.strip()

        # Log cost
        from brazil_module.services.intelligence.cost_tracker import CostTracker
        tracker = CostTracker()
        tracker.log(
            model=settings.haiku_model or "claude-haiku-4-5-20251001",
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            latency_ms=0,
            module="briefing",
            function_name="jarvis_format",
        )

        return formatted

    except Exception as e:
        frappe.log_error(str(e), "I8 JARVIS Briefing Format Error")
        return None


def _send_via_telegram(message: str, reply_markup: dict | None = None) -> None:
    """Send the briefing via Telegram."""
    try:
        from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot
        bot = TelegramBot()
        chat_id = frappe.db.get_single_value("I8 Agent Settings", "telegram_chat_id")
        if chat_id:
            bot.send_message(chat_id, message, reply_markup)
        from brazil_module.services.intelligence.notifications import notify_desk
        notify_desk(
            title="J.A.R.V.I.S. Daily Briefing",
            message="Briefing diario enviado ao Telegram por J.A.R.V.I.S.",
        )
    except Exception as e:
        frappe.log_error(str(e), "I8 Daily Briefing Error")
