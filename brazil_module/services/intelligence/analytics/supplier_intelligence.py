"""
Supplier Intelligence — Automated supplier reliability scoring.

Calculates scores based on:
- NF delivery time (vs expected)
- Value accuracy (NF vs PO)
- Follow-up frequency
- Price trend
"""

from datetime import date, timedelta

import frappe
from frappe.utils import flt


def update_supplier_scores():
    """Update reliability scores for all suppliers with I8 config."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return

    suppliers = frappe.get_all(
        "Supplier",
        filters={"i8_expected_nf_days": [">", 0]},
        fields=["name", "supplier_name", "i8_expected_nf_days"],
    )

    for supplier in suppliers:
        try:
            score = calculate_supplier_score(supplier["name"])
            # Score is 0-1, stored as rating (0-5 scale in Frappe)
            # But we don't have a rating field, so store as score 0-100
            # Use i8_agent_notes to store the score summary
            summary = _build_score_summary(supplier["name"], score)
            frappe.db.set_value("Supplier", supplier["name"], "i8_agent_notes", summary)
        except Exception as e:
            frappe.log_error(str(e), f"I8 Supplier Score Error: {supplier['name']}")

    frappe.db.commit()


def calculate_supplier_score(supplier_name: str) -> dict:
    """Calculate detailed supplier score."""
    scores = {
        "nf_delivery": _score_nf_delivery(supplier_name),
        "value_accuracy": _score_value_accuracy(supplier_name),
        "overall": 0,
    }

    # Weighted average
    weights = {"nf_delivery": 0.5, "value_accuracy": 0.5}
    total_weight = 0
    weighted_sum = 0
    for key, weight in weights.items():
        if scores[key] is not None:
            weighted_sum += scores[key] * weight
            total_weight += weight

    scores["overall"] = round(weighted_sum / total_weight * 100) if total_weight > 0 else 0
    return scores


def _score_nf_delivery(supplier_name: str) -> float | None:
    """Score based on how fast supplier delivers NFs after PO.

    1.0 = always on time or early
    0.5 = average delays
    0.0 = always very late
    """
    expected_days = frappe.db.get_value("Supplier", supplier_name, "i8_expected_nf_days") or 30

    # Get POs and their linked NFs from last 6 months
    pos = frappe.db.sql("""
        SELECT po.name, po.transaction_date, nf.creation as nf_date
        FROM `tabPurchase Order` po
        LEFT JOIN `tabNota Fiscal` nf ON nf.purchase_order = po.name
        WHERE po.supplier = %s
        AND po.docstatus = 1
        AND po.transaction_date >= %s
    """, (supplier_name, (date.today() - timedelta(days=180)).isoformat()), as_dict=True)

    if not pos:
        return None

    on_time = 0
    total = 0
    for po in pos:
        if po.get("nf_date") and po.get("transaction_date"):
            days_took = (po["nf_date"].date() - po["transaction_date"]).days if hasattr(po["nf_date"], "date") else 0
            total += 1
            if days_took <= expected_days:
                on_time += 1
            elif days_took <= expected_days * 1.5:
                on_time += 0.5

    return on_time / total if total > 0 else None


def _score_value_accuracy(supplier_name: str) -> float | None:
    """Score based on NF value matching PO value.

    1.0 = always matches within 1%
    0.5 = typical 5-10% variations
    0.0 = frequent large mismatches
    """
    matches = frappe.db.sql("""
        SELECT nf.valor_total as nf_value, po.grand_total as po_value
        FROM `tabNota Fiscal` nf
        JOIN `tabPurchase Order` po ON po.nota_fiscal = nf.name
        WHERE po.supplier = %s
        AND po.docstatus = 1
        AND nf.creation >= %s
    """, (supplier_name, (date.today() - timedelta(days=180)).isoformat()), as_dict=True)

    if not matches:
        return None

    accurate = 0
    for m in matches:
        nf_val = flt(m.get("nf_value") or 0)
        po_val = flt(m.get("po_value") or 0)
        if po_val > 0:
            diff_pct = abs(nf_val - po_val) / po_val * 100
            if diff_pct <= 1:
                accurate += 1
            elif diff_pct <= 5:
                accurate += 0.7
            elif diff_pct <= 10:
                accurate += 0.3

    return accurate / len(matches) if matches else None


def _build_score_summary(supplier_name: str, scores: dict) -> str:
    """Build a human-readable score summary for the agent notes field."""
    lines = [
        f"=== Supplier Intelligence Score (updated {date.today().isoformat()}) ===",
        f"Overall: {scores['overall']}%",
    ]

    if scores["nf_delivery"] is not None:
        lines.append(f"NF Delivery: {scores['nf_delivery']:.0%}")
    else:
        lines.append("NF Delivery: insufficient data")

    if scores["value_accuracy"] is not None:
        lines.append(f"Value Accuracy: {scores['value_accuracy']:.0%}")
    else:
        lines.append("Value Accuracy: insufficient data")

    return "\n".join(lines)
