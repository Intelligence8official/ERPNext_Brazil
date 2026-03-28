import frappe

TOOL_SCHEMAS = [
    {
        "name": "erp-read_document",
        "description": "Read a document from ERPNext by doctype and name",
        "input_schema": {
            "type": "object",
            "properties": {
                "doctype": {"type": "string", "description": "The DocType name"},
                "name": {"type": "string", "description": "The document name/ID"},
            },
            "required": ["doctype", "name"],
        },
    },
    {
        "name": "erp-list_documents",
        "description": "List documents from ERPNext with optional filters",
        "input_schema": {
            "type": "object",
            "properties": {
                "doctype": {"type": "string"},
                "filters": {"type": "object", "description": "Filter conditions"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["doctype"],
        },
    },
    {
        "name": "erp-get_report_data",
        "description": "Get aggregated report data from ERPNext. Use for questions like 'how much did we spend on X' or 'what are the totals for Y'",
        "input_schema": {
            "type": "object",
            "properties": {
                "doctype": {"type": "string", "description": "DocType to query (e.g., Purchase Invoice, Sales Invoice, Payment Entry)"},
                "filters": {"type": "object", "description": "Filter conditions"},
                "group_by": {"type": "string", "description": "Field to group by (e.g., supplier, cost_center)"},
                "aggregate": {"type": "string", "description": "Aggregate function: SUM, COUNT, AVG"},
                "aggregate_field": {"type": "string", "description": "Field to aggregate (e.g., grand_total, outstanding_amount)"},
                "order_by": {"type": "string", "description": "Simple field name to order by"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["doctype"],
        },
    },
    {
        "name": "erp-get_account_balance",
        "description": "Get the balance of a specific account or group of accounts from GL Entry",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Account name or partial match"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "erp-cash_flow_projection",
        "description": "Project cash flow for the next N days including receivables, payables, and recurring expenses. Use for questions like 'what is our projected cash position'",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30, "description": "Number of days to project"},
            },
        },
    },
    {
        "name": "erp-cash_flow_scenario",
        "description": "Simulate a what-if scenario on cash flow. Use for questions like 'what if I delay payment X' or 'what if I pay X early'",
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario": {"type": "string", "description": "Description of the scenario"},
                "adjust_invoice": {"type": "string", "description": "Purchase Invoice name to adjust"},
                "new_due_date": {"type": "string", "description": "New due date YYYY-MM-DD"},
                "days": {"type": "integer", "default": 30},
            },
            "required": ["scenario"],
        },
    },
]


_FINANCIAL_DOCTYPES = frozenset({"Purchase Invoice", "Sales Invoice", "Payment Entry", "Journal Entry"})


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "erp-read_document":
        return executor.execute(args["doctype"], "read", {"name": args["name"]})
    elif tool_name == "erp-list_documents":
        return {
            "data": frappe.get_all(
                args["doctype"],
                filters=args.get("filters"),
                fields=args.get("fields", ["name"]),
                limit_page_length=args.get("limit", 20),
            )
        }
    elif tool_name == "erp-get_report_data":
        doctype = args["doctype"]
        filters = dict(args.get("filters") or {})

        # Only allow docstatus=1 (submitted) for financial queries
        if doctype in _FINANCIAL_DOCTYPES:
            filters.setdefault("docstatus", 1)

        group_by = args.get("group_by")
        aggregate = (args.get("aggregate") or "SUM").upper()
        agg_field = args.get("aggregate_field") or "grand_total"

        if aggregate not in ("SUM", "COUNT", "AVG"):
            aggregate = "SUM"

        if group_by:
            # Validate field names are simple identifiers (prevent SQL injection)
            if not group_by.isidentifier() or not agg_field.isidentifier():
                return {"error": "Invalid field names"}

            data = frappe.db.sql(
                f"""
                SELECT `{group_by}`, {aggregate}(`{agg_field}`) as value, COUNT(*) as count
                FROM `tab{doctype}`
                WHERE docstatus = %(docstatus)s
                GROUP BY `{group_by}`
                ORDER BY value DESC
                LIMIT %(limit)s
                """,
                {"docstatus": filters.get("docstatus", 1), "limit": args.get("limit", 20)},
                as_dict=True,
            )
            return {"data": data, "aggregate": aggregate, "field": agg_field, "grouped_by": group_by}
        else:
            data = frappe.get_all(
                doctype,
                filters=filters,
                fields=["name", agg_field] if agg_field != "name" else ["name"],
                limit_page_length=args.get("limit", 20),
                order_by=args.get("order_by", "creation desc"),
            )
            return {"data": data}

    elif tool_name == "erp-get_account_balance":
        account = args.get("account", "")
        from_date = args.get("from_date")
        to_date = args.get("to_date")

        date_filter = ""
        query_params: dict = {"account": f"%{account}%"}

        if from_date and to_date:
            date_filter = "AND posting_date BETWEEN %(from_date)s AND %(to_date)s"
            query_params["from_date"] = from_date
            query_params["to_date"] = to_date
        elif from_date:
            date_filter = "AND posting_date >= %(from_date)s"
            query_params["from_date"] = from_date
        elif to_date:
            date_filter = "AND posting_date <= %(to_date)s"
            query_params["to_date"] = to_date

        data = frappe.db.sql(
            f"""
            SELECT account,
                   SUM(debit) as total_debit,
                   SUM(credit) as total_credit,
                   SUM(debit) - SUM(credit) as balance
            FROM `tabGL Entry`
            WHERE account LIKE %(account)s AND is_cancelled = 0
            {date_filter}
            GROUP BY account
            ORDER BY balance DESC
            """,
            query_params,
            as_dict=True,
        )
        return {"data": data}

    elif tool_name == "erp-cash_flow_projection":
        return _cash_flow_projection(args.get("days", 30))

    elif tool_name == "erp-cash_flow_scenario":
        return _cash_flow_scenario(args)

    else:
        raise ValueError(f"Unknown tool: {tool_name}")


