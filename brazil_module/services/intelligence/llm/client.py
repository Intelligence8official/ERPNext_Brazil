"""
What Intelligence8 calls when it needs a model.

Everything above this line speaks in tiers — `fast`, `standard`, `deep` — and
never in model ids or vendor names. Everything below it is one adapter.

Three things happen here that used to be scattered or missing: the tier is
resolved into a model and a timeout, every call is written to I8 Cost Log
(the orchestrator's router call used to spend without leaving a record), and
a provider failure is announced once an hour instead of only reaching the
Error Log — the system stops on a failure, so it has to say that it stopped.
"""

import time
from collections.abc import Sequence

import frappe

from brazil_module.services.intelligence.llm.base import Completion, LLMError, Turn, UserTurn
from brazil_module.services.intelligence.llm.factory import (
    DEFAULT_TIER,
    build_provider,
    model_for,
    timeout_for,
)

ALERT_KEY = "i8_llm_alert"
ALERT_SILENCE_SECONDS = 3600


class LLM:
    def __init__(self, settings=None, provider=None, cost_tracker=None):
        self._settings = settings
        self._provider = provider
        self._cost_tracker = cost_tracker

    @property
    def provider(self):
        if self._provider is None:
            self._provider = build_provider(self._settings)
        return self._provider

    @property
    def cost_tracker(self):
        if self._cost_tracker is None:
            from brazil_module.services.intelligence.cost_tracker import CostTracker

            self._cost_tracker = CostTracker()
        return self._cost_tracker

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Turn],
        tier: str = DEFAULT_TIER,
        tools: Sequence[dict] = (),
        max_tokens: int = 4096,
        module: str = "",
        function_name: str = "",
        decision_log: str | None = None,
        trace_id: str | None = None,
    ) -> Completion:
        model = model_for(tier, self._settings)
        started = time.monotonic()

        try:
            completion = self.provider.complete(
                model=model,
                system=system,
                messages=list(messages),
                tools=list(tools),
                max_tokens=max_tokens,
                timeout=timeout_for(tier, self._settings),
            )
        except LLMError as e:
            self._announce(e)
            raise

        self._log_cost(
            completion,
            latency_ms=int((time.monotonic() - started) * 1000),
            module=module,
            function_name=function_name,
            decision_log=decision_log,
            trace_id=trace_id,
        )
        return completion

    def ask(
        self,
        *,
        system: str,
        prompt: str,
        tier: str = DEFAULT_TIER,
        max_tokens: int = 2000,
        module: str = "",
        function_name: str = "",
    ) -> str:
        """One question, one answer. What the briefing, the anomaly formatter
        and the event router need — none of them uses tools."""
        return self.complete(
            system=system,
            messages=[UserTurn(prompt)],
            tier=tier,
            max_tokens=max_tokens,
            module=module,
            function_name=function_name,
        ).text

    def _log_cost(self, completion: Completion, **context) -> None:
        """A cost record that cannot be written must not cost the answer."""
        try:
            self.cost_tracker.log(
                provider=completion.provider,
                model=completion.model,
                tokens_in=completion.usage.input_tokens,
                tokens_out=completion.usage.output_tokens,
                cached_tokens=completion.usage.cached_input_tokens,
                **context,
            )
        except Exception as e:
            _log_error(str(e), "I8 LLM Cost Log Error")

    def _announce(self, error: LLMError) -> None:
        """Tell the operator, at most once an hour per provider.

        Scheduled jobs retry, and an alert on every retry is an alert nobody
        reads. Nothing in here may replace the original failure: the caller
        still has to see the provider's own error.
        """
        _log_error(str(error), f"I8 LLM Error ({error.provider})")
        try:
            key = f"{ALERT_KEY}:{error.provider}"
            if frappe.cache.get_value(key):
                return
            frappe.cache.set_value(key, "sent", expires_in_sec=ALERT_SILENCE_SECONDS)
            _tell_operator(f"O provedor {error.provider} recusou as chamadas: {error}")
        except Exception:
            pass


def _tell_operator(message: str) -> None:
    from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot

    chat_id = frappe.db.get_single_value("I8 Agent Settings", "telegram_chat_id")
    if chat_id:
        TelegramBot().send_message(chat_id, message)


def _log_error(message: str, title: str) -> None:
    try:
        frappe.log_error(message, title)
    except Exception:
        pass
