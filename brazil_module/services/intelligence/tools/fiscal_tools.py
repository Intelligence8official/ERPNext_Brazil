import frappe

from brazil_module.services.intelligence.tools.purchasing_tools import _resolve_item_code

TOOL_SCHEMAS = [
    {
        "name": "fiscal-get_nf_details",
        "description": "Read full details of a Nota Fiscal including supplier, items, amounts, and current processing status",
        "input_schema": {
            "type": "object",
            "properties": {
                "nota_fiscal": {"type": "string", "description": "Nota Fiscal document name"},
            },
            "required": ["nota_fiscal"],
        },
    },
    {
        "name": "fiscal-find_matching_pos",
        "description": "Find Purchase Orders that match a Nota Fiscal by supplier CNPJ. Returns open POs for the supplier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nota_fiscal": {"type": "string", "description": "Nota Fiscal document name"},
                "supplier_cnpj": {"type": "string", "description": "Supplier CNPJ (alternative to nota_fiscal)"},
            },
        },
    },
    {
        "name": "fiscal-find_recurring_expense",
        "description": "Find an I8 Recurring Expense that matches a supplier. Used when no PO exists but the expense is recurring.",
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier_cnpj": {"type": "string", "description": "Supplier CNPJ"},
                "supplier_name": {"type": "string", "description": "Supplier name (alternative)"},
            },
        },
    },
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
        "description": "Create a Purchase Invoice from a Nota Fiscal. Works with or without a PO. Resolves supplier by CNPJ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nota_fiscal": {"type": "string", "description": "Nota Fiscal document name"},
                "purchase_order": {"type": "string", "description": "Optional: PO to link (for items/rates)"},
            },
            "required": ["nota_fiscal"],
        },
    },
    {
        "name": "fiscal-update_nf_status",
        "description": "Update the processing status of a Nota Fiscal (e.g., mark as processed, needs_review, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {
                "nota_fiscal": {"type": "string"},
                "invoice_status": {
                    "type": "string",
                    "enum": ["Pending", "Matched", "Invoiced", "Needs Review"],
                },
                "notes": {"type": "string", "description": "Optional processing notes"},
            },
            "required": ["nota_fiscal", "invoice_status"],
        },
    },
]


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "fiscal-get_nf_details":
        return _get_nf_details(args["nota_fiscal"])

    elif tool_name == "fiscal-find_matching_pos":
        return _find_matching_pos(args)

    elif tool_name == "fiscal-find_recurring_expense":
        return _find_recurring_expense(args)

    elif tool_name == "fiscal-link_nf_to_po":
        frappe.db.set_value("Nota Fiscal", args["nota_fiscal"], "purchase_order", args["purchase_order"])
        frappe.db.set_value("Purchase Order", args["purchase_order"], "nota_fiscal", args["nota_fiscal"])
        frappe.db.set_value("Nota Fiscal", args["nota_fiscal"], "invoice_status", "Matched")
        return {"status": "linked", "nota_fiscal": args["nota_fiscal"], "purchase_order": args["purchase_order"]}

    elif tool_name == "fiscal-create_purchase_invoice":
        return _create_purchase_invoice(args, executor)

    elif tool_name == "fiscal-update_nf_status":
        updates = {"invoice_status": args["invoice_status"]}
        frappe.db.set_value("Nota Fiscal", args["nota_fiscal"], updates)
        return {"status": "updated", "nota_fiscal": args["nota_fiscal"], "invoice_status": args["invoice_status"]}

    raise ValueError(f"Unknown tool: {tool_name}")


def _get_nf_details(nf_name: str) -> dict:
    """Get comprehensive NF details for the agent to analyze."""
    nf = frappe.get_doc("Nota Fiscal", nf_name)

    # Resolve supplier name from CNPJ
    supplier_name = None
    supplier_doc = None
    cnpj = nf.get("cnpj_emitente") or ""
    if cnpj:
        supplier_doc = frappe.db.get_value("Supplier", {"tax_id": ["like", f"%{cnpj}%"]}, "name")
        if supplier_doc:
            supplier_name = frappe.db.get_value("Supplier", supplier_doc, "supplier_name")

    # Get NF items
    items = []
    for item in (nf.get("items") or []):
        items.append({
            "description": item.get("descricao") or item.get("description") or "",
            "ncm": item.get("ncm") or "",
            "qty": float(item.get("quantidade") or item.get("qty") or 1),
            "rate": float(item.get("valor_unitario") or item.get("rate") or 0),
            "total": float(item.get("valor_total_item") or item.get("total") or 0),
        })

    return {
        "name": nf.name,
        "document_type": nf.get("document_type") or "",
        "cnpj_emitente": cnpj,
        "razao_social": nf.get("razao_social") or "",
        "supplier_found": supplier_doc,
        "supplier_name": supplier_name,
        "numero": nf.get("numero") or "",
        "data_emissao": str(nf.get("data_emissao") or ""),
        "valor_total": float(nf.get("valor_total") or 0),
        "processing_status": nf.get("processing_status") or "",
        "invoice_status": nf.get("invoice_status") or "",
        "items": items,
        "has_purchase_order": bool(nf.get("purchase_order")),
        "has_purchase_invoice": bool(nf.get("purchase_invoice")),
    }


