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


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "banking-create_payment":
        pi = frappe.get_doc("Purchase Invoice", args["purchase_invoice"])
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
