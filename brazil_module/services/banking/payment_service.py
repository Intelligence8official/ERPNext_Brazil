"""Outbound payments through Banco Inter: the single path to the bank.

Spec: docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md (sections 3 and 4.4).

- I1  Only ``execute_payment_order`` calls the bank's payment endpoints.
- I2  The claim (``Processing`` + idempotency key + request time) is committed before the request.
- I3  An order is sent at most once: a claim is only possible from ``Approved`` and nothing moves
      an order back there. No scheduler, retry or handler sends on its own.
- I4  An outcome that is not known is ``Needs Verification``. ``Failed`` means the bank does not
      hold the payment.
- I5  State is written with ``frappe.db.set_value`` + ``frappe.db.commit`` and always bumps
      ``modified``. A submitted order is never saved through its Document.
- I7  The Payment Entry exists only once the bank (or the operator) says the money left.
- I9  Every transition re-reads the row under a lock and writes only from the expected status.
"""

import json
import uuid
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, now_datetime

from brazil_module.services.banking.auth_manager import InterAuthError
from brazil_module.services.banking.inter_client import (
    InterAmbiguousResultError,
    InterAPIClient,
    InterAPIError,
)
from brazil_module.services.banking.payment_alerts import alert_operator
from brazil_module.services.banking.payment_guards import (
    DOCTYPE,
    check_invoice_payable,
    find_blocking_payment_order,
    get_inter_account_for_company,
    is_blocking,
    is_integration_enabled,
)

try:
    from rq.timeouts import JobTimeoutException
except ImportError:  # rq always comes with Frappe; the unit tests run without it

    class JobTimeoutException(Exception):
        """Keeps ``except JobTimeoutException`` valid where rq is not installed."""


ACCOUNT_DOCTYPE = "Inter Company Account"
PIX = "PIX"
BOLETO = "Boleto Payment"

JOB_TIMEOUT_SECONDS = 600
REQUEST_MAX_AGE = timedelta(minutes=15)
STALE_PROCESSING_AFTER = timedelta(minutes=30)
AWAITING_BANK_MAX_AGE = timedelta(days=85)  # GET /banking/v2/pix/{id} only covers 90 days
MAX_POLLS_PER_RUN = 15
# The hourly job runs on the "default" queue, where RQ kills it at 300 s. One hung poll can burn
# 270 s on its own, so stop asking the bank before the death penalty rather than lose the run.
POLL_BUDGET = timedelta(seconds=240)
PIX_DESCRIPTION_LIMIT = 140
BARCODE_LENGTHS = (44, 47, 48)
ERROR_LOG_TITLE_LENGTH = 140

# Bank status mapping (spec 3). Whatever is in none of these sets is still in flight.
PIX_PAID = frozenset({"PAGO", "PIX_PAGO"})
PIX_REJECTED = frozenset({"REPROVADO", "EXPIRADO", "CANCELADO", "CANCELADO_SEM_SALDO", "AGENDAMENTO_CANCELADO"})
PIX_NEEDS_HUMAN = frozenset({"FALHA", "NAO_DEBITADO"})
BOLETO_PAID = frozenset({"REALIZADO", "PAGO", "AGENDADO_REALIZADO"})
BOLETO_REJECTED = frozenset({
    "CANCELADO", "AGENDADO_CANCELADO", "ERRO", "ERRO_PAGAMENTO", "APROVACAO_EXPIRADA",
    "REPROVADO", "NAO_COMPENSADO", "AGENDADO_NAO_REALIZADO",
})
READ_SCOPES = {PIX: "pagamento-pix.read", BOLETO: "pagamento-boleto.read"}

FROM_PROCESSING = ("Processing",)
FROM_AWAITING_BANK = ("Awaiting Bank",)
FROM_NEEDS_VERIFICATION = ("Needs Verification",)

ORDER_FIELDS = [
    "name", "status", "docstatus", "idempotency_key", "bank_request_at", "bank_status", "invoice_lock",
    "payment_type", "company", "inter_company_account", "purchase_invoice", "amount", "pix_key", "barcode",
    "boleto_due_date", "scheduled_date", "approval_code", "transaction_id", "execution_date", "payment_entry",
]
CLEARED_ON_CLAIM = ("transaction_id", "approval_code", "bank_status", "execution_date", "inter_response")
INVOICE_FIELDS = ["docstatus", "outstanding_amount", "supplier", "company", "credit_to", "due_date"]


class _NothingSent(Exception):
    """A pre-send check failed: the request was never built, so ``Failed`` is the truth."""


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def enqueue_payment_execution(payment_order_name: str) -> bool:
    """Queue the execution as its own job. ``False`` when it is already queued or running."""
    job = frappe.enqueue(
        execute_payment_order,
        queue="short",
        timeout=JOB_TIMEOUT_SECONDS,
        job_id=f"inter_payment_order::{payment_order_name}",
        deduplicate=True,
        payment_order_name=payment_order_name,
        requested_at=str(now_datetime()),
    )
    return job is not None


def execute_payment_order(payment_order_name: str, requested_at: str | None = None) -> dict:
    """Send an ``Approved`` order to the bank - once in its life (I1, I2, I3).

    Returns ``{"status": "awaiting_bank" | "completed" | "needs_verification" | "failed" |
    "skipped" | "blocked", ...}``.
    """
    if not is_integration_enabled():
        return {"status": "blocked", "message": _("The Banco Inter integration is disabled")}
    if _is_request_stale(requested_at):
        return _skip_stale_request(payment_order_name, requested_at)
    order = _claim(payment_order_name)
    if order is None:
        return {"status": "skipped", "message": _("The order is not Approved, or it was already sent once")}
    try:
        client, payload = _prepare_send(order)
    except Exception as error:  # nothing was sent
        return _fail_before_send(order, error)
    result = _send_and_record(order, client, payload)
    if result["status"] != "awaiting_bank":
        return result
    return _follow_up(order, result)


