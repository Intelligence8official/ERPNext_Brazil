from datetime import date, timedelta

import frappe


def check_overdue():
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return

    profiles = frappe.get_all(
        "Supplier",
        filters={"i8_expected_nf_days": [">", 0]},
        fields=[
            "name", "supplier_name",
            "i8_expected_nf_days as expected_nf_days",
            "i8_follow_up_after_days as follow_up_after_days",
            "i8_max_follow_ups as max_follow_ups",
        ],
    )
    for p in profiles:
        p["supplier"] = p["name"]

    for profile in profiles:
        overdue_pos = _find_overdue_pos(profile)
        for po in overdue_pos:
            frappe.enqueue(
                "brazil_module.services.intelligence.agent.process_single_event",
                queue="long",
                job_id=f"i8:followup:{po['name']}",
                event_type="follow_up_supplier",
                event_id=f"followup:{po['name']}",
                event_data={
                    "module": "p2p",
                    "purchase_order": po["name"],
                    "supplier": profile["supplier"],
                    "supplier_profile": profile["name"],
                    "days_overdue": po["days_overdue"],
                },
                deduplicate=True,
            )


def _find_overdue_pos(profile: dict) -> list:
    """Find Purchase Orders that are overdue for NF delivery.

    Excludes:
    - Completed/Cancelled POs
    - POs that already have a Nota Fiscal linked
    - POs that already have a Purchase Invoice linked
    """
    expected_days = profile.get("expected_nf_days") or 5
    cutoff = date.today() - timedelta(days=expected_days)

    pos = frappe.get_all(
        "Purchase Order",
        filters={
            "supplier": profile["supplier"],
            "docstatus": 1,
            "status": ["not in", ["Completed", "Cancelled", "Closed"]],
            "transaction_date": ["<=", cutoff.isoformat()],
        },
        fields=["name", "transaction_date", "grand_total", "status"],
    )

    overdue = []
    for po in pos:
        # Skip if NF already received
        nf_exists = frappe.db.exists("Nota Fiscal", {"purchase_order": po["name"]})
        if nf_exists:
            continue

        # Skip if Purchase Invoice already exists for this PO
        pi_exists = frappe.db.exists(
            "Purchase Invoice Item",
            {"purchase_order": po["name"], "docstatus": ["<", 2]},
        )
        if pi_exists:
            continue

        txn_date = po["transaction_date"]
        if isinstance(txn_date, str):
            txn_date = date.fromisoformat(txn_date)
        days_overdue = (date.today() - txn_date).days - expected_days
        overdue.append({**po, "days_overdue": max(0, days_overdue)})

    return overdue
