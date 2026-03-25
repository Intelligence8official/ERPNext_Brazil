from datetime import datetime

import frappe


class DecisionEngine:
    """Evaluates agent actions against confidence thresholds and audits every decision."""

    def __init__(self, settings=None):
        self._settings = settings or frappe.get_single("I8 Agent Settings")

    def evaluate(
        self,
        action: str,
        doctype: str,
        confidence: float,
        amount: float = 0,
        custom_threshold: float | None = None,
    ) -> dict:
        """Return auto_approve decision based on confidence, action type, and value rules.

        Args:
            action: The operation to perform (e.g. "create", "submit", "cancel").
            doctype: Target Frappe DocType name (for context only, not evaluated here).
            confidence: Model confidence score in [0, 1].
            amount: Monetary amount for high-value gate (default 0).
            custom_threshold: Overrides settings.default_confidence_threshold when provided.

        Returns:
            dict with keys: auto_approve (bool), confidence (float), threshold (float).
        """
        threshold = (
            custom_threshold
            if custom_threshold is not None
            else self._settings.default_confidence_threshold
        )

        requires_human = (
            confidence < threshold
            or action in ("submit", "cancel")
            or (
                self._settings.high_value_confirmation_pin
                and amount > float(self._settings.high_value_threshold or 0)
            )
        )

        return {
            "auto_approve": not requires_human,
            "confidence": confidence,
            "threshold": threshold,
        }

    def log_decision(
        self,
        event_type: str,
        module: str,
        action: str,
        actor: str,
        channel: str,
        confidence: float,
        model: str,
        input_summary: str,
        reasoning: str,
        result: str,
        related_doctype: str | None = None,
        related_docname: str | None = None,
        cost_usd: float = 0,
        human_override: bool = False,
        human_feedback: str | None = None,
    ) -> str:
        """Create an I8 Decision Log entry and submit it unless result is Pending.

        Args:
            event_type: Category of triggering event (e.g. "recurring", "webhook").
            module: Business module identifier (e.g. "p2p", "fiscal").
            action: Action taken by the agent.
            actor: Name of the agent or user responsible.
            channel: Input channel (e.g. "system", "email", "api").
            confidence: Model confidence score used for the decision.
            model: Model identifier string (e.g. "claude-haiku-4-5-20251001").
            input_summary: Brief description of the input data processed.
            reasoning: Explanation of why the decision was made.
            result: Outcome status — "Success", "Failure", or "Pending".
            related_doctype: Optional linked DocType name.
            related_docname: Optional linked document name.
            cost_usd: Estimated LLM cost in USD (default 0).
            human_override: Whether a human overrode the agent's decision.
            human_feedback: Optional free-text feedback from the human reviewer.

        Returns:
            The name (ID) of the created I8 Decision Log document.
        """
        doc = frappe.new_doc("I8 Decision Log")
        doc.timestamp = datetime.now()
        doc.event_type = event_type
        doc.module = module
        doc.action = action
        doc.actor = actor
        doc.channel = channel
        doc.confidence_score = confidence
        doc.model_used = model
        doc.input_summary = input_summary
        doc.reasoning = reasoning
        doc.result = result
        doc.related_doctype = related_doctype
        doc.related_docname = related_docname
        doc.cost_usd = cost_usd
        doc.human_override = human_override
        doc.human_feedback = human_feedback
        doc.insert(ignore_permissions=True)
        if result != "Pending":
            doc.submit()
        return doc.name
