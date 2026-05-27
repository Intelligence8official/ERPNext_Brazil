import frappe

TOOL_SCHEMAS = [
    {
        "name": "banking-create_payment",
        "description": "Create a payment for a Purchase Invoice via Banco Inter",
        "input_schema": {
            "type": "object",
            "properties": {
                "purchase_invoice": {"type": "string"},
                "payment_method": {"type": "string", "enum": ["PIX", "TED", "Boleto"]},
            },
            "required": ["purchase_invoice", "payment_method"],
        },
    },
    {
        "name": "banking-get_balance",
        "description": "Get current bank account balance",
        "input_schema": {
            "type": "object",
            "properties": {
                "bank_account": {"type": "string", "description": "Bank Account name"},
            },
            "required": ["bank_account"],
        },
    },
    {
        "name": "banking-reconcile_transactions",
        "description": "Run auto-reconciliation on unmatched bank transactions",
        "input_schema": {
            "type": "object",
            "properties": {
                "bank_account": {"type": "string"},
            },
            "required": ["bank_account"],
        },
    },
]


def _has_existing_payment(purchase_invoice: str) -> dict | None:
    """Check if a Payment Entry or Inter Payment Order already exists for this PI.

    Returns:
        Dict with details of existing payment if found, None otherwise.
    """
    # Check for Payment Entry (draft or submitted)
    existing_pe = frappe.db.sql("""
        SELECT pe.name, pe.docstatus
        FROM `tabPayment Entry Reference` per
        JOIN `tabPayment Entry` pe ON pe.name = per.parent
        WHERE per.reference_name = %s
        AND pe.docstatus < 2
    """, purchase_invoice, as_dict=True)

    if existing_pe:
        return {
            "type": "Payment Entry",
            "name": existing_pe[0]["name"],
            "status": "Submitted" if existing_pe[0]["docstatus"] == 1 else "Draft",
        }

    # Check for Inter Payment Order (any non-cancelled)
    existing_ipo = frappe.db.get_value(
        "Inter Payment Order",
        {"purchase_invoice": purchase_invoice, "docstatus": ["<", 2]},
        ["name", "status"],
        as_dict=True,
    )

    if existing_ipo:
        return {
            "type": "Inter Payment Order",
            "name": existing_ipo["name"],
            "status": existing_ipo["status"],
        }

    return None


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "banking-create_payment":
        pi_name = args["purchase_invoice"]

        # Guard: prevent duplicate payments
        existing = _has_existing_payment(pi_name)
        if existing:
            return {
                "status": "skipped",
                "reason": "duplicate_payment",
                "message": (
                    f"Payment already exists for {pi_name}: "
                    f"{existing['type']} {existing['name']} ({existing['status']})"
                ),
                "existing_payment": existing,
            }

        pi = frappe.get_doc("Purchase Invoice", pi_name)

        if pi.outstanding_amount <= 0:
            return {
                "status": "skipped",
                "reason": "no_outstanding_amount",
                "message": f"Purchase Invoice {pi_name} has no outstanding amount.",
            }

        payment_data = {
            "payment_type": "Pay",
            "party_type": "Supplier",
            "party": pi.supplier,
            "paid_amount": float(pi.outstanding_amount or pi.grand_total),
            "reference_no": pi.name,
            "reference_date": frappe.utils.today(),
        }
        return executor.execute("Payment Entry", "create", payment_data)
    elif tool_name == "banking-get_balance":
        balance = frappe.db.get_value(
            "Bank Account", args["bank_account"],
            ["account_name", "bank_balance"],
            as_dict=True,
        )
        return balance or {"error": "Bank account not found"}
    elif tool_name == "banking-reconcile_transactions":
        return executor.execute("Bank Transaction", "reconcile", {"bank_account": args["bank_account"]})
    raise ValueError(f"Unknown tool: {tool_name}")
