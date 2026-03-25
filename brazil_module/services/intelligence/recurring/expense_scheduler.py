import calendar
from datetime import date, timedelta

import frappe


def daily_check():
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return

    today = date.today()
    expenses = frappe.get_all(
        "I8 Recurring Expense",
        filters={"active": 1},
        fields=[
            "name", "title", "supplier", "document_type", "estimated_amount",
            "currency", "frequency", "day_of_month", "lead_days",
            "notify_supplier", "last_created", "next_due",
        ],
    )

    for expense in expenses:
        if _is_due(expense, today):
            frappe.enqueue(
                "brazil_module.services.intelligence.agent.process_single_event",
                queue="long",
                timeout=120,
                event_type="recurring_schedule",
                event_id=f"recurring:{expense['name']}:{today.isoformat()}",
                event_data={
                    "module": "p2p",
                    "recurring_expense": expense["name"],
                    "supplier": expense["supplier"],
                    "document_type": expense["document_type"],
                    "amount": float(expense["estimated_amount"] or 0),
                    "currency": expense["currency"],
                },
                deduplicate=True,
            )


def _is_due(expense: dict, today: date) -> bool:
    next_due = expense.get("next_due")
    if not next_due:
        return False
    if isinstance(next_due, str):
        next_due = date.fromisoformat(next_due)
    lead_days = expense.get("lead_days") or 0
    trigger_date = next_due - timedelta(days=lead_days)
    last_created = expense.get("last_created")
    if last_created and isinstance(last_created, str):
        last_created = date.fromisoformat(last_created)
    return today >= trigger_date and (not last_created or last_created < next_due)


def calculate_next_due(frequency: str, day_of_month: int, after_date: date) -> date:
    day = min(day_of_month, 28) if day_of_month else 1
    if frequency == "Monthly":
        month = after_date.month % 12 + 1
        year = after_date.year + (1 if month == 1 else 0)
        max_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(day_of_month, max_day))
    elif frequency == "Weekly":
        return after_date + timedelta(weeks=1)
    elif frequency == "Quarterly":
        month = (after_date.month - 1 + 3) % 12 + 1
        year = after_date.year + ((after_date.month - 1 + 3) // 12)
        max_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(day_of_month, max_day))
    elif frequency == "Yearly":
        year = after_date.year + 1
        max_day = calendar.monthrange(year, after_date.month)[1]
        return date(year, after_date.month, min(day_of_month, max_day))
    month = after_date.month % 12 + 1
    year = after_date.year + (1 if month == 1 else 0)
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day_of_month, max_day))
