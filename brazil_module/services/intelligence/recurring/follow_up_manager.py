from datetime import date, timedelta

import frappe


def check_overdue():
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return

    profiles = frappe.get_all(
        "I8 Supplier Profile",
        filters={},
        fields=[
            "name", "supplier", "expected_nf_days",
            "follow_up_after_days", "max_follow_ups", "follow_up_interval_days",
        ],
    )

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
    expected_days = profile.get("expected_nf_days") or 5
    cutoff = date.today() - timedelta(days=expected_days)

    pos = frappe.get_all(
        "Purchase Order",
        filters={
            "supplier": profile["supplier"],
            "docstatus": 1,
            "transaction_date": ["<=", cutoff.isoformat()],
        },
        fields=["name", "transaction_date", "grand_total"],
    )

    overdue = []
    for po in pos:
        # Check if NF already received for this PO
        nf_exists = frappe.db.exists(
            "Nota Fiscal",
            {"purchase_order": po["name"]},
        )
        if not nf_exists:
            txn_date = po["transaction_date"]
            if isinstance(txn_date, str):
                txn_date = date.fromisoformat(txn_date)
            days_overdue = (date.today() - txn_date).days - expected_days
            overdue.append({**po, "days_overdue": max(0, days_overdue)})

    return overdue
