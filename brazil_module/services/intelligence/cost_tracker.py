"""
What each model call cost, written down.

The price table moved to `llm/pricing.py`, keyed by provider AND model: the
same token count costs different money depending on who ran it, and pricing
every model as if it were Claude was how the old table under-reported the
moment the system started answering with Gemini.
"""

from datetime import datetime

import frappe

from brazil_module.services.intelligence.llm.pricing import calculate_cost_usd

DEFAULT_PROVIDER = "anthropic"


class CostTracker:
    def log(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        module: str,
        function_name: str,
        provider: str = DEFAULT_PROVIDER,
        cached_tokens: int = 0,
        cache_hit: bool | None = None,
        decision_log: str | None = None,
        company: str | None = None,
        department: str | None = None,
        trace_id: str | None = None,
    ) -> str:
        cost = calculate_cost_usd(provider, model, tokens_in, tokens_out, cached_tokens)

        doc = frappe.new_doc("I8 Cost Log")
        doc.timestamp = datetime.now()
        doc.module = module
        doc.function_name = function_name
        doc.provider = provider
        doc.model = model
        doc.tokens_in = tokens_in
        doc.tokens_out = tokens_out
        doc.cached_tokens = cached_tokens
        doc.cost_usd = cost
        doc.latency_ms = latency_ms
        doc.cache_hit = bool(cached_tokens) if cache_hit is None else cache_hit
        doc.decision_log = decision_log
        doc.company = company
        doc.department = department
        doc.trace_id = trace_id
        doc.insert(ignore_permissions=True)
        return doc.name

    def check_daily_budget(self, limit_usd: float) -> bool:
        return self.get_daily_total() < limit_usd

    def get_daily_total(self) -> float:
        today = datetime.now().date()
        result = frappe.db.sql(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM `tabI8 Cost Log` WHERE DATE(timestamp) = %s",
            (today,),
        )
        return float(result[0][0]) if result else 0.0
