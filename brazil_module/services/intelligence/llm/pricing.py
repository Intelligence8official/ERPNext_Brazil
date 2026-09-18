"""
What a call costs, per provider and per model.

Prices are USD per million tokens, checked on 2026-09-18 against each
provider's public price page. They move: Anthropic dropped Sonnet 5 to
$2/$10 (the $3/$15 increase announced for September never happened), and
**Gemini 3.x Flash is on promotional pricing until 2026-12-31, doubling to
$1.50/$7.50 on 2027-01-01**. Revisit this table then.

`cached_input` is what a cached prompt prefix costs to read — roughly a tenth
of the input price everywhere. Cached tokens are counted apart from
`tokens_in` here: the providers report them inconsistently (Anthropic's
`input_tokens` already excludes them, Google's `prompt_token_count` includes
them), so each adapter normalizes before this module sees them.
"""

from dataclasses import dataclass

import frappe


@dataclass(frozen=True)
class Price:
    """USD per million tokens.

    The Gemini Pro models charge a second, higher rate once the input passes a
    threshold — and the deep tier is exactly where the long prompts go.
    """

    input: float
    output: float
    cached_input: float
    long_input: float | None = None
    long_output: float | None = None
    long_cached_input: float | None = None
    long_threshold: int = 200_000

    def above(self, input_tokens: int) -> "Price":
        """This price, at the size of the prompt that was actually sent."""
        if self.long_input is None or input_tokens <= self.long_threshold:
            return self
        return Price(
            input=self.long_input,
            output=self.long_output if self.long_output is not None else self.output,
            cached_input=(
                self.long_cached_input if self.long_cached_input is not None else self.cached_input
            ),
        )


PRICING: dict[str, dict[str, Price]] = {
    "anthropic": {
        "claude-haiku-4-5-20251001": Price(1.0, 5.0, 0.10),
        "claude-sonnet-5": Price(2.0, 10.0, 0.20),
        "claude-opus-5": Price(5.0, 25.0, 0.50),
        # The generation a migrating site arrives with: the old settings
        # shipped these two as defaults, and the patch carries them over.
        "claude-sonnet-4-6": Price(3.0, 15.0, 0.30),
        "claude-opus-4-6": Price(5.0, 25.0, 0.50),
        "claude-fable-5-1": Price(10.0, 50.0, 0.25),
    },
    "google": {
        # Flash 3.x is on promotional pricing until 2026-12-31: on 2027-01-01
        # input, output AND cached input all double, to 1.50 / 7.50 / 0.15.
        "gemini-3.8-flash": Price(0.75, 3.75, 0.075),
        "gemini-3.7-flash": Price(0.75, 3.75, 0.075),
        # Not part of that promotion.
        "gemini-3.5-flash-lite": Price(0.30, 2.50, 0.03),
        # The only GA deep-reasoning model Google has; 3.1 Pro is preview.
        # Both charge a second rate above 200k input tokens.
        "gemini-2.5-pro": Price(1.25, 10.0, 0.125, 2.50, 15.0, 0.25),
        "gemini-3.1-pro-preview": Price(2.0, 12.0, 0.20, 4.0, 18.0, 0.40),
        "gemini-2.5-flash": Price(0.30, 2.50, 0.03),
        "gemini-2.5-flash-lite": Price(0.10, 0.40, 0.01),
    },
    "openai": {
        "gpt-5.6-luna": Price(0.20, 1.20, 0.02),
        "gpt-5.6-terra": Price(2.00, 12.00, 0.20),
        # Promotional through 2026-11-21; list price is around $5/$30.
        "gpt-5.6-sol": Price(4.00, 20.00, 0.40),
        "gpt-6-astra": Price(10.00, 50.00, 1.00),
    },
}

PER_MILLION = 1_000_000

CACHE_WRITE_MULTIPLIER = 1.25
"""Filling a cache costs above the input rate at all three providers; only
reading from it is cheaper."""


def price_for(provider: str, model: str) -> Price:
    """The price of a model, never raising.

    An unknown model is priced as the DEAREST model of its provider. The daily
    budget gate spends this number, and a guess that is too low would let a
    model nobody priced run straight past the budget.
    """
    table = PRICING.get((provider or "").lower().strip())
    if table is None:
        _report(f"Unknown LLM provider '{provider}' — priced as the dearest model known", str(provider))
        return _dearest(_every_price())

    price = table.get((model or "").strip())
    if price is not None:
        return price

    _report(
        f"Unknown model '{model}' for provider '{provider}' — priced as that provider's dearest",
        f"{provider}:{model}",
    )
    return _dearest(table.values())


def calculate_cost_usd(
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """What one call cost. The three input buckets do not overlap."""
    price = price_for(provider, model).above(
        (tokens_in or 0) + (cached_tokens or 0) + (cache_write_tokens or 0)
    )
    return (
        (tokens_in or 0) * price.input
        + (tokens_out or 0) * price.output
        + (cached_tokens or 0) * price.cached_input
        + (cache_write_tokens or 0) * price.input * CACHE_WRITE_MULTIPLIER
    ) / PER_MILLION


def _dearest(prices) -> Price:
    return max(prices, key=lambda price: price.output)


def _every_price() -> list[Price]:
    return [price for table in PRICING.values() for price in table.values()]


REPORT_SILENCE_SECONDS = 86400


def _report(message: str, key: str) -> None:
    """A price we had to guess is worth knowing about — once.

    This runs on every call, and the report is itself a database write: one
    Error Log per model call would bury the log it is trying to raise.
    """
    try:
        cache_key = f"i8_pricing_report:{key}"
        if frappe.cache.get_value(cache_key):
            return
        frappe.cache.set_value(cache_key, "reported", expires_in_sec=REPORT_SILENCE_SECONDS)
        frappe.log_error(message, "I8 LLM Pricing")
    except Exception:
        pass
