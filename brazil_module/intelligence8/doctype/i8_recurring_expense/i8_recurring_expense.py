import calendar
from datetime import date

import frappe
from frappe.model.document import Document


class I8RecurringExpense(Document):
    def validate(self):
        self._validate_dates()
        if self.active and not self.next_due:
            self.next_due = self._calculate_first_due()

    def _validate_dates(self):
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                frappe.throw("End Date must be after Start Date")
        if self.day_of_month and (self.day_of_month < 1 or self.day_of_month > 31):
            frappe.throw("Day of Month must be between 1 and 31")

    def _calculate_first_due(self) -> date:
        """Calculate the first next_due date based on start_date and day_of_month.

        If start_date is in the current month and day_of_month hasn't passed yet,
        the first due is this month. If day_of_month has passed, first due is next period.
        If start_date is in a past month, find the next upcoming due date.
        """
        start = self.start_date
        if isinstance(start, str):
            start = date.fromisoformat(start)
        if not start:
            start = date.today()

        day = self.day_of_month or 1
        freq = self.frequency or "Monthly"

        # Try the due date in the same month as start_date
        try:
            max_day = calendar.monthrange(start.year, start.month)[1]
            same_month_due = date(start.year, start.month, min(day, max_day))
        except ValueError:
            same_month_due = None

        # If the due date in start month is on or after start_date, use it
        if same_month_due and same_month_due >= start:
            return same_month_due

        # Otherwise, calculate next period
        return self._calculate_next_due(same_month_due or start)

    def _calculate_next_due(self, after_date) -> date:
        """Calculate the next due date after the given date."""
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
