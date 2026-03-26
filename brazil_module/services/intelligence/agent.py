import json
import re
import time

import anthropic
import frappe

from brazil_module.services.intelligence.action_executor import ActionExecutor
from brazil_module.services.intelligence.circuit_breaker import CircuitBreaker
from brazil_module.services.intelligence.context_builder import ContextBuilder
from brazil_module.services.intelligence.cost_tracker import CostTracker
from brazil_module.services.intelligence.decision_engine import DecisionEngine
from brazil_module.services.intelligence.prompts.system_prompt import build_system_prompt
from brazil_module.services.intelligence.tools import execute_tool, get_all_tool_schemas

_circuit_breaker = CircuitBreaker()

HAIKU_EVENTS = [
    "classify_email", "format_notification", "status_check",
    "simple_match", "recurring_schedule",
]
OPUS_EVENTS = [
    "anomaly_detected", "high_value_decision",
    "complex_reconciliation", "multi_document_analysis",
]


class Intelligence8Agent:
    def __init__(self):
        self.settings = frappe.get_single("I8 Agent Settings")
        self.client = anthropic.Anthropic(
            api_key=self.settings.get_password("anthropic_api_key") if hasattr(self.settings, "get_password") else ""
        )
        self.context_builder = ContextBuilder()
        self.decision_engine = DecisionEngine(self.settings)
        self.action_executor = ActionExecutor()
        self.cost_tracker = CostTracker()

    def select_model(self, event_type: str) -> str:
        if event_type in HAIKU_EVENTS:
            return self.settings.haiku_model
        if event_type in OPUS_EVENTS:
            return self.settings.opus_model
        return self.settings.sonnet_model

    def get_timeout(self, model: str) -> int:
        if "haiku" in model:
            return self.settings.haiku_timeout_seconds or 30
        if "opus" in model:
            return self.settings.opus_timeout_seconds or 120
        return self.settings.sonnet_timeout_seconds or 60

    def process_event(self, event_type: str, event_data: dict) -> dict:
        if not self.settings.enabled:
            return {"status": "skipped", "reason": "agent_disabled"}

        if not _circuit_breaker.allow_request():
            return {"status": "queued", "reason": "circuit_breaker_open"}

        if not self.cost_tracker.check_daily_budget(float(self.settings.daily_budget_usd or 999)):
            if self.settings.pause_on_budget_exceeded:
                return {"status": "paused", "reason": "budget_exceeded"}

        model = self.select_model(event_type)
        context = self.context_builder.build(event_type, event_data)
        system_prompt = build_system_prompt(self.settings, ["p2p", "fiscal", "banking"])
        tools = get_all_tool_schemas()

        system = system_prompt + "\n\n" + context.get("module_context", "")
        messages = [{"role": "user", "content": self._build_user_message(event_type, event_data, context)}]

        all_results = []
        text_response = ""
        max_turns = 5  # prevent infinite loops

        for turn in range(max_turns):
            start = time.monotonic()
            try:
                response = self.client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system,
                    messages=messages,
                    tools=tools,
                )
                _circuit_breaker.record_success()
            except Exception as e:
                _circuit_breaker.record_failure()
                frappe.log_error(str(e), "Intelligence8 Claude API Error")
                return {"status": "error", "message": str(e)}

            latency_ms = int((time.monotonic() - start) * 1000)
            usage = response.usage
            cache_hit = getattr(usage, "cache_read_input_tokens", 0) > 0

            self.cost_tracker.log(
                model=model,
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
                latency_ms=latency_ms,
                module=event_data.get("module", "general"),
                function_name=event_type,
                cache_hit=cache_hit,
            )

            confidence = self._extract_confidence(response)

            # Collect text and tool calls from this turn
            tool_results_for_next_turn = []
            for block in response.content:
                if block.type == "text":
                    text_response += block.text
                elif block.type == "tool_use":
                    result = self._handle_tool_call(block, event_type, event_data, confidence)
                    all_results.append(result)
                    # Build tool_result for next turn
                    tool_output = json.dumps(result.get("result", {"status": result["status"]}), default=str)
                    tool_results_for_next_turn.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": tool_output[:4000],
                    })

            # If no tool calls or stop_reason is "end_turn", we're done
            if response.stop_reason != "tool_use" or not tool_results_for_next_turn:
                break

            # Continue the conversation: add assistant response + tool results
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results_for_next_turn})

        # Send final text response back to the user's channel
        if text_response.strip():
            self._send_response(event_data, text_response.strip())

        return {"status": "completed", "results": all_results, "text": text_response}

    def _send_response(self, event_data: dict, text: str) -> None:
        """Send agent's text response back to the originating channel."""
        chat_id = event_data.get("chat_id")
        if chat_id:
            # Came from Telegram — reply there
            try:
                from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot
                bot = TelegramBot()
                # Strip confidence line from user-facing messages
                clean = re.sub(r"\n?Confidence:\s*0\.\d+", "", text).strip()
                if clean:
                    bot.send_message(chat_id, clean)
            except Exception as e:
                frappe.log_error(str(e), "I8 Telegram Response Error")

        # Always log to conversation
        try:
            from brazil_module.services.intelligence.channels.channel_router import ChannelRouter
            router = ChannelRouter()
            channel = "telegram" if chat_id else "system"
            router.route_message(
                channel=channel, direction="outgoing", actor="agent",
                content=text,
                related_doctype=event_data.get("related_doctype"),
                related_docname=event_data.get("related_docname"),
            )
        except Exception as e:
            frappe.log_error(str(e), "I8 Conversation Log Error")

    @staticmethod
    def _build_user_message(event_type: str, event_data: dict, context: dict) -> str:
        """Build a rich user message with all context for the LLM."""
        parts = [f"EVENT: {event_type}\n"]

        if event_type == "recurring_schedule" and context.get("recurring_expense"):
            exp = context["recurring_expense"]
            items_str = "\n".join(
                f"  - {it['item_code']}: qty={it['qty']}, rate={it['rate']}"
                for it in exp.get("items", [])
            )
            parts.append(
                f"ACTION REQUIRED: Create a Purchase Order for this recurring expense.\n\n"
                f"Recurring Expense: {exp['title']}\n"
                f"Supplier: {exp['supplier']}\n"
                f"Amount: {exp['currency']} {exp['estimated_amount']}\n"
                f"Due Date: {exp['next_due']}\n"
                f"Document Type: {exp['document_type']}\n"
                f"Items:\n{items_str}\n"
                f"Notify Supplier: {'Yes' if exp.get('notify_supplier') else 'No'}\n"
            )
        elif event_type == "human_message":
            parts.append(f"User message: {event_data.get('text', '')}\n")
        elif event_type == "classify_email":
            comm_name = event_data.get("communication", "")
            parts.append(
                f"ACTION REQUIRED: Classify this email by calling the email-classify tool.\n\n"
                f"Communication ID: {comm_name}\n"
                f"Subject: {event_data.get('subject', '')}\n"
                f"Sender: {event_data.get('sender', '')}\n"
                f"Content: {(event_data.get('content') or '')[:500]}\n\n"
                f"Call email-classify with:\n"
                f"- communication: \"{comm_name}\"\n"
                f"- classification: one of FISCAL, COMMERCIAL, FINANCIAL, OPERATIONAL, SPAM, UNCERTAIN\n"
                f"- reasoning: brief explanation\n"
                f"DO NOT call any other tool. Just classify and call email-classify.\n"
            )
        elif event_type == "nf_received":
            nf_name = event_data.get("nota_fiscal", "")
            supplier = event_data.get("supplier", "")
            parts.append(
                f"ACTION REQUIRED: Process this incoming Nota Fiscal.\n\n"
                f"Nota Fiscal: {nf_name}\n"
                f"Supplier CNPJ: {supplier}\n\n"
                f"Follow these steps IN ORDER:\n\n"
                f"Step 1: Call fiscal-get_nf_details to read the NF details.\n\n"
                f"Step 2: Call fiscal-find_matching_pos to find Purchase Orders for this supplier.\n\n"
                f"Step 3: Based on results, follow ONE of these scenarios:\n\n"
                f"  SCENARIO A (PO found): Call fiscal-link_nf_to_po, then fiscal-create_purchase_invoice with the PO.\n\n"
                f"  SCENARIO B (No PO, but recurring expense exists): Call fiscal-find_recurring_expense.\n"
                f"    If found, call p2p-create_purchase_order to create the PO first,\n"
                f"    then fiscal-link_nf_to_po and fiscal-create_purchase_invoice.\n\n"
                f"  SCENARIO C (No PO, supplier known in ERPNext): Call fiscal-create_purchase_invoice directly (without PO).\n\n"
                f"  SCENARIO D (Unknown supplier): Call fiscal-update_nf_status with invoice_status='Needs Review'\n"
                f"    and explain in notes why it needs human review.\n"
            )
        else:
            parts.append(json.dumps(event_data, default=str, ensure_ascii=False))

        if context.get("supplier_profile"):
            sp = context["supplier_profile"]
            parts.append(f"\nSupplier Profile: expected_nf_days={sp.get('expected_nf_days')}, auto_pay={sp.get('auto_pay')}")

        parts.append(f"\nToday: {context.get('system_context', '')}")
        return "\n".join(parts)

    @staticmethod
    def _extract_confidence(response) -> float:
        for block in response.content:
            if block.type == "text":
                match = re.search(r"[Cc]onfidence:\s*(0\.\d+|1\.0)", block.text)
                if match:
                    return float(match.group(1))
        return 0.5

    def _notify_auto_approved(self, tool_name: str, tool_args: dict, result: dict) -> None:
        """Notify Telegram about an auto-approved action from learning."""
        try:
            from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot
            from brazil_module.services.intelligence.channels.telegram_bot import _format_approval_description

            bot = TelegramBot()
            desc = _format_approval_description({"action": tool_name, "input_summary": json.dumps(tool_args, default=str)})
            doc_name = result.get("name", "") if isinstance(result, dict) else ""

            msg = f"*Auto-aprovado (aprendizado):*\n  {desc['title']}\n  {desc['detail']}"
            if doc_name:
                msg += f"\n  Documento: {doc_name}"

            bot.send_message(bot._settings.telegram_chat_id, msg)
        except Exception as e:
            frappe.log_error(str(e), "I8 Learning Notification Error")

    def _notify_document_created(self, tool_name: str, tool_args: dict, doc_name: str) -> None:
        """Notify Telegram when a document is auto-created by the agent."""
        try:
            from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot, _format_approval_description
            bot = TelegramBot()
            desc = _format_approval_description({"action": tool_name, "input_summary": json.dumps(tool_args, default=str)})
            base_url = frappe.utils.get_url()

            # Determine doctype for URL from tool name
            doctype_map = {
                "p2p-create_purchase_order": "purchase-order",
                "fiscal-create_purchase_invoice": "purchase-invoice",
                "banking-create_payment": "payment-entry",
            }
            url_doctype = doctype_map.get(tool_name, "")
            link = f"\n[Ver no ERP]({base_url}/app/{url_doctype}/{doc_name})" if url_doctype else ""

            bot.send_message(
                bot._settings.telegram_chat_id,
                f"*Documento criado automaticamente:*\n  {desc['title']}\n  {desc['detail']}\n  Documento: {doc_name}{link}",
            )
        except Exception as e:
            frappe.log_error(str(e), "I8 Document Creation Notification Error")

    # Tools that are always safe to execute without approval
    ALWAYS_APPROVE_TOOLS = {
        "email-classify",   # classifying email is read-only
        "email-search",     # searching email is read-only
        "email-get_content",  # reading email is read-only
        "erp-read_document",  # reading documents is read-only
        "erp-list_documents",  # listing documents is read-only
        "erp-get_report_data",  # reporting is read-only
        "erp-get_account_balance",  # reading balances is read-only
        "fiscal-get_nf_details",  # reading NF is read-only
        "fiscal-find_matching_pos",  # finding POs is read-only
        "fiscal-find_recurring_expense",  # finding recurring expenses is read-only
        "fiscal-update_nf_status",  # updating NF status is low-risk
    }

    def _handle_tool_call(self, tool_block, event_type: str, event_data: dict, confidence: float) -> dict:
        tool_name = tool_block.name
        tool_args = tool_block.input

        amount = tool_args.get("rate", 0) * tool_args.get("qty", 1) if "rate" in tool_args else tool_args.get("amount", 0)
        action = tool_name.split("-")[-1]
        doctype = tool_args.get("doctype", "")

        # Check learning patterns for non-safe tools
        if tool_name not in self.ALWAYS_APPROVE_TOOLS:
            try:
                from brazil_module.services.intelligence.learning_engine import check_learned_pattern
                if check_learned_pattern(tool_name, tool_args):
                    try:
                        result = execute_tool(tool_name, tool_args, self.action_executor)
                        self.decision_engine.log_decision(
                            event_type=event_type, module=event_data.get("module", ""),
                            action=tool_name, actor="Agent", channel="system",
                            confidence=confidence, model=self.select_model(event_type),
                            input_summary=json.dumps(tool_args, default=str)[:500],
                            reasoning="Auto-approved by Learning Loop",
                            result="Success", related_doctype=doctype,
                            related_docname=result.get("name") if isinstance(result, dict) else None,
                        )
                        self._notify_auto_approved(tool_name, tool_args, result)
                        return {"tool": tool_name, "status": "executed", "result": result}
                    except Exception as e:
                        frappe.log_error(str(e), f"I8 Learning Auto-Approve Error: {tool_name}")
                        return {"tool": tool_name, "status": "error", "message": str(e)}
            except Exception:
                pass  # If learning check fails, fall through to normal flow

        # Safe/read-only tools skip the Decision Engine entirely
        if tool_name in self.ALWAYS_APPROVE_TOOLS:
            decision = {"auto_approve": True, "confidence": confidence, "threshold": 0}
        else:
            decision = self.decision_engine.evaluate(
                action=action, doctype=doctype,
                confidence=confidence, amount=amount,
            )

        if decision["auto_approve"]:
            try:
                result = execute_tool(tool_name, tool_args, self.action_executor)
                self.decision_engine.log_decision(
                    event_type=event_type, module=event_data.get("module", ""),
                    action=tool_name, actor="Agent", channel="system",
                    confidence=confidence, model=self.select_model(event_type),
                    input_summary=json.dumps(tool_args, default=str)[:500],
                    reasoning="Auto-approved",
                    result="Success", related_doctype=doctype,
                    related_docname=result.get("name") if isinstance(result, dict) else None,
                )
                # Notify Telegram about auto-created documents
                if tool_name not in self.ALWAYS_APPROVE_TOOLS:
                    doc_name = result.get("name") if isinstance(result, dict) else None
                    if doc_name:
                        self._notify_document_created(tool_name, tool_args, doc_name)
                return {"tool": tool_name, "status": "executed", "result": result}
            except Exception as e:
                frappe.log_error(str(e), f"I8 Tool Error: {tool_name}")
                return {"tool": tool_name, "status": "error", "message": str(e)}
        else:
            log_name = self.decision_engine.log_decision(
                event_type=event_type, module=event_data.get("module", ""),
                action=tool_name, actor="Agent", channel="system",
                confidence=confidence, model=self.select_model(event_type),
                input_summary=json.dumps(tool_args, default=str)[:500],
                reasoning="Below confidence threshold",
                result="Pending",
            )
            return {"tool": tool_name, "status": "pending_approval", "decision_log": log_name}


