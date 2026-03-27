"""
Auto-reconciliation service.

Matches Bank Transactions to ERPNext documents (Sales Invoice,
Purchase Invoice, Payment Entry, Journal Entry, Expense Claim).
"""

from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import flt


def on_payment_entry_submit(doc, method=None):
    """Hook: when a Payment Entry is submitted, check if it's linked to Inter documents."""
    if doc.get("inter_payment_order"):
        # Update the Payment Order status
        frappe.db.set_value(
            "Inter Payment Order",
            doc.inter_payment_order,
            "status",
            "Completed",
        )


def batch_reconcile(bank_account: str, date_from: date | None = None) -> dict:
    """Run auto-reconciliation on unmatched Bank Transactions.

    Args:
        bank_account: ERPNext Bank Account name.
        date_from: Only reconcile transactions from this date onwards.

    Returns:
        Dict with reconciliation results.
    """
    filters = {
        "bank_account": bank_account,
        "docstatus": 1,
        "unallocated_amount": [">", 0],
    }
    if date_from:
        filters["date"] = [">=", date_from]

    transactions = frappe.get_all(
        "Bank Transaction",
        filters=filters,
        fields=["name", "date", "deposit", "withdrawal", "description", "reference_number"],
        order_by="date asc",
    )

    results = {"matched": 0, "unmatched": 0, "errors": 0}

    for txn in transactions:
        try:
            match = _find_match(txn, bank_account)
            if match:
                _allocate_transaction(txn["name"], match["doctype"], match["name"], match["amount"])
                results["matched"] += 1
            else:
                results["unmatched"] += 1
        except Exception as e:
            results["errors"] += 1
            frappe.log_error(str(e), f"Inter Reconciliation Error: {txn['name']}")

    return results


def _find_match(txn: dict, bank_account: str) -> dict | None:
    """Find the best matching document for a Bank Transaction.

    Matching strategies (in priority order):
    1. Exact reference number match (nosso_numero, txid, e2e_id)
    2. Amount + date range match with party
    """
    amount = flt(txn.get("deposit") or txn.get("withdrawal") or 0)
    reference = txn.get("reference_number", "")
    txn_date = txn.get("date")

    if not amount:
        return None

    # Strategy 1: Match by reference to Inter Boleto
    if reference:
        match = _match_by_inter_reference(reference, amount)
        if match:
            return match

    # Strategy 2: Match by amount to outstanding invoices
    is_credit = flt(txn.get("deposit", 0)) > 0
    description = txn.get("description", "")

    # Strategy 2: Match to Payment Entry by amount + party name in description
    match = _match_to_payment_entry(amount, txn_date, description, is_credit)
    if match:
        return match

    # Strategy 3: Match by amount to outstanding invoices
    if is_credit:
        match = _match_to_sales_invoice(amount, txn_date)
        if match:
            return match
    else:
        match = _match_to_purchase_invoice(amount, txn_date)
        if match:
            return match

    return None


def _match_by_inter_reference(reference: str, amount: float) -> dict | None:
    """Match by Inter Boleto nosso_numero or PIX txid."""
    # Check Inter Boleto
    boleto = frappe.db.get_value(
        "Inter Boleto",
        {"nosso_numero": reference, "status": ["in", ["Registered", "Pending"]]},
        ["name", "valor_nominal", "sales_invoice"],
        as_dict=True,
    )
    if boleto and boleto.sales_invoice:
        return {
            "doctype": "Sales Invoice",
            "name": boleto.sales_invoice,
            "amount": amount,
        }

    # Check Inter PIX Charge
    pix = frappe.db.get_value(
        "Inter PIX Charge",
        {"txid": reference, "status": ["in", ["Active", "Pending"]]},
        ["name", "valor", "sales_invoice"],
        as_dict=True,
    )
    if pix and pix.sales_invoice:
        return {
            "doctype": "Sales Invoice",
            "name": pix.sales_invoice,
            "amount": amount,
        }

    return None


