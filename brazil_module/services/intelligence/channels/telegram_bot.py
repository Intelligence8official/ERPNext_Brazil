"""
Telegram Bot integration for Intelligence8.

Handles:
- Webhook secret validation
- User authorization via I8 Agent Settings telegram_users table
- Incoming updates (messages and callback queries)
- Approval/rejection of I8 Decision Log entries via inline keyboard
- Sending messages and approval requests through the Telegram Bot API
"""
import json
from datetime import date

import requests
import frappe

from brazil_module.services.intelligence.channels.channel_router import ChannelRouter
from brazil_module.services.intelligence.prompts.approval_formatter import format_approval_message


# Action name to human-readable description
_ACTION_LABELS = {
    "p2p-create_purchase_order": "Criar Ordem de Compra",
    "p2p-send_po_to_supplier": "Enviar PO ao Fornecedor",
    "fiscal-create_purchase_invoice": "Criar Fatura de Compra",
    "fiscal-link_nf_to_po": "Vincular NF a PO",
    "banking-create_payment": "Criar Pagamento",
    "email-classify": "Classificar Email",
}


def _format_approval_description(dl: dict) -> dict:
    """Parse a Decision Log into human-readable title and detail."""
    action = dl.get("action", "")
    title = _ACTION_LABELS.get(action, action)

    try:
        data = json.loads(dl.get("input_summary") or "{}")
    except (json.JSONDecodeError, TypeError):
        data = {}

    # Build detail based on action type
    if "purchase_order" in action:
        supplier = (data.get("supplier") or "")[:40]
        items = data.get("items", [])
        total = sum(float(it.get("rate", 0)) * float(it.get("qty", 1)) for it in items)
        detail = f"Fornecedor: {supplier}" if supplier else ""
        if total > 0:
            detail += f"\n  Valor: R$ {total:,.2f}"
    elif "send_po" in action:
        po = data.get("purchase_order", "")
        detail = f"PO: {po}" if po and "PLACEHOLDER" not in po.upper() else "PO pendente de criacao"
    elif "purchase_invoice" in action:
        nf = data.get("nota_fiscal", "")
        detail = f"Nota Fiscal: {nf}" if nf else ""
    elif "payment" in action:
        pi = data.get("purchase_invoice", "")
        method = data.get("payment_method", "")
        detail = f"Fatura: {pi}" if pi else ""
        if method:
            detail += f" via {method}"
    elif "classify" in action:
        classification = data.get("classification", "?")
        comm = data.get("communication", "")
        detail = f"Classificacao: {classification}"
        if comm:
            detail += f" (Email: {comm})"
    else:
        detail = str(data)[:80] if data else ""

    return {"title": title, "detail": detail}

TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramBot:
    def __init__(self):
        from brazil_module.intelligence8.doctype.i8_agent_settings.i8_agent_settings import I8AgentSettings
        self._token = I8AgentSettings.get_telegram_token()
        self._settings = I8AgentSettings.get_settings()
        self._router = ChannelRouter()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def validate_webhook(self, request_secret: str) -> bool:
        """Return True only when request_secret matches the configured webhook secret."""
        expected = (
            self._settings.get_password("telegram_webhook_secret")
            if hasattr(self._settings, "get_password")
            else ""
        )
        return request_secret == expected

    def authorize_user(self, telegram_user_id: str) -> dict | None:
        """Return user metadata dict for known active users, None otherwise."""
        for user_row in (self._settings.telegram_users or []):
            if str(user_row.telegram_user_id) == str(telegram_user_id) and user_row.active:
                return {
                    "user": user_row.user,
                    "approval_limit": float(user_row.approval_limit or 0),
                }
        return None

    # ------------------------------------------------------------------
    # Update dispatcher
    # ------------------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        """Dispatch an incoming Telegram update to the appropriate handler."""
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
        elif "message" in update:
            self._handle_message(update["message"])

    # ------------------------------------------------------------------
    # Callback (inline keyboard) handler
    # ------------------------------------------------------------------

    def _handle_callback(self, callback: dict) -> None:
        user_id = str(callback["from"]["id"])
        auth = self.authorize_user(user_id)
        if not auth:
            return

        data = callback.get("data", "")
        parts = data.split(":", 2)
        action = parts[0]
        sub_action = parts[1] if len(parts) > 1 else ""
        log_name = parts[1] if action in ("approve", "reject", "details") else ""

        if action == "briefing":
            self._handle_briefing_action(sub_action, str(callback["message"]["chat"]["id"]))
            return

        if not log_name:
            return

        if action == "approve":
            self._process_approval(log_name, auth, approved=True)
        elif action == "reject":
            self._process_approval(log_name, auth, approved=False)
        elif action == "details":
            self._send_details(log_name)

    def _process_approval(self, log_name: str, auth: dict, approved: bool) -> None:
        log = frappe.get_doc("I8 Decision Log", log_name)

        transaction_amount = self._extract_transaction_amount(log)
        if auth["approval_limit"] > 0 and transaction_amount > auth["approval_limit"]:
            self.send_message(
                self._settings.telegram_chat_id,
                (
                    f"Limite de aprovacao excedido. "
                    f"Valor: R${transaction_amount:,.2f}, "
                    f"Limite: R${auth['approval_limit']:,.2f}"
                ),
            )
            return

        log.resolve(
            actor="Human",
            result="Success" if approved else "Rejected",
            channel="telegram",
            human_override=True,
        )

        # Record learning pattern
        try:
            from brazil_module.services.intelligence.learning_engine import record_approval, record_rejection
            tool_args = json.loads(log.input_summary or "{}")
            if approved:
                record_approval(log.action, tool_args)
            else:
                record_rejection(log.action, tool_args)
        except Exception as e:
            frappe.log_error(str(e), "I8 Learning Record Error")

        if approved:
            # Execute the approved tool directly instead of re-running the agent
            frappe.enqueue(
                "brazil_module.services.intelligence.channels.telegram_bot.execute_approved_action",
                queue="long",
                timeout=120,
                log_name=log_name,
            )
            self.send_message(
                self._settings.telegram_chat_id,
                f"Aprovado: {log.action}. Executando...",
            )
        else:
            self.send_message(
                self._settings.telegram_chat_id,
                f"Rejeitado: {log.action} - {log.related_docname}",
            )

        self._router.route_message(
            channel="telegram",
            direction="incoming",
            actor="human",
            content=f"{'Aprovado' if approved else 'Rejeitado'}: {log_name}",
            related_doctype=log.related_doctype,
            related_docname=log.related_docname,
        )

    def _send_details(self, log_name: str) -> None:
        try:
            log = frappe.get_doc("I8 Decision Log", log_name)
            text = (
                f"Detalhes: {log_name}\n"
                f"Evento: {log.event_type}\n"
                f"Acao: {log.action}\n"
                f"Modulo: {log.module}\n"
                f"Confianca: {log.confidence_score}\n"
                f"Motivo: {log.reasoning}\n"
                f"Input: {(log.input_summary or '')[:200]}"
            )
            self.send_message(self._settings.telegram_chat_id, text)
        except Exception as exc:
            frappe.log_error(str(exc), f"I8 Telegram Details Error: {log_name}")

    # ------------------------------------------------------------------
    # Briefing callback handler
    # ------------------------------------------------------------------

    def _handle_briefing_action(self, action: str, chat_id: str) -> None:
        """Handle callback buttons from the daily briefing."""
        if action == "list_approvals":
            self._briefing_list_approvals(chat_id)
        elif action == "list_overdue":
            self._briefing_list_overdue(chat_id)
        elif action == "process_nfs":
            self.send_message(chat_id, "Processando NFs pendentes...")
            frappe.enqueue(
                "brazil_module.services.intelligence.recurring.planning_loop.process_pending_nfs",
                queue="long",
                timeout=300,
            )
        elif action == "reconcile":
            self.send_message(chat_id, "Executando conciliacao bancaria...")
            frappe.enqueue(
                "brazil_module.services.intelligence.recurring.planning_loop.run_reconciliation",
                queue="long",
                timeout=300,
                notify_always=True,
            )

    def _briefing_list_approvals(self, chat_id: str) -> None:
        """List pending approvals in human-readable format with action buttons."""
        pending = frappe.get_all(
            "I8 Decision Log",
            filters={"result": "Pending", "docstatus": 0},
            fields=["name", "action", "input_summary", "confidence_score", "module", "creation"],
            order_by="creation desc",
            limit=5,
        )
        if not pending:
            self.send_message(chat_id, "Nenhuma aprovacao pendente.")
            return

        base_url = frappe.utils.get_url()
        lines = ["*Aprovacoes pendentes:*\n"]

        for dl in pending:
            # Parse input_summary to extract human-readable info
            description = _format_approval_description(dl)
            doc_link = f"{base_url}/app/i8-decision-log/{dl['name']}"
            lines.append(
                f"*{description['title']}*\n"
                f"  {description['detail']}\n"
                f"  Confianca: {float(dl.get('confidence_score') or 0):.0%}\n"
                f"  [Ver no ERP]({doc_link})\n"
            )

        keyboard = {"inline_keyboard": [
            [
                {"text": f"Aprovar", "callback_data": f"approve:{dl['name']}"},
                {"text": "Rejeitar", "callback_data": f"reject:{dl['name']}"},
                {"text": "Detalhes", "callback_data": f"details:{dl['name']}"},
            ]
            for dl in pending
        ]}
        self.send_message(chat_id, "\n".join(lines), keyboard)

    def _briefing_list_overdue(self, chat_id: str) -> None:
        """List overdue payments with document links."""
        overdue = frappe.get_all(
            "Purchase Invoice",
            filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": ["<", frappe.utils.today()]},
            fields=["name", "supplier_name", "outstanding_amount", "due_date"],
            order_by="due_date asc",
            limit=10,
        )
        if not overdue:
            self.send_message(chat_id, "Nenhum pagamento vencido.")
            return

        base_url = frappe.utils.get_url()
        total = sum(float(inv.get("outstanding_amount") or 0) for inv in overdue)
        lines = [f"*Pagamentos vencidos: R$ {total:,.2f}*\n"]

        for inv in overdue:
            supplier = (inv.get("supplier_name") or "")[:35]
            amount = float(inv.get("outstanding_amount") or 0)
            doc_link = f"{base_url}/app/purchase-invoice/{inv['name']}"
            days_late = (date.today() - inv["due_date"]).days if hasattr(inv["due_date"], "isoformat") else "?"
            lines.append(
                f"- [{inv['name']}]({doc_link})\n"
                f"  {supplier}\n"
                f"  R$ {amount:,.2f} — {days_late} dias atrasado"
            )

        self.send_message(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    def _handle_message(self, message: dict) -> None:
        user_id = str(message["from"]["id"])
        auth = self.authorize_user(user_id)
        if not auth:
            return

        text = message.get("text", "")
        chat_id = str(message["chat"]["id"])

        self._router.route_message(
            channel="telegram",
            direction="incoming",
            actor="human",
            content=text,
        )

        frappe.enqueue(
            "brazil_module.services.intelligence.agent.process_single_event",
            queue="long",
            event_type="human_message",
            event_id=str(message["message_id"]),
            event_data={
                "module": "conversation",
                "text": text,
                "chat_id": chat_id,
                "user": auth["user"],
            },
        )

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict | None = None,
    ) -> dict:
        """POST a sendMessage request to the Telegram Bot API."""
        url = f"{TELEGRAM_API.format(token=self._token)}/sendMessage"
        # Telegram max message length is 4096 chars
        truncated = text[:4090] + "..." if len(text) > 4096 else text
        payload: dict = {"chat_id": chat_id, "text": truncated, "parse_mode": "Markdown"}
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup)
        resp = requests.post(url, json=payload, timeout=10)
        result = resp.json()
        # Fallback: if Markdown fails, retry without parse_mode
        if not result.get("ok"):
            payload["parse_mode"] = ""
            del payload["parse_mode"]
            resp = requests.post(url, json=payload, timeout=10)
            result = resp.json()
        return result

    def send_approval_request(self, decision: dict) -> dict:
        """Format and send an approval request with an inline keyboard."""
        formatted = format_approval_message(decision)
        return self.send_message(
            self._settings.telegram_chat_id,
            formatted["text"],
            formatted["reply_markup"],
        )

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_transaction_amount(log) -> float:
        """Parse the transaction monetary value from a decision log's input_summary."""
        try:
            data = json.loads(log.input_summary or "{}")
            if "rate" in data and "qty" in data:
                return float(data["rate"]) * float(data["qty"])
            # Check items array for total
            if "items" in data:
                total = sum(float(it.get("rate", 0)) * float(it.get("qty", 1)) for it in data["items"])
                if total > 0:
                    return total
            return float(data.get("amount", 0))
        except (ValueError, TypeError, json.JSONDecodeError):
            return 0.0