def _cash_flow_projection(days: int) -> dict:
    """Build a cash flow projection for the next N days."""
    from datetime import date as _date, timedelta as _td

    today = _date.today()
    end_date = today + _td(days=days)

    # Current balance from GL
    gl_balance = frappe.db.sql("""
        SELECT COALESCE(SUM(debit) - SUM(credit), 0) as balance
        FROM `tabGL Entry` gl
        JOIN `tabBank Account` ba ON ba.account = gl.account
        WHERE ba.is_company_account = 1 AND gl.is_cancelled = 0
    """, as_dict=True)
    current_balance = float(gl_balance[0]["balance"]) if gl_balance else 0

    # Receivables by week
    receivables = frappe.db.sql("""
        SELECT due_date, SUM(outstanding_amount) as total
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND outstanding_amount > 0
        AND due_date BETWEEN %s AND %s
        GROUP BY due_date ORDER BY due_date
    """, (today.isoformat(), end_date.isoformat()), as_dict=True)
    total_receivable = sum(float(r["total"]) for r in receivables)

    # Payables by week
    payables = frappe.db.sql("""
        SELECT due_date, supplier_name, SUM(outstanding_amount) as total
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1 AND outstanding_amount > 0
        AND due_date BETWEEN %s AND %s
        GROUP BY due_date, supplier_name ORDER BY due_date
    """, (today.isoformat(), end_date.isoformat()), as_dict=True)
    total_payable = sum(float(p["total"]) for p in payables)

    # Recurring expenses
    recurring = frappe.get_all(
        "I8 Recurring Expense",
        filters={"active": 1, "next_due": ["between", [today.isoformat(), end_date.isoformat()]]},
        fields=["title", "estimated_amount", "next_due", "supplier"],
    )
    total_recurring = sum(float(r.get("estimated_amount") or 0) for r in recurring)

    projected = current_balance + total_receivable - total_payable - total_recurring

    return {
        "current_balance": current_balance,
        "total_receivable": total_receivable,
        "total_payable": total_payable,
        "total_recurring": total_recurring,
        "projected_balance": projected,
        "days": days,
        "payables_detail": [
            {"date": str(p["due_date"]), "supplier": p.get("supplier_name", "")[:30], "amount": float(p["total"])}
            for p in payables
        ],
        "recurring_detail": [
            {"title": r["title"], "amount": float(r.get("estimated_amount") or 0), "due": str(r["next_due"])}
            for r in recurring
        ],
    }


def _cash_flow_scenario(args: dict) -> dict:
    """Simulate a what-if scenario on cash flow."""
    from datetime import date as _date, timedelta as _td

    days = args.get("days", 30)
    base = _cash_flow_projection(days)

    adjust_invoice = args.get("adjust_invoice")
    new_due_date = args.get("new_due_date")

    if adjust_invoice and new_due_date:
        # Find the invoice
        inv = frappe.db.get_value(
            "Purchase Invoice", adjust_invoice,
            ["outstanding_amount", "due_date", "supplier_name"],
            as_dict=True,
        )
        if inv:
            amount = float(inv.get("outstanding_amount") or 0)
            today = _date.today()
            end_date = today + _td(days=days)
            new_date = _date.fromisoformat(new_due_date)

            # If moving payment out of the projection window
            old_in_window = inv["due_date"] and str(inv["due_date"]) <= end_date.isoformat()
            new_in_window = new_date <= end_date

            adjustment = 0
            if old_in_window and not new_in_window:
                adjustment = amount  # Payment moved out = more cash
            elif not old_in_window and new_in_window:
                adjustment = -amount  # Payment moved in = less cash

            base["scenario"] = args.get("scenario", "")
            base["adjusted_invoice"] = adjust_invoice
            base["adjustment"] = adjustment
            base["projected_balance_scenario"] = base["projected_balance"] + adjustment
            base["original_due_date"] = str(inv.get("due_date", ""))
            base["new_due_date"] = new_due_date
            return base

    base["scenario"] = args.get("scenario", "No specific invoice adjusted")
    base["projected_balance_scenario"] = base["projected_balance"]
    return base