def process_single_event(event_type: str, event_id: str, event_data: dict):
    lock_key = f"i8:lock:{event_type}:{event_id}"
    cache = frappe.cache
    if cache.get_value(lock_key):
        return
    cache.set_value(lock_key, 1, expires_in_sec=300)

    try:
        agent = Intelligence8Agent()
        agent.process_event(event_type, event_data)
    finally:
        cache.delete_value(lock_key)


def on_communication(doc, method=None):
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    if doc.communication_type != "Communication" or doc.sent_or_received != "Received":
        return
    frappe.enqueue(
        "brazil_module.services.intelligence.agent.process_single_event",
        queue="long",
        timeout=60,
        job_id=f"i8:classify_email:{doc.name}",
        event_type="classify_email",
        event_id=doc.name,
        event_data={
            "module": "email",
            "communication": doc.name,
            "subject": doc.subject,
            "content": doc.content,
            "sender": doc.sender,
        },
        deduplicate=True,
    )


def on_nota_fiscal(doc, method=None):
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    frappe.enqueue(
        "brazil_module.services.intelligence.agent.process_single_event",
        queue="long",
        timeout=120,
        job_id=f"i8:nf_received:{doc.name}",
        event_type="nf_received",
        event_id=doc.name,
        event_data={
            "module": "fiscal",
            "nota_fiscal": doc.name,
            "supplier": doc.cnpj_emitente,
        },
        deduplicate=True,
    )
