import frappe

TOOL_SCHEMAS = [
    {
        "name": "fiscal-link_nf_to_po",
        "description": "Link a Nota Fiscal to a Purchase Order",
        "input_schema": {
            "type": "object",
            "properties": {
                "nota_fiscal": {"type": "string"},
                "purchase_order": {"type": "string"},
            },
            "required": ["nota_fiscal", "purchase_order"],
        },
    },
    {
        "name": "fiscal-create_purchase_invoice",
        "description": "Create Purchase Invoice from a Nota Fiscal",
        "input_schema": {
            "type": "object",
            "properties": {
                "nota_fiscal": {"type": "string"},
            },
            "required": ["nota_fiscal"],
        },
    },
    {
        "name": "fiscal-find_matching_pos",
        "description": "Find Purchase Orders that match a Nota Fiscal by supplier and amount",
        "input_schema": {
            "type": "object",
            "properties": {
                "nota_fiscal": {"type": "string", "description": "Nota Fiscal document name"},
                "supplier_cnpj": {"type": "string", "description": "Supplier CNPJ (alternative to nota_fiscal)"},
            },
        },
    },
]


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "fiscal-link_nf_to_po":
        frappe.db.set_value("Nota Fiscal", args["nota_fiscal"], "purchase_order", args["purchase_order"])
        frappe.db.set_value("Purchase Order", args["purchase_order"], "nota_fiscal", args["nota_fiscal"])
        return {"status": "linked", "nota_fiscal": args["nota_fiscal"], "purchase_order": args["purchase_order"]}
    elif tool_name == "fiscal-create_purchase_invoice":
        nf = frappe.get_doc("Nota Fiscal", args["nota_fiscal"])
        pi_data = {
            "supplier": nf.get("supplier") or nf.get("cnpj_emitente"),
            "bill_no": nf.get("numero"),
            "bill_date": nf.get("data_emissao"),
            "nota_fiscal": nf.name,
            "items": [{"item_code": "Services", "qty": 1, "rate": float(nf.get("valor_total") or 0)}],
        }
        return executor.execute("Purchase Invoice", "create", pi_data)
    elif tool_name == "fiscal-find_matching_pos":
        cnpj = args.get("supplier_cnpj", "")
        valor = 0
        if args.get("nota_fiscal") and frappe.db.exists("Nota Fiscal", args["nota_fiscal"]):
            nf = frappe.get_doc("Nota Fiscal", args["nota_fiscal"])
            cnpj = cnpj or nf.get("cnpj_emitente", "")
            valor = float(nf.get("valor_total") or 0)

        # Search by supplier (using CNPJ linked to Supplier)
        suppliers = frappe.get_all("Supplier", filters={"tax_id": ["like", f"%{cnpj}%"]}, pluck="name") if cnpj else []

        pos = []
        for supplier_name in suppliers:
            matches = frappe.get_all(
                "Purchase Order",
                filters={"supplier": supplier_name, "docstatus": 1, "status": ["not in", ["Completed", "Cancelled"]]},
                fields=["name", "supplier", "grand_total", "transaction_date", "status"],
                order_by="transaction_date desc",
                limit=5,
            )
            pos.extend(matches)

        return {"data": pos, "nf_amount": valor, "supplier_cnpj": cnpj}
    raise ValueError(f"Unknown tool: {tool_name}")