def execute_approved_action(log_name: str):
    """Background job: execute a tool call that was approved by a human.

    Reads the Decision Log to get the tool name and arguments, then
    executes the tool directly via ActionExecutor.
    After creating a PO, auto-sends to supplier if email is configured.
    """
    from brazil_module.services.intelligence.action_executor import ActionExecutor
    from brazil_module.services.intelligence.tools import execute_tool

    log = frappe.get_doc("I8 Decision Log", log_name)
    tool_name = log.action
    tool_args = json.loads(log.input_summary or "{}")

    executor = ActionExecutor()
    try:
        result = execute_tool(tool_name, tool_args, executor)
        bot = TelegramBot()
        doc_name = result.get("name", "") if isinstance(result, dict) else ""
        doctype = result.get("doctype", "") if isinstance(result, dict) else ""

        # Auto-submit: check recurring expense setting first, then global setting
        submitted = _auto_submit_if_enabled(executor, doctype, doc_name, tool_args)
        status_msg = "Criado e Submetido" if submitted else "Criado (Draft)"

        base_url = frappe.utils.get_url()
        doctype_slug = doctype.lower().replace(" ", "-") if doctype else ""
        link = f"\n[Ver no ERP]({base_url}/app/{doctype_slug}/{doc_name})" if doctype_slug and doc_name else ""

        bot.send_message(
            bot._settings.telegram_chat_id,
            f"{status_msg}: {doctype} {doc_name}{link}",
        )

        # Auto-send PO to supplier after creation
        if tool_name == "p2p-create_purchase_order" and doc_name:
            _auto_send_po_to_supplier(bot, doc_name, executor)
            # Clean up any pending send_po decisions with placeholder names
            _cleanup_placeholder_decisions()

        # Log to conversation
        from brazil_module.services.intelligence.channels.channel_router import ChannelRouter
        router = ChannelRouter()
        router.route_message(
            channel="system", direction="outgoing", actor="agent",
            content=f"Executed: {tool_name} -> {doctype} {doc_name} ({status_msg})",
            related_doctype=doctype,
            related_docname=doc_name,
        )
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(str(e), f"I8 Approved Action Error: {tool_name}")
        try:
            bot = TelegramBot()
            bot.send_message(
                bot._settings.telegram_chat_id,
                f"Erro ao executar {tool_name}: {str(e)[:200]}",
            )
        except Exception:
            pass


