"""The few things every module of the outbound payment path needs.

Small on purpose. What lives here was written three times before: the same doctype name, the same
column width, and three private copies of the ``JobTimeoutException`` fallback - which is worse
than it looks, because without ``rq`` each copy is a *different* class, so one module's ``except``
would not catch another module's raise.

Nothing here reads a payment order or talks to the bank.
"""

import frappe

ACCOUNT_DOCTYPE = "Inter Company Account"
ERROR_LOG_TITLE_LENGTH = 140  # Error Log.method is a 140-character column

try:
    from rq.timeouts import JobTimeoutException
except ImportError:  # rq always comes with Frappe; the unit tests run without it

    class JobTimeoutException(Exception):
        """Keeps ``except JobTimeoutException`` valid where rq is not installed."""


def bank_gl_account(inter_account: str | None) -> str | None:
    """The ledger account the money leaves: Inter Company Account -> Bank Account -> account."""
    bank_account = frappe.db.get_value(ACCOUNT_DOCTYPE, inter_account, "bank_account") if inter_account else None
    return frappe.db.get_value("Bank Account", bank_account, "account") or None if bank_account else None