def _is_request_stale(requested_at) -> bool:
    if not requested_at:
        return False
    try:
        age = now_datetime() - get_datetime(requested_at)
    except Exception:
        return True  # a request whose age cannot be told is not executed
    return age > REQUEST_MAX_AGE


def _skip_stale_request(name: str, requested_at) -> dict:
    message = _(
        "The execution requested at {0} waited more than 15 minutes for a worker and was NOT sent. "
        "Check that it was not paid some other way, then execute the order again if it is still due."
    ).format(requested_at)
    _best_effort(alert_operator, _("Inter payment {0} was not executed").format(name), message, name)
    return {"status": "skipped", "message": message}


def _claim(name: str) -> dict | None:
    """``Approved`` -> ``Processing`` under a row lock, committed before anything is sent (I2).

    Reading the new columns here makes an un-migrated schema fail before any request exists.
    """
    order = frappe.db.get_value(DOCTYPE, name, ORDER_FIELDS, as_dict=True, for_update=True)
    claimable = bool(order) and order.get("docstatus") == 1 and order.get("status") == "Approved"
    if not claimable or order.get("idempotency_key"):
        frappe.db.rollback()
        if claimable:
            _report_reused_order(name)
        return None
    claim = {
        "status": "Processing",
        "idempotency_key": str(uuid.uuid4()),
        "bank_request_at": now_datetime(),
        **dict.fromkeys(CLEARED_ON_CLAIM),
    }
    frappe.db.set_value(DOCTYPE, name, claim)
    frappe.db.commit()
    return {**order, **claim}


def _report_reused_order(name: str) -> None:
    message = _(
        "The order is Approved but already carries an idempotency key: it was sent to the bank before and is "
        "never sent twice (I3). Cancel it and create a new order if the payment is still due."
    )
    _best_effort(alert_operator, _("Inter payment {0} was not executed").format(name), message, name)


# ---------------------------------------------------------------------------
# Before the send: whatever fails here means nothing reached the bank
# ---------------------------------------------------------------------------

def _prepare_send(order: dict) -> tuple:
    payment_type = order.get("payment_type")
    if payment_type == "TED":
        raise _NothingSent(_("Banco Inter's Banking API has no TED endpoint"))
    if payment_type not in (PIX, BOLETO):
        raise _NothingSent(_("Unknown payment type: {0}").format(payment_type))
    if order.get("purchase_invoice"):
        reason = check_invoice_payable(
            order["purchase_invoice"], order.get("amount"), order_name=order["name"], company=order.get("company")
        )
        if reason:
            raise _NothingSent(reason)
    _check_inter_account(order)
    payload = _build_payload(order)
    client = InterAPIClient(order["inter_company_account"])
    _check_certificate(client, order)
    return client, payload


def _check_certificate(client, order: dict) -> None:
    """Resolve the mTLS certificate HERE, where a failure still means nothing was sent.

    The client resolves it inside the request, and a missing or moved file raises a plain
    ``FileNotFoundError`` there - neither an ``InterAPIError`` nor an ``InterAuthError``. It would
    land in the "the request may be at the bank" branch and freeze the invoice behind a
    ``Needs Verification`` that asks a person to check a statement for a payment no socket ever
    carried.
    """
    try:
        client.auth.get_cert_paths()
    except Exception as error:
        raise _NothingSent(
            _("The certificate of Inter Company Account {0} could not be read: {1}").format(
                order.get("inter_company_account"), error
            )
        ) from error


def _check_inter_account(order: dict) -> None:
    account = order.get("inter_company_account")
    account_company = frappe.db.get_value(ACCOUNT_DOCTYPE, account, "company") if account else None
    if not account_company:
        raise _NothingSent(_("Inter Company Account {0} was not found").format(account))
    if order.get("company") and account_company != order["company"]:
        raise _NothingSent(
            _("Inter Company Account {0} belongs to company {1}, not to {2}").format(
                account, account_company, order["company"]
            )
        )


def _build_payload(order: dict) -> dict:
    amount = flt(order.get("amount"), 2)
    if amount <= 0:
        raise _NothingSent(_("The payment amount must be greater than zero"))
    if order["payment_type"] == PIX:
        return _build_pix_payload(order, amount)
    return _build_boleto_payload(order, amount)


def _build_pix_payload(order: dict, amount: float) -> dict:
    key = str(order.get("pix_key") or "").strip()
    if not key:
        raise _NothingSent(_("The order has no PIX key"))
    description = _("Payment {0}").format(order.get("purchase_invoice") or order["name"])
    payload = {
        "valor": amount,
        "descricao": description[:PIX_DESCRIPTION_LIMIT],
        "destinatario": {"tipo": "CHAVE", "chave": key},
    }
    return _with_payment_date(payload, order)


def _build_boleto_payload(order: dict, amount: float) -> dict:
    barcode = "".join(char for char in str(order.get("barcode") or "") if char.isdigit())
    if len(barcode) not in BARCODE_LENGTHS:
        raise _NothingSent(_("The barcode must have 44, 47 or 48 digits"))
    due_date = getdate(order["boleto_due_date"]) if order.get("boleto_due_date") else None
    if not due_date:
        raise _NothingSent(_("The boleto has no due date"))
    payload = {"codBarraLinhaDigitavel": barcode, "valorPagar": f"{amount:.2f}", "dataVencimento": str(due_date)}
    return _with_payment_date(payload, order)


def _with_payment_date(payload: dict, order: dict) -> dict:
    """``dataPagamento`` only for a date after today; without it the bank pays today."""
    scheduled = order.get("scheduled_date")
    if scheduled and getdate(scheduled) > getdate(now_datetime()):
        return {**payload, "dataPagamento": str(getdate(scheduled))}
    return payload


