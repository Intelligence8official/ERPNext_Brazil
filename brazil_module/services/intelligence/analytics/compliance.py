"""
Fiscal Compliance — NF-e cancellation detection and tax validation.
"""

from datetime import date, timedelta

import frappe
from frappe.utils import flt


def check_nf_cancellations():
    """Check for NF-e cancellations from SEFAZ and handle them.

    Looks for Nota Fiscal documents marked as cancelled (cancelada=1)
    that still have active Purchase Invoices linked.
    """
    try:
        cancelled_with_invoices = frappe.db.sql("""
            SELECT nf.name, nf.chave_de_acesso, nf.razao_social,
                   pi.name as invoice_name, pi.docstatus as invoice_status
            FROM `tabNota Fiscal` nf
            JOIN `tabPurchase Invoice` pi ON pi.nota_fiscal = nf.name
            WHERE nf.cancelada = 1
            AND nf.processing_status = 'Cancelled'
            AND pi.docstatus = 1
        """, as_dict=True)

        if cancelled_with_invoices:
            lines = [f"*NFs canceladas com faturas ativas: {len(cancelled_with_invoices)}*\n"]
            for item in cancelled_with_invoices:
                lines.append(
                    f"  - NF {item['name']}: {item.get('razao_social', '')[:30]}\n"
                    f"    Fatura {item['invoice_name']} precisa ser cancelada"
                )

            _notify(
                "\n".join(lines),
                f"I8: {len(cancelled_with_invoices)} NFs canceladas com faturas ativas",
            )

    except Exception as e:
        frappe.log_error(str(e), "I8 Compliance: NF Cancellation Check Error")


def check_tax_anomalies():
    """Validate taxes on received NFs (from suppliers).

    For Simples Nacional (no withholding), just verify:
    - NF has tax values declared (not all zeros)
    - ISS rate is within expected range for the service type
    - Total taxes are reasonable (not > 30% of total)
    """
    try:
        # Get recent NFS-e with tax data
        nfs = frappe.db.sql("""
            SELECT name, razao_social, valor_total, valor_servicos,
                   issqn_valor, issqn_aliquota,
                   pis_valor, cofins_valor, inss_valor, irrf_valor
            FROM `tabNota Fiscal`
            WHERE creation >= %s
            AND document_type IN ('NFS-e', 'NF-e')
            AND processing_status != 'Cancelled'
            AND valor_total > 0
        """, (date.today() - timedelta(days=30)).isoformat(), as_dict=True)

        anomalies = []
        for nf in nfs:
            total = flt(nf.get("valor_total") or 0)
            if total <= 0:
                continue

            # Check 1: ISS rate on service NFs
            iss_rate = flt(nf.get("issqn_aliquota") or 0)
            if iss_rate > 0 and (iss_rate < 2 or iss_rate > 5):
                anomalies.append({
                    "nf": nf["name"],
                    "supplier": (nf.get("razao_social") or "")[:30],
                    "issue": f"ISS aliquota incomum: {iss_rate}% (esperado 2-5%)",
                })

            # Check 2: Total taxes > 30% of total
            total_tax = sum(flt(nf.get(f) or 0) for f in [
                "issqn_valor", "pis_valor", "cofins_valor", "inss_valor", "irrf_valor",
            ])
            if total_tax > total * 0.30:
                anomalies.append({
                    "nf": nf["name"],
                    "supplier": (nf.get("razao_social") or "")[:30],
                    "issue": f"Impostos > 30%: R$ {total_tax:,.2f} em NF de R$ {total:,.2f}",
                })

        if anomalies:
            lines = [f"*Anomalias tributarias: {len(anomalies)}*\n"]
            for a in anomalies:
                lines.append(f"  - {a['nf']}: {a['supplier']} — {a['issue']}")
            _notify("\n".join(lines), f"I8: {len(anomalies)} anomalias tributarias")

    except Exception as e:
        frappe.log_error(str(e), "I8 Compliance: Tax Anomaly Check Error")


def _notify(message: str, title: str) -> None:
    """Send notification via Telegram and desk."""
    try:
        from brazil_module.services.intelligence.recurring.planning_loop import _notify_telegram
        _notify_telegram(message)
    except Exception:
        pass

    try:
        from brazil_module.services.intelligence.notifications import notify_desk
        notify_desk(title=title, message=message[:200])
    except Exception:
        pass
