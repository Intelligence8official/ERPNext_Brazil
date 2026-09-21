import re
from datetime import date, datetime, timedelta, time as dt_time

import frappe

from brazil_module.services.intelligence.daily_window import is_due


# Cache key for the "briefing already sent today" dedup marker. Written by
# scheduled_briefing() ONLY after a successful send (never before), so that a
# failed send is retried on the next scheduler tick instead of being latched
# off for the whole day.
_BRIEFING_SENT_KEY = "i8_last_briefing_date"

# Inter Payment Orders that still need a human (see _payment_orders_section).
PAYMENT_ORDERS_TITLE = "*Pagamentos Inter — precisam de atencao:*"
PAYMENT_ORDERS_PER_GROUP = 8
_PAYMENT_ORDER_FIELDS = [
    "name", "status", "amount", "bank_status", "bank_request_at", "modified", "purchase_invoice",
]
_IDLE_ORDER_STATUSES = ["Draft", "Pending Approval", "Approved"]
_ONE_DAY = timedelta(hours=24)
_PAYMENT_ORDER_LINE = re.compile(r"^\s+- (.+?): R\$ ", re.MULTILINE)  # a line of _payment_group_lines


JARVIS_PERSONALITY = """You are I8Operator, the AI financial assistant for Intelligence8.
Your personality: professional yet warm, subtly witty like the original JARVIS from Iron Man.
You address the user by their first name. You are their trusted right hand for ERP operations.

Format the daily briefing data below into a natural, conversational Telegram message.
Rules:
- Start with a warm greeting using the user's name and mention the day/date in Portuguese
- Use a natural flow — don't just list items mechanically
- Highlight what needs attention (overdue payments, pending approvals) with appropriate urgency
- The "Pagamentos Inter" section is about real money that may be in flight: reproduce
  every order of it (name, amount, status, age) exactly — never summarise, merge or drop one
- For support tickets ("Chamados da Plataforma"), lead with the ones that have been
  waiting on us the longest, and flag any whose subject suggests the customer is
  blocked (login, payment, error, outage). Keep suggestions (Feature Requests) to one line
- If everything is fine, be reassuring
- Add subtle personality — a light observation or encouragement
- Use Markdown formatting (bold, italic) for Telegram
- Keep it concise but complete — max 2500 chars. Never drop a section to fit:
  shorten the lines instead, and keep every section heading that the data has
- Write entirely in Brazilian Portuguese
- Sign off as "I8Operator" at the end
"""


def scheduled_briefing():
    """Scheduled job: send daily briefing via Telegram if enabled.

    Runs every 15 minutes via scheduler. Checks if current time is within
    the 15-minute window of the configured briefing_time and if briefing
    hasn't already been sent today.
    """
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    if not frappe.db.get_single_value("I8 Agent Settings", "briefing_enabled"):
        return

    if not _is_briefing_time():
        return

    today = date.today()
    try:
        sent, formatted = _compose_and_send()
    except Exception as e:
        # Do NOT mark as sent: leaving the dedup unset lets the next */15 tick
        # retry, instead of latching the briefing off for the whole day.
        frappe.log_error(str(e), "I8 Daily Briefing Error")
        return

    # Mark as sent ONLY after a successful send, so a transient failure retries.
    if sent:
        frappe.cache.set_value(
            _BRIEFING_SENT_KEY, today.strftime("%Y-%m-%d"), expires_in_sec=86400
        )
        if formatted:
            _send_payment_orders_left_out(formatted)


def _compose_and_send() -> tuple[bool, str]:
    """Build the briefing and hand it to Telegram. Returns ``(sent, formatted)``."""
    today = date.today()
    raw_data = build_briefing()
    user_name = _get_user_first_name()
    buttons = _build_briefing_buttons(today)
    # Use LLM to format the briefing with JARVIS personality
    formatted = _format_with_jarvis(raw_data, user_name, today)
    return _send_via_telegram(formatted or raw_data, buttons), formatted


def send_briefing_now() -> dict:
    """Send the briefing this instant, whatever the clock says, and report what happened.

    The button in the settings form used to queue the scheduled job, which begins by checking the
    configured window and whether today's briefing already went out. Pressed at any other hour it
    therefore did nothing at all - while the form announced that the briefing had been sent.
    """
    try:
        sent, formatted = _compose_and_send()
    except Exception as e:
        frappe.log_error(title="I8 Daily Briefing Error", message=str(e))
        return {"status": "error", "message": f"O briefing falhou: {e}"}
    if not sent:
        return {
            "status": "error",
            "message": "O Telegram nao aceitou a mensagem. Confira telegram_chat_id e o Error Log.",
        }
    if formatted:
        _send_payment_orders_left_out(formatted)
    return {"status": "sent", "message": "Briefing enviado ao Telegram."}