def _fail_before_send(order: dict, error: Exception) -> dict:
    if isinstance(error, _NothingSent):
        reason = str(error)
    else:
        reason = _("The payment could not be prepared and was not sent: {0}").format(f"{type(error).__name__}: {error}")
        _best_effort(_log_traceback, _error_title(order["name"], "not sent"))
    wrote = mark_failed(order["name"], reason, expected_from=FROM_PROCESSING)
    return {"status": "failed" if wrote else "skipped", "message": reason}


# ---------------------------------------------------------------------------
# The send (the only place that talks to the bank's payment endpoints - I1)
# ---------------------------------------------------------------------------

def _send_and_record(order: dict, client, payload: dict) -> dict:
    name = order["name"]
    try:
        response = _send(client, order, payload)
    except InterAmbiguousResultError as error:
        return _unknown_outcome(name, _("The bank did not give a definitive answer: {0}").format(error))
    except (InterAPIError, InterAuthError) as error:  # definitive by the client's contract
        return _rejected_by_bank(order, error)
    except BaseException as error:  # RQ's JobTimeoutException included: the request may be at the bank
        _flag_unknown_outcome(name, error)
        raise
    try:
        return _record_bank_answer(order, response)
    except BaseException as error:
        _flag_unknown_outcome(name, error, response)
        raise


def _send(client, order: dict, payload: dict) -> dict:
    if order["payment_type"] == PIX:
        response = client.send_pix(payload, order["idempotency_key"])
    else:
        response = client.pay_barcode(payload)
    return response if isinstance(response, dict) else {"raw": response}


def _unknown_outcome(name: str, reason: str) -> dict:
    wrote = mark_needs_verification(name, reason, expected_from=FROM_PROCESSING)
    return {"status": "needs_verification" if wrote else "skipped", "message": reason}


def _flag_unknown_outcome(name: str, error: BaseException, response: dict | None = None) -> None:
    """Best effort on the way out of an unexpected exception. Never raises: the caller re-raises."""
    reason = _("The send ended with {0}. The bank may hold this payment.").format(f"{type(error).__name__}: {error}")
    if response is not None:
        reason = f"{reason} {_('Answer of the bank: {0}').format(_as_json(response))}"
    try:
        mark_needs_verification(name, reason, expected_from=FROM_PROCESSING)
    except Exception:
        _best_effort(frappe.log_error, title=_error_title(name, "could not be flagged"), message=reason)


def _rejected_by_bank(order: dict, error: Exception) -> dict:
    status_code = getattr(error, "status_code", None)
    reason = _("Banco Inter rejected the payment: {0}").format(error)
    if status_code == 406 and order["payment_type"] == BOLETO:
        reason = _(
            "Banco Inter says this bill is already paid (HTTP 406, título já liquidado). "
            "Check the bank statement before paying it any other way. {0}"
        ).format(error)
    answer = {"error": str(error), "status_code": status_code, "response_body": getattr(error, "response_body", None)}
    wrote = mark_failed(order["name"], reason, expected_from=FROM_PROCESSING, response=answer)
    return {"status": "failed" if wrote else "skipped", "message": reason}


def _record_bank_answer(order: dict, response: dict) -> dict:
    name = order["name"]
    bank_id, bank_status = _accepted_as(order["payment_type"], response)
    if not bank_id:
        return _unknown_outcome(name, _("The bank accepted the request without an id: {0}").format(_as_json(response)))
    recorded = mark_awaiting_bank(
        name, expected_from=FROM_PROCESSING, bank_id=bank_id, bank_status=bank_status, response=response
    )
    if not recorded:
        message = _(
            "The bank accepted this payment with id {0} ({1}), but the order had already left Processing. "
            "Resolve it as 'at the bank' with this id."
        ).format(bank_id, bank_status)
        _best_effort(alert_operator, _("Inter payment {0} needs verification").format(name), message, name)
        return {"status": "needs_verification", "message": message, "bank_id": bank_id}
    return {"status": "awaiting_bank", "bank_id": bank_id, "bank_status": bank_status, "response": response}


def _accepted_as(payment_type: str, response: dict) -> tuple[str, str]:
    """``(bank id, bank status)`` of a 2xx answer. The POST never completes a Pix (spec 3)."""
    if payment_type == PIX:
        kind = str(response.get("tipoRetorno") or "")
        bank_status = "AGUARDANDO_APROVACAO" if kind == "APROVACAO" else kind
        return str(response.get("codigoSolicitacao") or ""), bank_status
    return str(response.get("codigoTransacao") or ""), str(response.get("statusPagamento") or "")


def _follow_up(order: dict, accepted: dict) -> dict:
    """Pix: one immediate best-effort poll. Boleto: the POST already carries ``statusPagamento``."""
    if order["payment_type"] == PIX:
        outcome = poll_bank_status(order["name"])
    else:
        answer = {
            "bank_status": accepted["bank_status"], "transaction_id": accepted["bank_id"],
            "errors": [], "response": accepted["response"],
        }
        outcome = _apply_bank_status(order, answer, expected_from=FROM_AWAITING_BANK)
    if outcome.get("status") in ("completed", "failed", "needs_verification"):
        return outcome
    return {key: value for key, value in accepted.items() if key != "response"}


# ---------------------------------------------------------------------------
# Polling: never sends, never fails an order from absence
# ---------------------------------------------------------------------------

