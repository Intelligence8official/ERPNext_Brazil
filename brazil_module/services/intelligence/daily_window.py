"""Is it this job's hour yet?

``scheduler_events`` in hooks.py is a static dict, so a job whose hour the operator can change
cannot be scheduled at that hour. It is woken often instead - every fifteen minutes - and decides
for itself, from the Time field on I8 Agent Settings.

Two things here are less obvious than they look:

- A Time field does NOT arrive as a string. Frappe casts Time to ``datetime.timedelta``, and
  splitting the object itself is what silently killed the daily briefing for months: the check
  raised on every tick, outside the try that guarded the rest.
- ``is_due`` never writes the marker it reads. The job writes it with ``mark_done`` once the work
  is actually done, so a failure is retried on the next tick instead of latching the job off for
  the day.
"""

from datetime import datetime

import frappe

SETTINGS = "I8 Agent Settings"
WINDOW_MINUTES = 15
"""How long after the configured time the job may still start - one scheduler tick."""


def is_due(time_field: str, marker: str, *, default: str = "08:00:00", now: datetime | None = None) -> bool:
    """Whether the job configured in ``time_field`` should run now. Read-only."""
    moment = now or datetime.now()
    hour, minute = target_time(time_field, default)
    target = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    elapsed = (moment - target).total_seconds() / 60
    if not (0 <= elapsed < WINDOW_MINUTES):
        return False
    return frappe.cache.get_value(marker) != moment.strftime("%Y-%m-%d")


def mark_done(marker: str, *, now: datetime | None = None) -> None:
    """Record that the job ran today. Called by the job, after the work, never by ``is_due``."""
    moment = now or datetime.now()
    frappe.cache.set_value(marker, moment.strftime("%Y-%m-%d"), expires_in_sec=86400)


def target_time(time_field: str, default: str = "08:00:00") -> tuple[int, int]:
    """The configured hour and minute, whatever type the field comes back as."""
    value = frappe.db.get_single_value(SETTINGS, time_field)
    try:
        # str() is the point: a timedelta prints as "4:00:00" and a time as "09:30:00", both of
        # which split cleanly. Minutes are optional - the shipped defaults are "07:00" and "09:00".
        parts = str(value or default).split(":")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        # Whatever is in there, a job woken every fifteen minutes must not die every fifteen.
        _log(f"{time_field} ilegivel: {value!r}")
        return _fallback(default)


def _fallback(default: str) -> tuple[int, int]:
    parts = str(default).split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def _log(message: str) -> None:
    try:
        frappe.log_error(title="I8 Daily Window", message=message)
    except Exception:
        pass