def _is_briefing_time() -> bool:
    """Return True if now is within the 15-minute window after the configured
    briefing_time AND the briefing has not already been sent today.

    This is a READ-ONLY check. The "sent today" marker (_BRIEFING_SENT_KEY) is
    written by scheduled_briefing() only after a successful send, so a failed
    send is retried on the next scheduler tick rather than suppressed for 24h.
    """
    return is_due("briefing_time", _BRIEFING_SENT_KEY, default="08:00:00", now=datetime.now())


def build_briefing() -> str:
    """Build the daily briefing message with key metrics.

    Monday: full briefing with 7-day payables, recurring expenses, and 30-day cash flow.
    Tue-Sun: compact briefing with today's payables only.

    Each section is wrapped in try/except so a failure in one section
    doesn't prevent the rest of the briefing from being sent.
    """
    from brazil_module.services.intelligence.analytics.support_tickets import support_tickets_section

    today = date.today()
    is_monday = today.weekday() == 0

    section_funcs = [
        lambda: f"*Daily Briefing — {today.strftime('%d/%m/%Y')} ({'Segunda' if is_monday else _weekday_name(today)})*\n",
        _banking_health_section,
        _bank_balance_section,
        _reconciliation_status_section,
        lambda: _payables_section(today, is_monday),
        _payment_orders_section,
        _pending_actions_section,
        lambda: support_tickets_section(is_monday),
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


BANKING_HEALTH_TITLE = "*Comunicacao bancaria:*"


def _banking_health_section() -> str:
    """What the daily watchman last found, repeated every day - unlike a single alert.

    Empty while the channel is healthy. First, because the answer to "is any of this current?"
    decides how to read the balance below it. A failure is reported IN the section rather than
    raised: build_briefing() drops a section that raises, and this one vanishing would read as
    "nothing to worry about" - which is exactly what five silent months already looked like.
    """
    try:
        verdict = banking_health_status()
        if not verdict.get("state") or verdict["state"] == "ok":
            return ""
        lines = [BANKING_HEALTH_TITLE]
        lines += [f"  {part.strip()}" for part in str(verdict.get("summary") or "").split("|") if part.strip()]
        checked = verdict.get("checked_on")
        if checked:
            lines.append(f"  (verificado em {frappe.utils.format_datetime(checked, 'dd/MM HH:mm')})")
        return "\n".join(lines)
    except Exception as e:
        try:
            frappe.log_error(title="I8 Briefing Banking Health Error", message=str(e))
        except Exception:
            pass
        return f"{BANKING_HEALTH_TITLE}\n  Nao foi possivel ler a saude da conexao (ver Error Log)"


def banking_health_status() -> dict:
    """Indirection on purpose: the section is tested against a reader that can be made to fail."""
    from brazil_module.services.banking.banking_health import status

    return status()


def _balance_age(balance_date) -> str:
    """A balance is only news while it is recent; an old one has to say how old it is.

    The age is an annotation, the balance is the information: if the date cannot be read, print it
    as it is rather than let the whole line disappear into the caller's except.
    """
    if not balance_date:
        return "sem data"
    try:
        as_date = frappe.utils.getdate(balance_date)
        days = (now_datetime().date() - as_date).days
        if days <= 0:
            return str(as_date)
        return f"{as_date}, ha {days} dias"
    except Exception:
        return str(balance_date)


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
                lines.append(f"  Inter ({acc['company']}): R$ {balance:,.2f} ({_balance_age(acc.get('balance_date'))})")
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


def _payment_orders_section(now: datetime | None = None) -> str:
    """Inter Payment Orders that still need a human - repeated every day, unlike a single alert.

    Empty when there is nothing to report. A failure is reported IN the section instead of
    raised: build_briefing() drops a section that raises, and a payment section that quietly
    disappears reads as "nothing to worry about".
    """
    try:
        moment = now or frappe.utils.now_datetime()
        lines = []
        for title, orders, describe in _payment_order_groups(moment):
            lines += _payment_group_lines(title, orders, describe, moment)
        return "\n".join([PAYMENT_ORDERS_TITLE, *lines]) if lines else ""
    except Exception as e:
        try:
            frappe.log_error(title="I8 Briefing Payment Orders Error", message=str(e))
        except Exception:
            # Writing the Error Log is a database write too: the section still has to come back.
            pass
        return f"{PAYMENT_ORDERS_TITLE}\n  Nao foi possivel ler as ordens de pagamento (ver Error Log)"


def _send_payment_orders_left_out(formatted: str) -> None:
    """Send the payment section verbatim when the formatted briefing lost one of its orders.

    The briefing is rewritten by an LLM and the prompt is a request, not a guarantee. This is
    the part that does not depend on it: every order named in the section must be named in
    what was sent, or the section goes out again, untouched. Best effort - never raises.
    """
    try:
        section = _payment_orders_section()
        if section and not _carries_every_order(section, formatted):
            _send_via_telegram(section)
    except Exception as e:
        try:
            frappe.log_error(title="I8 Briefing Payment Orders Error", message=str(e))
        except Exception:
            pass


def _carries_every_order(section: str, formatted: str) -> bool:
    """True when every order line of the section has its name, whole, in the formatted text."""
    names = _PAYMENT_ORDER_LINE.findall(section)
    if not names:  # "could not read the orders": nothing to match, so it is never assumed kept
        return False
    return all(re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", formatted) for name in names)


def _payment_order_groups(now: datetime) -> list:
    """``(title, orders, describe)`` per group, most urgent first. Five queries, none per order."""
    from brazil_module.services.banking.payment_guards import DOCTYPE

    cutoff = now - _ONE_DAY

    def read(filters: dict) -> list:
        return frappe.get_all(DOCTYPE, filters=filters, fields=_PAYMENT_ORDER_FIELDS, order_by="modified asc")

    # Every poll bumps `modified`: how long the bank has had an order is `bank_request_at`.
    waiting = [
        order for order in read({"docstatus": 1, "status": "Awaiting Bank"})
        if _order_since(order) is None or _order_since(order) <= cutoff
    ]
    return [
        ("Verificacao necessaria (o banco pode ter o pagamento)",
         read({"docstatus": 1, "status": "Needs Verification"}), _describe_with_invoice),
        ("Aguardando o banco ha mais de 24h", waiting, _describe_with_bank_status),
        ("Falharam nas ultimas 24h",
         read({"docstatus": 1, "status": "Failed", "modified": [">=", cutoff]}), _describe_failed),
        ("Pagos sem Payment Entry (use Create Payment Entry na ordem)",
         read({"docstatus": 1, "status": "Completed", "payment_entry": ["is", "not set"]}), _describe_failed),
        ("Parados ha mais de 24h (bloqueiam a fatura)",
         read({"docstatus": ["<", 2], "status": ["in", _IDLE_ORDER_STATUSES], "modified": ["<", cutoff]}),
         _describe_with_status),
    ]


def _payment_group_lines(title: str, orders: list, describe, now: datetime) -> list:
    """One group: its count, the oldest orders first, and how many were left out."""
    if not orders:
        return []
    oldest_first = sorted(orders, key=lambda order: _order_since(order) or datetime.min)
    lines = [f"  *{title}: {len(orders)}*"]
    for order in oldest_first[:PAYMENT_ORDERS_PER_GROUP]:
        amount = float(order.get("amount") or 0)
        lines.append(f"    - {order.get('name')}: R$ {amount:,.2f}{describe(order, now)}")
    if len(orders) > PAYMENT_ORDERS_PER_GROUP:
        lines.append(f"    ... e mais {len(orders) - PAYMENT_ORDERS_PER_GROUP}")
    return lines


def _describe_with_invoice(order: dict, now: datetime) -> str:
    invoice = order.get("purchase_invoice")
    return f" — {_order_age(order, now)}{f' ({invoice})' if invoice else ''}"


def _describe_with_bank_status(order: dict, now: datetime) -> str:
    return f" — {order.get('bank_status') or 'sem status do banco'}, {_order_age(order, now)}"


def _describe_failed(order: dict, now: datetime) -> str:
    invoice = order.get("purchase_invoice")
    return f" ({invoice})" if invoice else ""


def _describe_with_status(order: dict, now: datetime) -> str:
    return f" — {order.get('status')}, {_order_age(order, now)}"


def _order_since(order: dict) -> datetime | None:
    """When the bank received the order; before that (or without it), when it last changed."""
    return _as_datetime(order.get("bank_request_at")) or _as_datetime(order.get("modified"))


def _order_age(order: dict, now: datetime) -> str:
    since = _order_since(order)
    if since is None:
        return "data desconhecida"
    days = (now - since).days
    if days <= 0:
        return "hoje"
    return "ha 1 dia" if days == 1 else f"ha {days} dias"


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, dt_time())
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


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
    """Build inline keyboard buttons for actionable items in the briefing.

    Fully guarded: a DB error here must never propagate into scheduled_briefing()
    (it is the only call in the send path that is not otherwise wrapped).
    """
    try:
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
    except Exception as e:
        frappe.log_error(str(e), "I8 Briefing Buttons Error")
        return None


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
        stale = _statement_age()
        if stale:
            # Counting old transactions that are all reconciled is how a dead channel reports
            # "em dia": say the extract stopped instead.
            lines.append(f"  Sem extrato novo ha {stale} dias - os numeros abaixo sao antigos")
            lines.append(f"  {reconciled}/{total} transacoes conciliadas ({pct:.0f}%)")
            if unreconciled:
                lines.append(f"  {unreconciled} transacoes pendentes")
            return "\n".join(lines)
        if unreconciled == 0:
            lines.append("  Em dia (100% conciliado)")
        else:
            lines.append(f"  {reconciled}/{total} transacoes conciliadas ({pct:.0f}%)")
            lines.append(f"  {unreconciled} transacoes pendentes")
        return "\n".join(lines)
    except Exception:
        return ""


def _statement_age() -> int:
    """Days since the newest statement sync, or 0 while it is recent enough to trust."""
    from brazil_module.services.banking.banking_health import SYNC_STALE_DAYS

    synced = [
        row.get("last_statement_sync")
        for row in frappe.get_all("Inter Company Account", filters={"sync_enabled": 1},
                                  fields=["last_statement_sync"])
    ]
    newest = max((frappe.utils.get_datetime(when) for when in synced if when), default=None)
    if newest is None:
        return 0  # never synced, or no account: _banking_health_section already says it
    days = (now_datetime() - newest).days
    return days if days >= SYNC_STALE_DAYS else 0


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
    """Format the briefing in the I8Operator voice, on the fast tier of
    whichever provider this site is pointed at."""
    try:
        from brazil_module.services.intelligence.llm.client import LLM

        return LLM().ask(
            system=JARVIS_PERSONALITY,
            prompt=(
                f"User name: {user_name}\n"
                f"Today: {today.strftime('%A, %d de %B de %Y')} "
                f"({_weekday_name(today)}, {today.strftime('%d/%m/%Y')})\n\n"
                f"Raw briefing data:\n{raw_data}"
            ),
            tier="fast",
            max_tokens=3000,
            module="briefing",
            function_name="jarvis_format",
        ).strip()

    except Exception as e:
        frappe.log_error(str(e), "I8 JARVIS Briefing Format Error")
        return None


def _send_via_telegram(message: str, reply_markup: dict | None = None) -> bool:
    """Send the briefing via Telegram. Returns True only if the message was sent.

    The return value drives the dedup marker in scheduled_briefing(): a False
    return (no chat configured, or the Telegram API failed) leaves the briefing
    un-marked so the next scheduler tick retries.
    """
    try:
        from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot
        bot = TelegramBot()
        chat_id = frappe.db.get_single_value("I8 Agent Settings", "telegram_chat_id")
        if not chat_id:
            return False
        result = bot.send_message(chat_id, message, reply_markup)
    except Exception as e:
        frappe.log_error(str(e), "I8 Daily Briefing Error")
        return False

    # Telegram rejected the send (e.g. invalid bot token → {"ok": false}).
    # send_message() already logged it; return False so the briefing retries
    # on the next tick instead of being marked sent for the day.
    if not (isinstance(result, dict) and result.get("ok")):
        return False

    # Telegram send succeeded — the best-effort desk notification must not
    # affect the success result (a failure here would otherwise force a resend).
    try:
        from brazil_module.services.intelligence.notifications import notify_desk
        notify_desk(
            title="I8-Operator Daily Briefing",
            message="Briefing diario enviado ao Telegram por I8-Operator",
        )
    except Exception as e:
        frappe.log_error(str(e), "I8 Desk Notify Error")
    return True