def poll_bank_status(payment_order_name: str, *, interactive: bool = False) -> dict:
    """Ask the bank about an ``Awaiting Bank`` order and apply the mapping of spec 3.

    ``interactive`` is set by the desk button: it asks once instead of retrying, because the
    retries sleep in the caller and the operator can simply click again.
    """
    if not is_integration_enabled():
        return {"status": "blocked", "message": _("The Banco Inter integration is disabled")}
    order = frappe.db.get_value(DOCTYPE, payment_order_name, ORDER_FIELDS, as_dict=True)
    if not order:
        return {"status": "skipped", "message": _("Inter Payment Order {0} was not found").format(payment_order_name)}
    if not order.get("approval_code"):
        return {"status": "no_bank_id"}
    if order.get("docstatus") != 1 or order.get("status") != "Awaiting Bank":
        return {"status": "skipped", "order_status": order.get("status")}
    try:
        answer = _ask_bank(order, order["approval_code"], max_retries=0 if interactive else None)
    except JobTimeoutException:
        raise
    except Exception as error:
        return _poll_error(order, error)
    if answer is None:
        return {"status": "unchanged", "message": _("The bank has no record of this payment yet")}
    return _apply_bank_status(order, answer, expected_from=FROM_AWAITING_BANK)


def _ask_bank(order: dict, bank_id: str, *, max_retries: int | None = None) -> dict | None:
    """The bank's view of ``bank_id``, or ``None`` when it shows nothing that is provably ours.

    ``max_retries=0`` is for the desk: the retry policy sleeps in the calling process, and a person
    clicking a button must not hold a web worker for minutes.
    """
    payment_type = order.get("payment_type")
    if payment_type not in (PIX, BOLETO):
        return None
    client = InterAPIClient(order["inter_company_account"])
    if payment_type == PIX:
        return _pix_answer(client.get_pix_payment(bank_id, max_retries=max_retries), bank_id)
    query = {**_boleto_query(order, bank_id), "max_retries": max_retries}
    return _boleto_answer(client.find_barcode_payments(**query), bank_id)


def _pix_answer(response, bank_id: str) -> dict | None:
    transaction = response.get("transacaoPix") if isinstance(response, dict) else None
    if not isinstance(transaction, dict) or not transaction.get("status"):
        return None
    if transaction.get("codigoSolicitacao") not in (None, "", bank_id):
        return None  # an answer about some other payment
    return {
        "bank_status": str(transaction["status"]),
        "transaction_id": str(transaction.get("endToEnd") or ""),
        "errors": transaction.get("erros") or [],
        "response": response,
    }


def _boleto_answer(payments, bank_id: str) -> dict | None:
    for payment in payments or []:
        if isinstance(payment, dict) and payment.get("codigoTransacao") == bank_id and payment.get("statusPagamento"):
            return {
                "bank_status": str(payment["statusPagamento"]),
                "transaction_id": bank_id,
                "errors": [],
                "response": payment,
            }
    return None


def _boleto_query(order: dict, bank_id: str) -> dict:
    """Without dates the bank only searches the last 30 days: one day around the request."""
    query = {"codigo_transacao": bank_id, "filter_date_by": "INCLUSAO"}
    requested = getdate(order["bank_request_at"]) if order.get("bank_request_at") else None
    if not requested:
        return query
    return {**query, "start_date": requested - timedelta(days=1), "end_date": requested + timedelta(days=1)}


def _poll_error(order: dict, error: Exception) -> dict:
    """404, 403, timeouts, anything: the order stays exactly as it is."""
    name = order["name"]
    status_code = getattr(error, "status_code", None)
    if status_code == 403:
        scope = READ_SCOPES.get(order.get("payment_type"), "")
        message = _(
            "Banco Inter refused to show the payment (HTTP 403). The application probably lacks the scope {0}; "
            "until it is granted no payment of this kind can be confirmed. {1}"
        ).format(scope, error)
        _best_effort(alert_operator, _("Inter payment {0}: the bank cannot be consulted").format(name), message, name)
    else:
        detail = f"{type(error).__name__}: {error}"
        _best_effort(frappe.log_error, title=_error_title(name, "poll failed"), message=detail)
    return {"status": "error", "message": str(error), "http_status": status_code}


def _classify(payment_type: str, bank_status: str) -> str:
    # Normalise only for the decision: _store_bank_status keeps the bank's own spelling for audit.
    bank_status = str(bank_status or "").strip().upper()
    if payment_type == PIX:
        paid, rejected, human = PIX_PAID, PIX_REJECTED, PIX_NEEDS_HUMAN
    else:
        paid, rejected, human = BOLETO_PAID, BOLETO_REJECTED, frozenset()
    if bank_status in paid:
        return "paid"
    if bank_status in rejected:
        return "rejected"
    if bank_status in human:
        return "human"
    return "in_flight"  # unknown values too: the order keeps protecting its invoice


def _apply_bank_status(order: dict, answer: dict, *, expected_from: tuple) -> dict:
    name, bank_status = order["name"], answer["bank_status"]
    kind = _classify(order["payment_type"], bank_status)
    if kind == "paid":
        wrote = mark_completed(
            name, expected_from=expected_from, transaction_id=answer["transaction_id"],
            bank_status=bank_status, response=answer["response"],
        )
        return _poll_result("completed", wrote, bank_status)
    if kind == "rejected":
        reason = _("The bank reports this payment as {0}: it was not paid").format(bank_status)
        wrote = mark_failed(
            name, reason, expected_from=expected_from, bank_status=bank_status, response=answer["response"]
        )
        return _poll_result("failed", wrote, bank_status)
    _store_bank_status(name, bank_status, expected_from=expected_from)
    if kind == "human":
        reason = _("The bank reports this payment as {0}, which is not provably final. Errors of the bank: {1}").format(
            bank_status, _as_json(answer["errors"])
        )
        wrote = mark_needs_verification(name, reason, expected_from=expected_from)
        return _poll_result("needs_verification", wrote, bank_status)
    return {"status": "awaiting_bank", "bank_status": bank_status}


def _poll_result(status: str, wrote: bool, bank_status: str) -> dict:
    return {"status": status if wrote else "unchanged", "bank_status": bank_status}


