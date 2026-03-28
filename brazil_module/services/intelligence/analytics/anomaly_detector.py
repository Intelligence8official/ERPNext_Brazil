"""
Anomaly Detection — Proactive checks for financial irregularities.

Runs as part of the planning loop (daily) and alerts via Telegram.
"""

from datetime import date, timedelta

import frappe
from frappe.utils import flt


def daily_anomaly_check():
    """Run all anomaly checks. Called from planning loop."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return

    anomalies = []
    anomalies.extend(_check_nf_po_value_mismatch())
    anomalies.extend(_check_duplicate_payments())
    anomalies.extend(_check_unexpected_charges())
    anomalies.extend(_check_price_variations())

    if anomalies:
        _notify_anomalies(anomalies)


def _check_nf_po_value_mismatch() -> list:
    """Find NFs where valor_total differs significantly from linked PO grand_total."""
    results = []
    try:
        # Get NFs with linked POs from last 30 days
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
                if diff_pct > 5:  # More than 5% difference
                    results.append({
                        "type": "nf_po_mismatch",
                        "severity": "high" if diff_pct > 20 else "medium",
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
        # PIs without PO link from last 30 days
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
            # Check if supplier has a recurring expense
            has_recurring = frappe.db.exists(
                "I8 Recurring Expense",
                {"supplier": pi["supplier"], "active": 1},
            )
            if not has_recurring:
                results.append({
                    "type": "unexpected_charge",
                    "severity": "medium",
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
    """Find items where the latest purchase price varies significantly from average."""
    results = []
    try:
        variations = frappe.db.sql("""
            SELECT pii.item_code, pii.item_name, pii.rate as current_rate,
                   AVG(pii2.rate) as avg_rate, COUNT(pii2.name) as history_count,
                   pi.supplier_name
            FROM `tabPurchase Invoice Item` pii
            JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
            JOIN `tabPurchase Invoice Item` pii2 ON pii2.item_code = pii.item_code
            JOIN `tabPurchase Invoice` pi2 ON pi2.name = pii2.parent AND pi2.docstatus = 1
            WHERE pi.docstatus = 1
            AND pi.posting_date >= %s
            AND pi2.posting_date >= %s
            GROUP BY pii.item_code, pii.rate, pi.supplier_name
            HAVING history_count >= 3 AND ABS(pii.rate - avg_rate) / avg_rate > 0.15
            LIMIT 10
        """, (
            (date.today() - timedelta(days=30)).isoformat(),
            (date.today() - timedelta(days=180)).isoformat(),
        ), as_dict=True)

        for v in variations:
            diff_pct = abs(v["current_rate"] - v["avg_rate"]) / v["avg_rate"] * 100
            direction = "acima" if v["current_rate"] > v["avg_rate"] else "abaixo"
            results.append({
                "type": "price_variation",
                "severity": "medium",
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
    """Send anomaly alerts via Telegram and desk notification."""
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
        for a in medium[:5]:  # Limit to 5
            lines.append(f"  - {a['message']}")
        if len(medium) > 5:
            lines.append(f"  ... e mais {len(medium) - 5}")

    try:
        from brazil_module.services.intelligence.recurring.planning_loop import _notify_telegram
        _notify_telegram("\n".join(lines))
    except Exception:
        pass

    try:
        from brazil_module.services.intelligence.notifications import notify_desk
        notify_desk(
            title=f"I8: {len(anomalies)} anomalias detectadas",
            message=f"{len(high)} alta prioridade, {len(medium)} media prioridade",
        )
    except Exception:
        pass