def _find_matching_pos(args: dict) -> dict:
    """Find Purchase Orders matching a supplier."""
    cnpj = args.get("supplier_cnpj", "")
    valor = 0

    if args.get("nota_fiscal") and frappe.db.exists("Nota Fiscal", args["nota_fiscal"]):
        nf = frappe.get_doc("Nota Fiscal", args["nota_fiscal"])
        cnpj = cnpj or nf.get("cnpj_emitente", "")
        valor = float(nf.get("valor_total") or 0)

    suppliers = frappe.get_all(
        "Supplier", filters={"tax_id": ["like", f"%{cnpj}%"]}, pluck="name"
    ) if cnpj else []

    pos = []
    for supplier_name in suppliers:
        matches = frappe.get_all(
            "Purchase Order",
            filters={
                "supplier": supplier_name,
                "docstatus": 1,
                "status": ["not in", ["Completed", "Cancelled"]],
            },
            fields=["name", "supplier", "grand_total", "transaction_date", "status"],
            order_by="transaction_date desc",
            limit=5,
        )
        pos.extend(matches)

    return {
        "matching_pos": pos,
        "nf_amount": valor,
        "supplier_cnpj": cnpj,
        "supplier_found": suppliers[0] if suppliers else None,
        "po_count": len(pos),
    }


def _find_recurring_expense(args: dict) -> dict:
    """Find recurring expense matching a supplier."""
    cnpj = args.get("supplier_cnpj", "")
    supplier_name_query = args.get("supplier_name", "")

    # Resolve supplier from CNPJ
    supplier = None
    if cnpj:
        supplier = frappe.db.get_value("Supplier", {"tax_id": ["like", f"%{cnpj}%"]}, "name")
    if not supplier and supplier_name_query:
        supplier = frappe.db.get_value("Supplier", {"supplier_name": ["like", f"%{supplier_name_query}%"]}, "name")

    if not supplier:
        return {"found": False, "message": "Supplier not found"}

    expenses = frappe.get_all(
        "I8 Recurring Expense",
        filters={"supplier": supplier, "active": 1},
        fields=["name", "title", "estimated_amount", "document_type", "frequency", "next_due"],
    )

    return {
        "found": len(expenses) > 0,
        "supplier": supplier,
        "recurring_expenses": expenses,
    }


def _create_purchase_invoice(args: dict, executor) -> dict:
    """Create Purchase Invoice from NF, with or without PO."""
    nf = frappe.get_doc("Nota Fiscal", args["nota_fiscal"])
    cnpj = nf.get("cnpj_emitente") or ""

    # Resolve supplier
    supplier = nf.get("supplier")
    if not supplier and cnpj:
        supplier = frappe.db.get_value("Supplier", {"tax_id": ["like", f"%{cnpj}%"]}, "name")
    if not supplier:
        supplier = nf.get("razao_social") or cnpj

    # Build items from PO if available, otherwise from NF
    po_name = args.get("purchase_order")
    items = []

    if po_name and frappe.db.exists("Purchase Order", po_name):
        # Use PO items
        po = frappe.get_doc("Purchase Order", po_name)
        for po_item in po.items:
            items.append({
                "item_code": po_item.item_code,
                "qty": po_item.qty,
                "rate": po_item.rate,
                "purchase_order": po_name,
                "po_detail": po_item.name,
                "expense_account": po_item.get("expense_account") or "",
                "cost_center": po_item.get("cost_center") or "",
            })
    elif nf.get("items") and len(nf.items) > 0:
        # Use NF items
        for nf_item in nf.items:
            desc = nf_item.get("descricao") or nf_item.get("description") or "Servico"
            item_code = _resolve_item_code(desc)
            items.append({
                "item_code": item_code,
                "qty": float(nf_item.get("quantidade") or nf_item.get("qty") or 1),
                "rate": float(nf_item.get("valor_unitario") or nf_item.get("rate") or 0),
            })
    else:
        # Fallback: single line item
        item_code = _resolve_item_code(nf.get("razao_social") or "Services")
        items.append({
            "item_code": item_code,
            "qty": 1,
            "rate": float(nf.get("valor_total") or 0),
        })

    pi_data = {
        "supplier": supplier,
        "bill_no": nf.get("numero") or nf.name,
        "bill_date": nf.get("data_emissao"),
        "nota_fiscal": nf.name,
        "items": items,
    }

    result = executor.execute("Purchase Invoice", "create", pi_data)

    # Update NF status
    frappe.db.set_value("Nota Fiscal", nf.name, {
        "invoice_status": "Invoiced",
        "purchase_invoice": result.get("name"),
    })

    return result
