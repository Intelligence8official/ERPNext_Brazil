def format_approval_message(decision: dict) -> dict:
    confidence = decision.get("confidence", 0)
    text = (
        f"Aprovacao necessaria\n"
        f"Acao: {decision['action']}\n"
        f"Documento: {decision.get('related_doctype', '')} {decision.get('related_docname', '')}\n"
        f"Valor: {decision.get('amount', 'N/A')}\n"
        f"Confianca: {confidence:.0%}\n"
        f"Motivo: {decision.get('reasoning', '')}"
    )
    log_name = decision.get("decision_log_name", "")
    reply_markup = {
        "inline_keyboard": [[
            {"text": "Aprovar", "callback_data": f"approve:{log_name}"},
            {"text": "Rejeitar", "callback_data": f"reject:{log_name}"},
            {"text": "Detalhes", "callback_data": f"details:{log_name}"},
        ]]
    }
    return {"text": text, "reply_markup": reply_markup}
