"""
Anomaly Detection — Proactive checks for financial irregularities.

Runs as part of the planning loop. Uses a cache to avoid re-alerting
the same anomaly every day. New anomalies are formatted by J.A.R.V.I.S.
with a natural, conversational tone.
"""

from datetime import date, timedelta

import anthropic
import frappe
from frappe.utils import flt


JARVIS_ANOMALY_PROMPT = """You are J.A.R.V.I.S., the AI financial assistant.
Format the anomaly alerts below into a natural, conversational Telegram message.
Rules:
- Address the user by name
- Be direct but not alarming for medium-priority items
- For high-priority items (duplicates), be more urgent
- Add context: explain WHY this might be an issue and suggest action
- Keep it concise — max 1500 chars
- Write in Brazilian Portuguese
- Use Markdown for Telegram (bold, italic)
- Sign off as "J.A.R.V.I.S."
"""


def daily_anomaly_check():
    """Run all anomaly checks. Called from planning loop.

    Uses a monthly cache to avoid re-alerting the same anomaly daily.
    Only truly NEW anomalies (not seen this month) are reported.
    """
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return

    anomalies = []
    anomalies.extend(_check_nf_po_value_mismatch())
    anomalies.extend(_check_duplicate_payments())
    anomalies.extend(_check_unexpected_charges())
    anomalies.extend(_check_price_variations())

    # Filter out already-alerted anomalies this month
    new_anomalies = _filter_new_anomalies(anomalies)

    if new_anomalies:
        _notify_anomalies(new_anomalies)
        _mark_as_alerted(new_anomalies)


def _filter_new_anomalies(anomalies: list) -> list:
    """Filter out anomalies that were already alerted this month."""
    cache_key = f"i8:anomalies_alerted:{date.today().strftime('%Y-%m')}"
    alerted = frappe.cache.get_value(cache_key) or []
    if isinstance(alerted, str):
        import json
        alerted = json.loads(alerted)

    new = []
    for a in anomalies:
        # Create a unique key per anomaly
        anomaly_key = f"{a['type']}:{a.get('key', a['message'][:50])}"
        if anomaly_key not in alerted:
            a["_key"] = anomaly_key
            new.append(a)
    return new


def _mark_as_alerted(anomalies: list) -> None:
    """Mark anomalies as alerted for this month."""
    import json
    cache_key = f"i8:anomalies_alerted:{date.today().strftime('%Y-%m')}"
    alerted = frappe.cache.get_value(cache_key) or []
    if isinstance(alerted, str):
        alerted = json.loads(alerted)

    for a in anomalies:
        key = a.get("_key", f"{a['type']}:{a['message'][:50]}")
        if key not in alerted:
            alerted.append(key)

    frappe.cache.set_value(cache_key, json.dumps(alerted), expires_in_sec=86400 * 35)


def _check_nf_po_value_mismatch() -> list:
    """Find NFs where valor_total differs significantly from linked PO grand_total."""
    results = []
    try:
        nfs = frappe.db.sql("""
            SELECT nf.name, nf.valor_total, nf.razao_social,
                   po.name as po_name, po.grand_total as po_total
            FROM `tabNota Fiscal` nf
            JOIN `tabPurchase Order` po ON po.nota_fiscal = nf.name
            WHERE nf.creation >= %s
            AND nf.processing_status != 'Cancelled'
        """, (date.today() - timedelta(days=30)).isoformat(), as_dict=True)

        for nf in nfs:
            nf_val = flt(nf.get("valor_total") or 0)
            po_val = flt(nf.get("po_total") or 0)
            if po_val > 0:
                diff_pct = abs(nf_val - po_val) / po_val * 100
                if diff_pct > 5:
                    results.append({
                        "type": "nf_po_mismatch",
                        "severity": "high" if diff_pct > 20 else "medium",
                        "key": f"{nf['name']}:{nf['po_name']}",
                        "message": (
                            f"NF {nf['name']} ({nf.get('razao_social', '')[:30]}): "
                            f"R$ {nf_val:,.2f} vs PO {nf['po_name']} R$ {po_val:,.2f} "
                            f"(diferenca {diff_pct:.1f}%)"
                        ),
                    })
    except Exception as e:
        frappe.log_error(str(e), "I8 Anomaly: NF-PO Mismatch Check Error")
    return results