def _auto_send_po_to_supplier(bot, po_name: str, executor) -> None:
    """After PO creation, auto-send to supplier only if notify_supplier is enabled in Recurring Expense."""
    try:
        po = frappe.get_doc("Purchase Order", po_name)

        # Check if this supplier has notify_supplier enabled in any active Recurring Expense
        notify = frappe.get_all(
            "I8 Recurring Expense",
            filters={"supplier": po.supplier, "active": 1, "notify_supplier": 1},
            limit=1,
        )
        if not notify:
            return  # Supplier not marked for notification

        contact_email = frappe.db.get_value("Supplier", po.supplier, "email_id")
        if contact_email:
            frappe.sendmail(
                recipients=[contact_email],
                subject=f"Purchase Order {po.name}",
                message=f"Prezado fornecedor, segue a Purchase Order {po.name}.",
                reference_doctype="Purchase Order",
                reference_name=po.name,
            )
            bot.send_message(
                bot._settings.telegram_chat_id,
                f"PO enviada ao fornecedor: {contact_email}",
            )
        else:
            bot.send_message(
                bot._settings.telegram_chat_id,
                f"PO {po_name}: fornecedor sem email cadastrado (email_id). Envio manual necessario.",
            )
    except Exception as e:
        frappe.log_error(str(e), f"I8 Auto Send PO Error: {po_name}")


def _cleanup_placeholder_decisions():
    """Remove pending Decision Logs with placeholder PO names."""
    try:
        placeholders = frappe.db.sql("""
            DELETE FROM `tabI8 Decision Log`
            WHERE result = 'Pending' AND docstatus = 0
            AND action = 'p2p-send_po_to_supplier'
            AND (input_summary LIKE '%%PLACEHOLDER%%' OR input_summary LIKE '%%"PO"%%')
        """)
        frappe.db.commit()
    except Exception:
        pass


def _auto_submit_if_enabled(executor, doctype: str, doc_name: str, tool_args: dict | None = None) -> bool:
    """Submit a document if auto-submit is enabled.

    Checks in order:
    1. Recurring Expense auto_submit setting (per-expense override)
    2. Global Agent Settings auto_submit per DocType

    Returns True if submitted, False otherwise.
    """
    if not doctype or not doc_name:
        return False

    # Check recurring expense setting first (per-expense override)
    if tool_args and tool_args.get("supplier"):
        try:
            recurring = frappe.get_all(
                "I8 Recurring Expense",
                filters={"supplier": tool_args["supplier"], "active": 1},
                fields=["auto_submit"],
                limit=1,
            )
            if recurring:
                if recurring[0].get("auto_submit"):
                    try:
                        executor.execute(doctype, "submit", {"name": doc_name})
                        return True
                    except Exception as e:
                        frappe.log_error(str(e), f"I8 Auto-Submit Error: {doctype} {doc_name}")
                        return False
                else:
                    # Recurring expense explicitly says no auto-submit
                    return False
        except Exception:
            pass

    # Fallback: global Agent Settings
    settings = frappe.get_single("I8 Agent Settings")
    auto_submit_map = {
        "Purchase Order": settings.auto_submit_po,
        "Purchase Invoice": settings.auto_submit_pi,
        "Journal Entry": settings.auto_submit_je,
        "Payment Entry": settings.auto_submit_pe,
    }

    if not auto_submit_map.get(doctype):
        return False

    try:
        executor.execute(doctype, "submit", {"name": doc_name})
        return True
    except Exception as e:
        frappe.log_error(str(e), f"I8 Auto-Submit Error: {doctype} {doc_name}")
        return False