def _store_bank_status(name: str, bank_status: str, *, expected_from: tuple) -> None:
    """Remember what the bank last said - only when it changed, so an open form stays valid."""
    if not bank_status:
        return
    row = frappe.db.get_value(DOCTYPE, name, ["status", "docstatus", "bank_status"], as_dict=True, for_update=True)
    unchanged = not row or row.get("docstatus") != 1 or row.get("status") not in expected_from
    if unchanged or row.get("bank_status") == bank_status:
        frappe.db.rollback()
        return
    frappe.db.set_value(DOCTYPE, name, "bank_status", bank_status)
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Compare-and-set core (I5, I9): every mark_* returns True only when it wrote
# ---------------------------------------------------------------------------

def mark_awaiting_bank(name: str, *, expected_from: tuple, bank_id: str, bank_status: str, response=None) -> bool:
    values = {"status": "Awaiting Bank", "approval_code": bank_id, "bank_status": bank_status or None}
    if response is not None:
        values["inter_response"] = _as_json(response)
    if not _transition(name, values, expected_from=expected_from):
        return False
    _after_transition(name, _("The bank holds this payment (id {0}, status {1}).").format(bank_id, bank_status or "-"))
    return True


def mark_completed(
    name: str, *, expected_from: tuple, transaction_id: str = "", bank_status: str = "", response=None, paid_on=None
) -> bool:
    values = {"status": "Completed", "execution_date": get_datetime(paid_on) if paid_on else now_datetime()}
    if transaction_id:
        values["transaction_id"] = transaction_id
    if bank_status:
        values["bank_status"] = bank_status
    if response is not None:
        values["inter_response"] = _as_json(response)
    if not _transition(name, values, expected_from=expected_from):
        return False
    _after_transition(name, _("The payment is confirmed as paid."))
    create_payment_entry_for_order(name, paid_on=paid_on)
    return True


def mark_failed(name: str, reason: str, *, expected_from: tuple, bank_status: str = "", response=None) -> bool:
    """The bank definitely does not hold this payment: the invoice is free for a new attempt."""
    return _mark_failed(
        name, reason, expected_from=expected_from, bank_status=bank_status, response=response, alert=True
    )


def mark_needs_verification(name: str, reason: str, *, expected_from: tuple) -> bool:
    """The bank may hold this payment: only a human can tell. The invoice stays protected."""
    if not _transition(name, {"status": "Needs Verification"}, expected_from=expected_from):
        return False
    comment = _(
        "{0} Do not pay this invoice any other way: check the bank statement, the bank's approval queue and its "
        "scheduled payments, then use Resolve Verification."
    ).format(reason)
    _after_transition(name, comment, alert_subject=_("Inter payment {0} needs verification").format(name))
    return True


def _mark_failed(name: str, reason: str, *, expected_from: tuple, bank_status: str, response, alert: bool) -> bool:
    values = {
        "status": "Failed",
        "invoice_lock": None,
        "inter_response": _as_json(response if response is not None else {"error": reason}),
    }
    if bank_status:
        values["bank_status"] = bank_status
    if not _transition(name, values, expected_from=expected_from):
        return False
    subject = _("Inter payment {0} failed").format(name) if alert else None
    _after_transition(name, _("The payment failed and was not made. {0}").format(reason), alert_subject=subject)
    return True


def _transition(name: str, values: dict, *, expected_from: tuple) -> bool:
    """Write ``values`` only if the row - re-read under a lock - is still where the caller expects."""
    row = frappe.db.get_value(DOCTYPE, name, ["status", "docstatus", "payment_entry"], as_dict=True, for_update=True)
    if not row or row.get("docstatus") != 1 or row.get("status") not in expected_from:
        frappe.db.rollback()
        _log_lost_transition(name, row, values, expected_from)
        return False
    frappe.db.set_value(DOCTYPE, name, values)
    frappe.db.commit()  # the safe state first; comments and alerts come after
    return True


def _log_lost_transition(name: str, row, values: dict, expected_from: tuple) -> None:
    found = f"status {row.get('status')!r}, docstatus {row.get('docstatus')!r}" if row else "no such order"
    message = (
        f"Expected {name} in {expected_from}, found {found}. Nothing was written. "
        f"Refused values: {_as_json(values)}"
    )
    _best_effort(frappe.log_error, title=_error_title(name, "transition refused"), message=message)
    _best_effort(frappe.db.commit)


def _after_transition(name: str, comment: str, alert_subject: str | None = None) -> None:
    """Timeline, alert, realtime - each best effort - then a commit so nothing is left pending."""
    doc = _best_effort(frappe.get_doc, DOCTYPE, name)
    if doc is not None:
        _best_effort(doc.add_comment, "Info", comment)
    if alert_subject:
        _best_effort(alert_operator, alert_subject, comment, name)
    if doc is not None:
        _best_effort(doc.notify_update)
    _best_effort(frappe.db.commit)


def _comment(name: str, text: str) -> None:
    doc = _best_effort(frappe.get_doc, DOCTYPE, name)
    if doc is not None:
        _best_effort(doc.add_comment, "Info", text)


def _best_effort(action, *args, **kwargs):
    """Run a side effect that must never undo or hide a committed state."""
    try:
        return action(*args, **kwargs)
    except JobTimeoutException:
        raise
    except Exception as error:
        try:
            frappe.log_error(title="Inter payment side effect failed", message=f"{action!r}: {error!r}")
        except Exception:
            pass
        return None


def _log_traceback(title: str) -> None:
    frappe.log_error(title=title, message=frappe.get_traceback())


def _as_json(value) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, indent=1)


def _error_title(name: str, what: str) -> str:
    return f"Inter payment {name}: {what}"[:ERROR_LOG_TITLE_LENGTH]


# ---------------------------------------------------------------------------
# Payment Entry (I7): idempotent, never changes the order status, never retried on its own
# ---------------------------------------------------------------------------