def _check_duplicate_payments() -> list:
    """Find potential duplicate payments (same supplier + similar amount in 7 days)."""
    results = []
    try:
        duplicates = frappe.db.sql("""
            SELECT pe1.name as pe1, pe2.name as pe2,
                   pe1.party, pe1.party_name, pe1.paid_amount,
                   pe1.posting_date as date1, pe2.posting_date as date2
            FROM `tabPayment Entry` pe1
            JOIN `tabPayment Entry` pe2
                ON pe1.party = pe2.party
                AND pe1.name < pe2.name
                AND ABS(pe1.paid_amount - pe2.paid_amount) < pe1.paid_amount * 0.01
                AND ABS(DATEDIFF(pe1.posting_date, pe2.posting_date)) <= 7
            WHERE pe1.docstatus = 1 AND pe2.docstatus = 1
            AND pe1.payment_type = 'Pay' AND pe2.payment_type = 'Pay'
            AND pe1.posting_date >= %s
        """, (date.today() - timedelta(days=30)).isoformat(), as_dict=True)

        for dup in duplicates:
            results.append({
                "type": "duplicate_payment",
                "severity": "high",
                "key": f"{dup['pe1']}:{dup['pe2']}",
                "message": (
                    f"Possivel pagamento duplicado: {dup['party_name'][:30]} "
                    f"R$ {flt(dup['paid_amount']):,.2f} — "
                    f"{dup['pe1']} ({dup['date1']}) e {dup['pe2']} ({dup['date2']})"
                ),
            })
    except Exception as e:
        frappe.log_error(str(e), "I8 Anomaly: Duplicate Payment Check Error")
    return results


def _check_unexpected_charges() -> list:
    """Find Purchase Invoices without a corresponding PO or Recurring Expense."""
    results = []
    try:
        pis = frappe.db.sql("""
            SELECT pi.name, pi.supplier, pi.supplier_name, pi.grand_total, pi.posting_date
            FROM `tabPurchase Invoice` pi
            WHERE pi.docstatus = 1
            AND pi.posting_date >= %s
            AND NOT EXISTS (
                SELECT 1 FROM `tabPurchase Invoice Item` pii
                WHERE pii.parent = pi.name AND pii.purchase_order IS NOT NULL AND pii.purchase_order != ''
            )
        """, (date.today() - timedelta(days=30)).isoformat(), as_dict=True)

        for pi in pis:
            has_recurring = frappe.db.exists(
                "I8 Recurring Expense",
                {"supplier": pi["supplier"], "active": 1},
            )
            if not has_recurring:
                results.append({
                    "type": "unexpected_charge",
                    "severity": "medium",
                    "key": pi["name"],
                    "message": (
                        f"Fatura sem PO/recorrente: {pi['name']} "
                        f"{pi.get('supplier_name', '')[:30]} "
                        f"R$ {flt(pi['grand_total']):,.2f} ({pi['posting_date']})"
                    ),
                })
    except Exception as e:
        frappe.log_error(str(e), "I8 Anomaly: Unexpected Charge Check Error")
    return results


def _check_price_variations() -> list:
    """Find items where the latest purchase price varies significantly from average.

    Only flags variations on invoices created in the last 7 days (not repeating daily).
    """
    results = []
    try:
        variations = frappe.db.sql("""
            SELECT pii.item_code, pii.item_name, pii.rate as current_rate,
                   AVG(pii2.rate) as avg_rate, COUNT(pii2.name) as history_count,
                   pi.supplier_name, pi.name as invoice_name
            FROM `tabPurchase Invoice Item` pii
            JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
            JOIN `tabPurchase Invoice Item` pii2 ON pii2.item_code = pii.item_code
            JOIN `tabPurchase Invoice` pi2 ON pi2.name = pii2.parent AND pi2.docstatus = 1
            WHERE pi.docstatus = 1
            AND pi.posting_date >= %s
            AND pi2.posting_date >= %s
            GROUP BY pii.item_code, pii.rate, pi.supplier_name, pi.name
            HAVING history_count >= 3 AND ABS(pii.rate - avg_rate) / avg_rate > 0.15
            LIMIT 10
        """, (
            (date.today() - timedelta(days=7)).isoformat(),
            (date.today() - timedelta(days=180)).isoformat(),
        ), as_dict=True)

        for v in variations:
            diff_pct = abs(v["current_rate"] - v["avg_rate"]) / v["avg_rate"] * 100
            direction = "acima" if v["current_rate"] > v["avg_rate"] else "abaixo"
            results.append({
                "type": "price_variation",
                "severity": "medium",
                "key": f"{v.get('invoice_name', '')}:{v['item_code']}",
                "message": (
                    f"Preco {direction} da media: {v.get('item_name', v['item_code'])[:30]} "
                    f"R$ {flt(v['current_rate']):,.2f} vs media R$ {flt(v['avg_rate']):,.2f} "
                    f"({diff_pct:.0f}% — {v.get('supplier_name', '')[:20]})"
                ),
            })
    except Exception as e:
        frappe.log_error(str(e), "I8 Anomaly: Price Variation Check Error")
    return results


