import calendar
from datetime import date
import frappe
from frappe.model.document import Document


class I8RecurringExpense(Document):
    def validate(self):
        if not self.next_due and self.active:
            self.next_due = self._calculate_next_due(date.today())

    def _calculate_next_due(self, after_date):
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