def create_payment_entry_for_order(payment_order_name: str, paid_on=None) -> str | None:
    """Settle the invoice of a ``Completed`` order. Returns the Payment Entry, or ``None``."""
    try:
        entry = _settle(payment_order_name, paid_on)
        frappe.db.commit()
        return entry
    except Exception as error:
        frappe.db.rollback()
        _report_entry_failure(payment_order_name, error)
        if isinstance(error, JobTimeoutException):
            raise
        return None


def _settle(name: str, paid_on) -> str | None:
    order = frappe.db.get_value(DOCTYPE, name, ORDER_FIELDS, as_dict=True, for_update=True)
    if not order or order.get("docstatus") != 1 or order.get("status") != "Completed":
        return None  # I7: only what was paid is settled
    entry = order.get("payment_entry") or _adopt_existing_entry(name)
    if not entry:
        invoice = _invoice_to_settle(order)
        if invoice is None:
            return None
        entry = _insert_payment_entry(order, invoice, paid_on)
    frappe.db.set_value(DOCTYPE, name, {"payment_entry": entry, "invoice_lock": None})
    return entry


def _adopt_existing_entry(name: str) -> str | None:
    """An entry that already points to this order is linked (a draft is submitted first)."""
    entries = frappe.get_all(
        "Payment Entry",
        filters={"inter_payment_order": name, "docstatus": ["<", 2]},
        fields=["name", "docstatus"],
        order_by="docstatus desc, creation asc",
    )
    if not entries:
        return None
    entry = entries[0]
    if entry.get("docstatus") == 0:
        doc = frappe.get_doc("Payment Entry", entry.get("name"))
        doc.flags.ignore_permissions = True
        doc.submit()
    return entry.get("name")


def _invoice_to_settle(order: dict):
    """The invoice row when an entry can be booked against it; otherwise say why on the timeline."""
    name, invoice_name = order["name"], order.get("purchase_invoice")
    if not invoice_name:
        _leave_to_operator(name, _("This order has no Purchase Invoice: record the payment by hand."), alert=True)
        return None
    invoice = frappe.db.get_value("Purchase Invoice", invoice_name, INVOICE_FIELDS, as_dict=True)
    if not invoice or invoice.get("docstatus") != 1:
        text = _("Purchase Invoice {0} is not submitted: record the payment by hand.").format(invoice_name)
        _leave_to_operator(name, text, alert=True)
        return None
    if flt(invoice.get("outstanding_amount"), 2) <= 0:
        text = _(
            "No Payment Entry was created: Purchase Invoice {0} has nothing outstanding any more. "
            "If it was also paid some other way, this payment is a credit to recover from the supplier."
        ).format(invoice_name)
        _leave_to_operator(name, text, alert=False)
        return None
    return invoice


def _leave_to_operator(name: str, text: str, alert: bool) -> None:
    _comment(name, text)
    if alert:
        _best_effort(alert_operator, _("Inter payment {0} has no Payment Entry").format(name), text, name)


def _insert_payment_entry(order: dict, invoice: dict, paid_on) -> str:
    posting_date = getdate(paid_on or order.get("execution_date") or now_datetime())
    amount = flt(order.get("amount"), 2)
    entry = frappe.new_doc("Payment Entry")
    entry.payment_type = "Pay"
    entry.company = invoice.get("company")
    entry.posting_date = posting_date
    entry.party_type = "Supplier"
    entry.party = invoice.get("supplier")
    entry.paid_from = _bank_gl_account(order.get("inter_company_account"))
    entry.paid_to = invoice.get("credit_to")
    entry.paid_amount = amount
    entry.received_amount = amount
    entry.reference_no = order.get("transaction_id") or order.get("approval_code") or order["name"]
    entry.reference_date = posting_date
    entry.inter_payment_order = order["name"]
    entry.append("references", {
        "reference_doctype": "Purchase Invoice",
        "reference_name": order["purchase_invoice"],
        "allocated_amount": min(amount, flt(invoice.get("outstanding_amount"), 2)),
    })
    entry.insert(ignore_permissions=True)  # the money already left: booking it is a system action
    entry.submit()
    return entry.name


def _bank_gl_account(inter_account: str | None) -> str | None:
    bank_account = frappe.db.get_value(ACCOUNT_DOCTYPE, inter_account, "bank_account") if inter_account else None
    return frappe.db.get_value("Bank Account", bank_account, "account") if bank_account else None


def _report_entry_failure(name: str, error: Exception) -> None:
    detail = f"{type(error).__name__}: {error}"
    _best_effort(_log_traceback, _error_title(name, "Payment Entry failed"))
    message = _(
        "The payment was made, but its Payment Entry could not be created: {0}. There is no automatic retry: "
        "fix the cause and use Create Payment Entry on the order."
    ).format(detail)
    _best_effort(alert_operator, _("Inter payment {0} has no Payment Entry").format(name), message, name)
    _best_effort(frappe.db.commit)


# ---------------------------------------------------------------------------
# Needs Verification: a human decides, with the bank's word when there is one
# ---------------------------------------------------------------------------

def resolve_verification(
    payment_order_name: str, outcome: str, *, bank_reference: str = "", paid_on=None, note: str = ""
) -> dict:
    order = frappe.db.get_value(DOCTYPE, payment_order_name, ORDER_FIELDS, as_dict=True)
    if not order or order.get("docstatus") != 1 or order.get("status") != "Needs Verification":
        frappe.throw(_("Only an order that needs verification can be resolved"))
    handlers = {"at_bank": _resolve_at_bank, "paid": _resolve_paid, "not_paid": _resolve_not_paid}
    if outcome not in handlers:
        frappe.throw(_("Unknown outcome: {0}").format(outcome))
    return handlers[outcome](order, str(bank_reference or "").strip(), paid_on, str(note or "").strip())


