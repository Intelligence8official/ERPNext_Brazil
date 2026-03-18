"""
Bank statement sync service.

Fetches extrato from Banco Inter and creates ERPNext Bank Transaction records.
"""

from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import now_datetime, getdate, flt


def scheduled_statement_sync():
    """Scheduler entry point: sync statements for all enabled companies.

    Iterates over all Inter Company Accounts with sync_enabled=1.
    Each company is processed independently so one failure does not block others.
    """
    if not frappe.db.get_single_value("Banco Inter Settings", "enabled"):
        return

    if not frappe.db.get_single_value("Banco Inter Settings", "auto_sync_statements"):
        return

    accounts = frappe.get_all(
        "Inter Company Account",
        filters={"sync_enabled": 1, "certificate_valid": 1},
        pluck="name",
    )

    for account_name in accounts:
        try:
            sync_statements_for_company(account_name)
        except Exception as e:
            frappe.log_error(
                str(e), f"Inter Statement Sync Error: {account_name}"
            )


def sync_statements_for_company(
    company_account_name: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """Fetch extrato and create Bank Transactions for a company.

    Args:
        company_account_name: Name of the Inter Company Account.
        start_date: Start of date range (defaults to N days back from settings).
        end_date: End of date range (defaults to today).

    Returns:
        Dict with sync results.
    """
    from brazil_module.services.banking.inter_client import InterAPIClient

    account_doc = frappe.get_doc("Inter Company Account", company_account_name)
    settings = frappe.get_single("Banco Inter Settings")

    if not end_date:
        end_date = date.today()
    if not start_date:
        days_back = settings.sync_days_back or 3
        start_date = end_date - timedelta(days=days_back)

    # Create sync log
    sync_log = frappe.new_doc("Inter Sync Log")
    sync_log.company = account_doc.company
    sync_log.sync_type = "Statement"
    sync_log.status = "Running"
    sync_log.started_at = now_datetime()
    sync_log.date_range_start = start_date
    sync_log.date_range_end = end_date
    sync_log.insert(ignore_permissions=True)
    frappe.db.commit()

    results = {
        "fetched": 0,
        "created": 0,
        "skipped": 0,
        "failed": 0,
    }

    try:
        client = InterAPIClient(company_account_name)
        transactions = client.get_statement(start_date, end_date)
        results["fetched"] = len(transactions)

        for txn in transactions:
            try:
                if _is_duplicate_transaction(txn, account_doc.bank_account):
                    results["skipped"] += 1
                    continue

                _create_bank_transaction(txn, account_doc)
                results["created"] += 1

            except Exception as e:
                results["failed"] += 1
                frappe.log_error(
                    str(e), f"Inter Bank Transaction Creation Error"
                )

        # Update sync state
        frappe.db.set_value(
            "Inter Company Account",
            company_account_name,
            "last_statement_sync",
            now_datetime(),
            update_modified=False,
        )

        # Update sync log
        sync_log.reload()
        sync_log.status = "Success" if results["failed"] == 0 else "Partial"
        sync_log.completed_at = now_datetime()
        sync_log.records_fetched = results["fetched"]
        sync_log.records_created = results["created"]
        sync_log.records_skipped = results["skipped"]
        sync_log.records_failed = results["failed"]
        sync_log.save(ignore_permissions=True)
        frappe.db.commit()

        # Auto-reconcile if enabled
        if settings.auto_reconcile:
            try:
                from brazil_module.services.banking.reconciliation import batch_reconcile
                batch_reconcile(account_doc.bank_account, start_date)
            except Exception as e:
                frappe.log_error(str(e), "Inter Auto-Reconciliation Error")

    except Exception as e:
        sync_log.reload()
        sync_log.status = "Failed"
        sync_log.completed_at = now_datetime()
        sync_log.error_message = str(e)[:500]
        sync_log.save(ignore_permissions=True)
        frappe.db.commit()
        raise

    return results


def _create_bank_transaction(txn_data: dict, account_doc) -> str:
    """Create a single ERPNext Bank Transaction from an Inter statement entry.

    Inter API statement format:
    {
        "dataMovimento": "2025-01-15",
        "tipoTransacao": "CREDITO" | "DEBITO",
        "tipoOperacao": "PIX" | "TED" | "BOLETO" | ...,
        "valor": "150.00",
        "titulo": "Payment description",
        "descricao": "Detailed description",
        "detalhes": { ... additional info }
    }
    """
    is_credit = txn_data.get("tipoTransacao", "").upper() == "CREDITO"
    amount = flt(txn_data.get("valor", 0))
    description_parts = [
        txn_data.get("titulo", ""),
        txn_data.get("descricao", ""),
        txn_data.get("tipoOperacao", ""),
    ]
    description = " | ".join(filter(None, description_parts))

    # Build reference number from available data
    reference = _build_reference(txn_data)

    bt = frappe.new_doc("Bank Transaction")
    bt.date = txn_data.get("dataMovimento", date.today().isoformat())
    bt.bank_account = account_doc.bank_account
    bt.company = account_doc.company
    bt.description = description[:500] if description else "Banco Inter transaction"
    bt.currency = "BRL"

    if is_credit:
        bt.deposit = amount
        bt.withdrawal = 0
    else:
        bt.deposit = 0
        bt.withdrawal = amount

    bt.reference_number = reference
    bt.transaction_id = reference

    bt.insert(ignore_permissions=True)
    bt.submit()
    frappe.db.commit()

    return bt.name


def _build_reference(txn_data: dict) -> str:
    """Build a unique reference string from transaction data."""
    parts = []

    # Try to get specific identifiers
    detalhes = txn_data.get("detalhes", {})
    if isinstance(detalhes, dict):
        for key in ("endToEndId", "txid", "nossoNumero", "codigoTransacao"):
            if detalhes.get(key):
                parts.append(str(detalhes[key]))
                break

    if not parts:
        # Fallback: combine date + type + amount for uniqueness
        parts = [
            txn_data.get("dataMovimento", ""),
            txn_data.get("tipoOperacao", ""),
            str(txn_data.get("valor", "")),
            txn_data.get("titulo", "")[:50],
        ]

    return "-".join(filter(None, parts))[:140]


def _is_duplicate_transaction(txn_data: dict, bank_account: str) -> bool:
    """Check if this transaction already exists as a Bank Transaction."""
    reference = _build_reference(txn_data)
    txn_date = txn_data.get("dataMovimento")

    if not reference or not txn_date:
        return False

    return frappe.db.exists(
        "Bank Transaction",
        {
            "bank_account": bank_account,
            "reference_number": reference,
            "date": txn_date,
        },
    )


def update_balance(company_account_name: str) -> float:
    """Fetch current balance and update Inter Company Account.

    Returns:
        Current balance as float.
    """
    from brazil_module.services.banking.inter_client import InterAPIClient

    client = InterAPIClient(company_account_name)
    balance_data = client.get_balance()

    balance = flt(balance_data.get("disponivel", 0))

    frappe.db.set_value(
        "Inter Company Account",
        company_account_name,
        {
            "current_balance": balance,
            "balance_date": date.today(),
            "last_balance_check": now_datetime(),
        },
        update_modified=False,
    )
    frappe.db.commit()

    return balance


def daily_balance_update():
    """Scheduler entry point: update balance for all enabled companies."""
    if not frappe.db.get_single_value("Banco Inter Settings", "enabled"):
        return

    accounts = frappe.get_all(
        "Inter Company Account",
        filters={"sync_enabled": 1, "certificate_valid": 1},
        pluck="name",
    )

    for account_name in accounts:
        try:
            update_balance(account_name)
        except Exception as e:
            frappe.log_error(str(e), f"Inter Balance Update Error: {account_name}")
