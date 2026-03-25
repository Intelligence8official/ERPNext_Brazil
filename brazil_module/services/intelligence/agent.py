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

        start = time.monotonic()
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt + "\n\n" + context.get("module_context", ""),
                messages=[{"role": "user", "content": json.dumps(event_data, default=str, ensure_ascii=False)}],
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

        results = []
        for block in response.content:
            if block.type == "tool_use":
                result = self._handle_tool_call(block, event_type, event_data, confidence)
                results.append(result)

        return {"status": "completed", "results": results}

    @staticmethod
    def _extract_confidence(response) -> float:
        for block in response.content:
            if block.type == "text":
                match = re.search(r"[Cc]onfidence:\s*(0\.\d+|1\.0)", block.text)
                if match:
                    return float(match.group(1))
        return 0.5

    def _handle_tool_call(self, tool_block, event_type: str, event_data: dict, confidence: float) -> dict:
        tool_name = tool_block.name
        tool_args = tool_block.input

        amount = tool_args.get("rate", 0) * tool_args.get("qty", 1) if "rate" in tool_args else tool_args.get("amount", 0)
        action = tool_name.split(".")[-1]
        doctype = tool_args.get("doctype", "")

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
        event_type="nf_received",
        event_id=doc.name,
        event_data={
            "module": "fiscal",
            "nota_fiscal": doc.name,
            "supplier": doc.cnpj_emitente,
        },
        deduplicate=True,
    )
