import calendar
from datetime import date

import frappe
from frappe.model.document import Document


class I8RecurringExpense(Document):
    def validate(self):
        self._validate_dates()
        if self.active and not self.next_due:
            self.next_due = self._calculate_next_due(
                self.start_date or date.today()
            )

    def _validate_dates(self):
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                frappe.throw("End Date must be after Start Date")
        if self.day_of_month and (self.day_of_month < 1 or self.day_of_month > 31):
            frappe.throw("Day of Month must be between 1 and 31")

    def _calculate_next_due(self, after_date):
        if isinstance(after_date, str):
            after_date = date.fromisoformat(after_date)
        day = self.day_of_month or 1
        freq = self.frequency or "Monthly"

        if freq == "Monthly":
            month = after_date.month % 12 + 1
            year = after_date.year + (1 if month == 1 else 0)
            max_day = calendar.monthrange(year, month)[1]
            return date(year, month, min(day, max_day))
        elif freq == "Weekly":
            from datetime import timedelta
            return after_date + timedelta(weeks=1)
        elif freq == "Quarterly":
            month = (after_date.month - 1 + 3) % 12 + 1
            year = after_date.year + ((after_date.month - 1 + 3) // 12)
            max_day = calendar.monthrange(year, month)[1]
            return date(year, month, min(day, max_day))
        elif freq == "Yearly":
            year = after_date.year + 1
            max_day = calendar.monthrange(year, after_date.month)[1]
            return date(year, after_date.month, min(day, max_day))
        return after_date

    def update_after_creation(self):
        """Called after the agent creates a document for this expense."""
        self.last_created = date.today()
        self.next_due = self._calculate_next_due(date.today())
        if self.end_date and self.next_due > date.fromisoformat(str(self.end_date)):
            self.active = 0
        self.save(ignore_permissions=True)