def _match_to_sales_invoice(amount: float, txn_date: date) -> dict | None:
    """Match a credit transaction to an outstanding Sales Invoice by amount."""
    settings = frappe.get_single("Banco Inter Settings")
    tolerance = flt(settings.reconcile_tolerance_percent or 1) / 100

    min_amount = amount * (1 - tolerance)
    max_amount = amount * (1 + tolerance)

    filters = {
        "docstatus": 1,
        "outstanding_amount": ["between", [min_amount, max_amount]],
    }
    if txn_date:
        filters["posting_date"] = [">=", txn_date - timedelta(days=60)]

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name", "outstanding_amount", "posting_date"],
        order_by="posting_date desc",
        limit=10,
    )

    if invoices:
        best = min(invoices, key=lambda inv: abs(flt(inv["outstanding_amount"]) - amount))
        return {
            "doctype": "Sales Invoice",
            "name": best["name"],
            "amount": amount,
        }
    return None


def _match_to_purchase_invoice(amount: float, txn_date: date) -> dict | None:
    """Match a debit transaction to an outstanding Purchase Invoice by amount."""
    settings = frappe.get_single("Banco Inter Settings")
    tolerance = flt(settings.reconcile_tolerance_percent or 1) / 100

    min_amount = amount * (1 - tolerance)
    max_amount = amount * (1 + tolerance)

    filters = {
        "docstatus": 1,
        "outstanding_amount": ["between", [min_amount, max_amount]],
    }
    if txn_date:
        filters["posting_date"] = [">=", txn_date - timedelta(days=60)]

    invoices = frappe.get_all(
        "Purchase Invoice",
        filters=filters,
        fields=["name", "outstanding_amount", "posting_date"],
        order_by="posting_date desc",
        limit=10,
    )

    if invoices:
        best = min(invoices, key=lambda inv: abs(flt(inv["outstanding_amount"]) - amount))
        return {
            "doctype": "Purchase Invoice",
            "name": best["name"],
            "amount": amount,
        }
    return None


def _match_to_payment_entry(amount: float, txn_date, description: str, is_credit: bool) -> dict | None:
    """Match a bank transaction to a Payment Entry by amount and party name.

    Looks for submitted Payment Entries that:
    - Have matching amount (within tolerance)
    - Are not yet reconciled (no bank transaction linked)
    - Optionally match party name found in transaction description
    """
    settings = frappe.get_single("Banco Inter Settings")
    tolerance = flt(settings.reconcile_tolerance_percent or 1) / 100

    min_amount = amount * (1 - tolerance)
    max_amount = amount * (1 + tolerance)

    payment_type = "Receive" if is_credit else "Pay"

    filters = {
        "docstatus": 1,
        "payment_type": payment_type,
        "paid_amount": ["between", [min_amount, max_amount]],
        "clearance_date": ["is", "not set"],
    }
    if txn_date:
        if isinstance(txn_date, str):
            from datetime import date as _date
            try:
                txn_date = _date.fromisoformat(txn_date)
            except ValueError:
                txn_date = None
        if txn_date:
            filters["posting_date"] = [">=", txn_date - timedelta(days=30)]

    entries = frappe.get_all(
        "Payment Entry",
        filters=filters,
        fields=["name", "paid_amount", "party", "party_name", "posting_date", "reference_no"],
        order_by="posting_date desc",
        limit=10,
    )

    if not entries:
        return None

    # Try to match by party name in description
    description_lower = description.lower()
    for entry in entries:
        party_name = (entry.get("party_name") or entry.get("party") or "").lower()
        if party_name and len(party_name) > 3:
            # Check if any significant part of party name appears in description
            name_parts = [p for p in party_name.split() if len(p) > 3]
            if any(part in description_lower for part in name_parts):
                return {
                    "doctype": "Payment Entry",
                    "name": entry["name"],
                    "amount": amount,
                }

    # Fallback: best match by amount (closest)
    if len(entries) == 1:
        return {
            "doctype": "Payment Entry",
            "name": entries[0]["name"],
            "amount": amount,
        }

    return None


def _allocate_transaction(
    bank_transaction_name: str,
    matched_doctype: str,
    matched_name: str,
    amount: float,
):
    """Allocate a Bank Transaction to a matched document."""
    bt = frappe.get_doc("Bank Transaction", bank_transaction_name)

    bt.append("payment_entries", {
        "payment_document": matched_doctype,
        "payment_entry": matched_name,
        "allocated_amount": amount,
    })

    bt.save(ignore_permissions=True)
    frappe.db.commit()
