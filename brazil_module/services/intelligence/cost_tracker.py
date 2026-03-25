from datetime import datetime

import frappe

# Pricing per million tokens (as of 2026-03)
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
}

DEFAULT_PRICING = MODEL_PRICING["claude-sonnet-4-6"]

CACHE_INPUT_DISCOUNT = 0.10  # cached input is 10% of original price


def calculate_cost_usd(
    model: str,
    tokens_in: int,
    tokens_out: int,
    cache_hit: bool = False,
) -> float:
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
    input_rate = pricing["input"]
    if cache_hit:
        input_rate = input_rate * CACHE_INPUT_DISCOUNT
    return (tokens_in * input_rate / 1_000_000) + (tokens_out * pricing["output"] / 1_000_000)


class CostTracker:
    def log(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        module: str,
        function_name: str,
        cache_hit: bool = False,
        decision_log: str | None = None,
        company: str | None = None,
        department: str | None = None,
    ) -> str:
        cost = calculate_cost_usd(model, tokens_in, tokens_out, cache_hit)
        doc = frappe.new_doc("I8 Cost Log")
        doc.timestamp = datetime.now()
        doc.module = module
        doc.function_name = function_name
        doc.model = model
        doc.tokens_in = tokens_in
        doc.tokens_out = tokens_out
        doc.cost_usd = cost
        doc.latency_ms = latency_ms
        doc.cache_hit = cache_hit
        doc.decision_log = decision_log
        doc.company = company
        doc.department = department
        doc.insert(ignore_permissions=True)
        return doc.name

    def check_daily_budget(self, limit_usd: float) -> bool:
        today = datetime.now().date()
        result = frappe.db.sql(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM `tabI8 Cost Log` WHERE DATE(timestamp) = %s",
            (today,),
        )
        total = float(result[0][0]) if result else 0.0
        return total < limit_usd

    def get_daily_total(self) -> float:
        today = datetime.now().date()
        result = frappe.db.sql(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM `tabI8 Cost Log` WHERE DATE(timestamp) = %s",
            (today,),
        )
        return float(result[0][0]) if result else 0.0