def _resolve_at_bank(order: dict, reference: str, paid_on, note: str) -> dict:
    name, known_id = order["name"], order.get("approval_code")
    reference = reference or known_id or ""
    if not reference:
        frappe.throw(_("The bank reference (codigoSolicitacao / codigoTransacao) is required"))
    if known_id and reference != known_id:
        frappe.throw(_("This order already carries the bank id {0}").format(known_id))
    if not is_integration_enabled():
        frappe.throw(_("The Banco Inter integration is disabled: the bank cannot be consulted"))
    _refuse_reference_in_use(name, reference)
    answer = _confirmed_by_bank(order, reference)
    recorded = mark_awaiting_bank(
        name, expected_from=FROM_NEEDS_VERIFICATION, bank_id=reference,
        bank_status=answer["bank_status"], response=answer["response"],
    )
    if not recorded:
        frappe.throw(_("The order changed while it was being resolved. Reload it."))
    _comment(name, _resolution_text("at the bank", reference, note))
    # _apply_bank_status rolls back when the bank status has not moved, and that would throw away
    # the operator's attestation along with it.
    _best_effort(frappe.db.commit)
    return _apply_bank_status(order, answer, expected_from=FROM_AWAITING_BANK)


def _confirmed_by_bank(order: dict, reference: str) -> dict:
    try:
        answer = _ask_bank(order, reference, max_retries=0)  # always a person at a keyboard
    except JobTimeoutException:
        raise
    except Exception as error:
        frappe.throw(_("The bank did not confirm reference {0}: {1}").format(reference, error))
    if answer is None:
        frappe.throw(_("The bank shows no payment with reference {0}").format(reference))
    return answer


def _resolve_paid(order: dict, reference: str, paid_on, note: str) -> dict:
    name = order["name"]
    if not reference or not paid_on:
        frappe.throw(_("The bank reference and the payment date are required"))
    paid_date = _parse_date(paid_on)
    if paid_date is None or paid_date > getdate(now_datetime()):
        frappe.throw(_("{0} is not a valid payment date").format(paid_on))
    _refuse_reference_in_use(name, reference)
    if not mark_completed(name, expected_from=FROM_NEEDS_VERIFICATION, transaction_id=reference, paid_on=paid_date):
        frappe.throw(_("The order changed while it was being resolved. Reload it."))
    _comment(name, _resolution_text(f"paid on {paid_date}", reference, note))
    _best_effort(frappe.db.commit)
    return {"status": "completed", "payment_entry": frappe.db.get_value(DOCTYPE, name, "payment_entry")}


def _resolve_not_paid(order: dict, reference: str, paid_on, note: str) -> dict:
    name = order["name"]
    if order.get("approval_code"):
        known_id = order["approval_code"]
        frappe.throw(_("The bank gave this payment the id {0}: resolve it as 'at the bank'").format(known_id))
    if not note:
        frappe.throw(_("A note saying where you checked is required"))
    reason = _resolution_text("NOT paid", "", note)
    wrote = _mark_failed(
        name, reason, expected_from=FROM_NEEDS_VERIFICATION, bank_status="", response=None, alert=False
    )
    if not wrote:
        frappe.throw(_("The order changed while it was being resolved. Reload it."))
    return {"status": "failed"}


def _refuse_reference_in_use(name: str, reference: str) -> None:
    for field in ("approval_code", "transaction_id"):
        other = frappe.db.get_value(DOCTYPE, {field: reference, "name": ["!=", name]}, "name")
        if other:
            frappe.throw(_("Inter Payment Order {0} already carries the bank reference {1}").format(other, reference))


def _resolution_text(verdict: str, reference: str, note: str) -> str:
    parts = [f"Resolved as {verdict} by {frappe.session.user}."]
    if reference:
        parts.append(f"Bank reference: {reference}.")
    if note:
        parts.append(f"Note: {note}")
    return " ".join(parts)


