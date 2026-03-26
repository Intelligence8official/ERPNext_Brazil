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

import requests
import frappe

from brazil_module.services.intelligence.channels.channel_router import ChannelRouter
from brazil_module.services.intelligence.prompts.approval_formatter import format_approval_message

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
        action, _, log_name = data.partition(":")

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

        # Auto-submit if enabled in settings
        submitted = _auto_submit_if_enabled(executor, doctype, doc_name)
        status_msg = "Criado e Submetido" if submitted else "Criado (Draft)"

        bot.send_message(
            bot._settings.telegram_chat_id,
            f"{status_msg}: {doctype} {doc_name}",
        )

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


def _auto_submit_if_enabled(executor, doctype: str, doc_name: str) -> bool:
    """Submit a document if auto-submit is enabled for its DocType.

    Returns True if submitted, False otherwise.
    """
    if not doctype or not doc_name:
        return False

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