def _notify_anomalies(anomalies: list) -> None:
    """Send anomaly alerts via Telegram using J.A.R.V.I.S. personality."""
    # Get user name
    user_name = "chefe"
    try:
        settings = frappe.get_single("I8 Agent Settings")
        for user_row in (settings.telegram_users or []):
            if user_row.active and user_row.user:
                first_name = frappe.db.get_value("User", user_row.user, "first_name")
                if first_name:
                    user_name = first_name
                    break
    except Exception:
        pass

    # Build raw data for LLM
    high = [a for a in anomalies if a["severity"] == "high"]
    medium = [a for a in anomalies if a["severity"] == "medium"]

    raw_lines = [f"Date: {date.today().isoformat()}", f"User: {user_name}", ""]
    if high:
        raw_lines.append(f"HIGH PRIORITY ({len(high)}):")
        for a in high:
            raw_lines.append(f"  - [{a['type']}] {a['message']}")
    if medium:
        raw_lines.append(f"MEDIUM PRIORITY ({len(medium)}):")
        for a in medium:
            raw_lines.append(f"  - [{a['type']}] {a['message']}")

    raw_data = "\n".join(raw_lines)

    # Format with J.A.R.V.I.S.
    formatted = _format_with_jarvis(raw_data, user_name)
    message = formatted or _fallback_format(anomalies)

    try:
        from brazil_module.services.intelligence.recurring.planning_loop import _notify_telegram
        _notify_telegram(message)
    except Exception:
        pass

    try:
        from brazil_module.services.intelligence.notifications import notify_desk
        notify_desk(
            title=f"J.A.R.V.I.S.: {len(anomalies)} anomalias detectadas",
            message=message[:200],
        )
    except Exception:
        pass


def _format_with_jarvis(raw_data: str, user_name: str) -> str | None:
    """Format anomaly report with J.A.R.V.I.S. personality via Haiku."""
    try:
        from brazil_module.intelligence8.doctype.i8_agent_settings.i8_agent_settings import I8AgentSettings
        settings = I8AgentSettings.get_settings()

        client = anthropic.Anthropic(api_key=I8AgentSettings.get_api_key())
        response = client.messages.create(
            model=settings.haiku_model or "claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=JARVIS_ANOMALY_PROMPT,
            messages=[{"role": "user", "content": raw_data}],
        )

        # Log cost
        from brazil_module.services.intelligence.cost_tracker import CostTracker
        CostTracker().log(
            model=settings.haiku_model or "claude-haiku-4-5-20251001",
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            latency_ms=0,
            module="anomaly",
            function_name="jarvis_anomaly_format",
        )

        return response.content[0].text.strip()

    except Exception as e:
        frappe.log_error(str(e), "I8 JARVIS Anomaly Format Error")
        return None


def _fallback_format(anomalies: list) -> str:
    """Fallback formatting when LLM is not available."""
    high = [a for a in anomalies if a["severity"] == "high"]
    medium = [a for a in anomalies if a["severity"] == "medium"]

    lines = [f"*Anomalias detectadas: {len(anomalies)}*\n"]
    if high:
        lines.append(f"*ALTA prioridade ({len(high)}):*")
        for a in high:
            lines.append(f"  - {a['message']}")
        lines.append("")
    if medium:
        lines.append(f"*Media prioridade ({len(medium)}):*")
        for a in medium[:5]:
            lines.append(f"  - {a['message']}")
        if len(medium) > 5:
            lines.append(f"  ... e mais {len(medium) - 5}")

    return "\n".join(lines)