def _parse_date(value):
    try:
        return getdate(value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Hourly cron: it never sends
# ---------------------------------------------------------------------------

def scheduled_payment_status_check() -> None:
    """Scheduler entry point: flag stale claims and poll the bank. It NEVER sends (I3)."""
    if not is_integration_enabled():
        return
    _flag_stale_processing_orders()
    _poll_waiting_orders(_expire_old_waiting_orders())


def _flag_stale_processing_orders() -> None:
    cutoff = now_datetime() - STALE_PROCESSING_AFTER
    orders = frappe.get_all(
        DOCTYPE, filters={"status": "Processing", "docstatus": 1}, fields=["name", "bank_request_at"]
    )
    reason = _(
        "The order was still Processing 30 minutes after the request: the worker died or lost the bank's answer."
    )
    for order in orders:
        requested = order.get("bank_request_at")
        if requested and get_datetime(requested) >= cutoff:
            continue  # its job may still be running
        _guarded(mark_needs_verification, order.get("name"), reason, expected_from=FROM_PROCESSING)


def _expire_old_waiting_orders() -> list:
    """Orders the bank can no longer be asked about go to a human; the others are returned."""
    cutoff = now_datetime() - AWAITING_BANK_MAX_AGE
    orders = frappe.get_all(
        DOCTYPE,
        filters={"status": "Awaiting Bank", "docstatus": 1},
        fields=["name", "payment_type", "bank_request_at"],
        order_by="name asc",
    )
    reason = _("The bank has not settled this payment in 85 days and stops answering about it after 90.")
    waiting = []
    for order in orders:
        requested = order.get("bank_request_at")
        if requested and get_datetime(requested) < cutoff:
            _guarded(mark_needs_verification, order.get("name"), reason, expected_from=FROM_AWAITING_BANK)
        else:
            waiting.append(order)
    return waiting


def _poll_waiting_orders(waiting: list) -> None:
    batch = _polling_window(waiting)
    deadline = now_datetime() + POLL_BUDGET
    forbidden = set()  # a missing read scope fails every order of that kind: one alert is enough
    polled = []
    for order in batch:
        if now_datetime() >= deadline:
            break
        polled.append(order)
        if order.get("payment_type") in forbidden:
            continue
        result = _guarded(poll_bank_status, order.get("name"))
        if result and result.get("http_status") == 403:
            forbidden.add(order.get("payment_type"))
    _report_orders_left(waiting, polled)


def _report_orders_left(waiting: list, polled: list) -> None:
    """No silent cap: an order nobody asked about this run has to be visible somewhere."""
    if len(polled) == len(waiting):
        return
    asked = {order.get("name") for order in polled}
    left = [order.get("name") for order in waiting if order.get("name") not in asked]
    frappe.logger().warning(
        f"Inter payments: {len(waiting)} orders await the bank, {len(polled)} polled this run; "
        f"left for the next run: {left}"
    )


def _polling_window(waiting: list) -> list:
    """At most 15 per run; the window moves every hour so that no order starves behind the cap."""
    if len(waiting) <= MAX_POLLS_PER_RUN:
        return list(waiting)
    run = int(now_datetime().timestamp() // 3600)
    start = (run * MAX_POLLS_PER_RUN) % len(waiting)
    return (list(waiting) + list(waiting))[start:start + MAX_POLLS_PER_RUN]


def _guarded(action, name: str, *args, **kwargs):
    """One broken order never stops the run."""
    try:
        return action(name, *args, **kwargs)
    except JobTimeoutException:
        raise
    except Exception as error:
        _best_effort(frappe.db.rollback)
        _best_effort(frappe.log_error, title=_error_title(name, "status check failed"), message=f"{error!r}")
        return None


# ---------------------------------------------------------------------------
# Creating an order for an invoice (API and weekly scheduler)
# ---------------------------------------------------------------------------

def create_payment_order_for_invoice(
    invoice_name: str, payment_type: str, *, pix_key: str = "", barcode: str = "",
    scheduled_date=None, boleto_due_date=None, submit: bool = True,
) -> str:
    """Create (and by default submit) an order whose party, company and amount come from the invoice."""
    invoice = frappe.db.get_value("Purchase Invoice", invoice_name, INVOICE_FIELDS, as_dict=True)
    if not invoice:
        frappe.throw(_("Purchase Invoice {0} was not found").format(invoice_name))
    account = get_inter_account_for_company(invoice.get("company"))
    if not account:
        frappe.throw(_("No Inter Company Account with sync enabled for company {0}").format(invoice.get("company")))
    reason = check_invoice_payable(invoice_name, invoice.get("outstanding_amount"), company=invoice.get("company"))
    if reason:
        frappe.throw(reason)
    order = frappe.new_doc(DOCTYPE)
    order.payment_type = payment_type
    order.company = invoice.get("company")
    order.inter_company_account = account
    order.purchase_invoice = invoice_name
    order.party_type = "Supplier"
    order.party = invoice.get("supplier")
    order.amount = flt(invoice.get("outstanding_amount"), 2)
    order.scheduled_date = scheduled_date or None
    _set_recipient(order, invoice.get("supplier"))
    if payment_type == PIX:
        order.pix_key = pix_key
    elif payment_type == BOLETO:
        order.barcode = barcode
        order.boleto_due_date = boleto_due_date or invoice.get("due_date")
    order.insert()
    if submit:
        order.submit()
    return order.name


def _set_recipient(order, supplier: str | None) -> None:
    details = frappe.db.get_value("Supplier", supplier, ["supplier_name", "tax_id"], as_dict=True) if supplier else None
    if details:
        order.recipient_name = details.get("supplier_name")
        order.recipient_cpf_cnpj = details.get("tax_id") or ""


# ---------------------------------------------------------------------------
# Payment Entry cancelled (doc_event): runs INSIDE the entry's transaction - no commit, no rollback
# ---------------------------------------------------------------------------

def on_payment_entry_cancel(doc, method=None) -> None:
    """Unlink the cancelled entry so Frappe's back-link check lets it go, and protect the invoice again."""
    order_name = doc.get("inter_payment_order")
    if not order_name:
        return
    fields = ["status", "docstatus", "payment_entry", "purchase_invoice"]
    order = frappe.db.get_value(DOCTYPE, order_name, fields, as_dict=True, for_update=True)
    if not order or order.get("payment_entry") != doc.name:
        return
    frappe.db.set_value(DOCTYPE, order_name, "payment_entry", None)
    _restore_invoice_lock(order_name, order)
    text = _("Payment Entry {0} was cancelled: this payment is no longer booked in the ERP.").format(doc.name)
    _comment(order_name, text)


def _restore_invoice_lock(order_name: str, order: dict) -> None:
    invoice = order.get("purchase_invoice")
    if not invoice or order.get("docstatus") != 1 or not is_blocking(order.get("status"), None):
        return
    try:
        holder = find_blocking_payment_order(invoice, exclude=order_name)
        if holder is None:
            frappe.db.set_value(DOCTYPE, order_name, "invoice_lock", invoice)
            return
        detail = _("Inter Payment Order {0} ({1}) already protects the invoice").format(
            holder["name"], holder["status"]
        )
    except Exception as error:  # the unique key on invoice_lock: another order took the invoice meanwhile
        detail = f"{type(error).__name__}: {error}"
    message = _(
        "Payment Entry cancelled, but Purchase Invoice {0} could not be locked for this order again: {1}. "
        "Make sure the invoice is not paid twice."
    ).format(invoice, detail)
    subject = _("Inter payment {0}: check invoice {1}").format(order_name, invoice)
    # Alerting means HTTP (Telegram waits up to 10 s, twice). This runs inside the Payment Entry's
    # cancel transaction with the order row locked, so hold it until the commit frees both.
    _best_effort(frappe.db.after_commit.add, lambda: _best_effort(alert_operator, subject, message, order_name))
