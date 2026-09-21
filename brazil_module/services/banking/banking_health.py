"""Is the bank still talking to us?

Spec: docs/superpowers/specs/2026-09-21-banking-health-design.md

The integration was switched off by hand in April and nothing mentioned it for five months, while
the daily briefing kept printing an April balance as today's and calling the reconciliation "em
dia". The data to notice was there the whole time - every call is in ``Inter API Log``, the
certificate expiry is on the account - and nothing read it.

So this asks eight questions about the channel, each one on its own, and records a verdict with its
date. What it never does is **write to the bank**. Its model, ``telegram_health``, re-registers the
webhook by itself at night; the banking equivalent would be an unattended job writing to Banco
Inter, which is exactly the shape of the incident this branch exists to fix. This one reports, says
what to do, and leaves the button for a person.

Nothing here raises: ``check()`` answers a button as well as a job.
"""

import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, now_datetime

from brazil_module.services.banking.inter_client import InterAPIClient
from brazil_module.services.banking.payment_alerts import alert_operator
from brazil_module.services.banking.payment_common import ACCOUNT_DOCTYPE

SETTINGS = "Banco Inter Settings"
API_LOG = "Inter API Log"
JOB_DOCTYPE = "Scheduled Job Type"
WEBHOOK_ENDPOINT = "/api/method/brazil_module.api.webhook_receiver"

CERT_WARNING_DAYS = 30
SYNC_STALE_DAYS = 2
MIN_CALLS = 3
ERROR_RATE = 0.5
JOBS_LATE_FACTOR = 3
REMINDER_DAYS = 7
PIX_LIMIT_CODE = "PIXP30"

WATCHED_JOBS = {
    "brazil_module.services.banking.payment_service.scheduled_payment_status_check": 1,
    "brazil_module.services.banking.statement_sync.scheduled_statement_sync": 6,
    "brazil_module.services.banking.boleto_service.scheduled_boleto_status_check": 0.5,
    "brazil_module.services.banking.pix_service.scheduled_pix_status_check": 0.25,
}

# Without the integration these say nothing new: a stale statement is a consequence of the switch
# being off, not a second piece of news. ``jobs`` is deliberately NOT here - see _jobs().
NEEDS_INTEGRATION = ("statement_sync", "api_errors", "pix_limit", "webhook")

STATE_FIELD = "banking_health_state"
STATUS_FIELD = "banking_health_status"
CHECKED_FIELD = "banking_health_checked_on"
SINCE_FIELD = "banking_health_since"
ALERTED_FIELD = "banking_health_alerted_on"

RECONNECT = "Reconecte a integracao em Banco Inter Settings"


def site_webhook_url() -> str:
    """Where Banco Inter should be delivering, for this site."""
    return f"{frappe.utils.get_url().rstrip('/')}{WEBHOOK_ENDPOINT}"


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------

def check(record: bool = True) -> dict:
    """Ask every question about the channel. Never raises: this answers a button too."""
    enabled = _is_enabled()
    results = [_run(one, enabled) for one in CHECKS]
    problems = [item for item in results if not item["healthy"] and not item["skipped"]]
    verdict = {
        "healthy": not problems,
        "state": _state(problems),
        "summary": _summary(problems),
        "problems": problems,
        "checks": results,
    }
    if record:
        _record(verdict)
    return verdict


def status() -> dict:
    """The last recorded verdict, for the briefing and the form."""
    stored = _stored()
    return {
        "state": stored.get(STATE_FIELD) or "",
        "summary": stored.get(STATUS_FIELD) or "",
        "checked_on": stored.get(CHECKED_FIELD),
        "since": stored.get(SINCE_FIELD),
    }


def _state(problems: list) -> str:
    """The stable key that decides whether to interrupt.

    Never the prose: it carries day counts that grow every night, and change detection on those
    would send a message every morning - which is the other way to be ignored.
    """
    return ",".join(sorted(item["name"] for item in problems)) if problems else "ok"


def _summary(problems: list) -> str:
    if not problems:
        return _("OK - a comunicacao com o Banco Inter esta funcionando")
    return " | ".join(item["problem"] for item in problems)


def _run(one, enabled: bool) -> dict:
    name = one.__name__.lstrip("_")
    if name in NEEDS_INTEGRATION and not enabled:
        return _result(name, True, skipped=True)
    try:
        return one()
    except Exception as error:
        return _result(name, False, _("A verificacao {0} falhou: {1}").format(name, error))


def _result(name: str, healthy: bool, problem: str = "", fixable_by: str = "", skipped: bool = False) -> dict:
    return {
        "name": name,
        "healthy": healthy,
        "problem": problem,
        "fixable_by": fixable_by,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# The eight questions
# ---------------------------------------------------------------------------

def _integration_enabled() -> dict:
    if _is_enabled():
        return _result("integration_enabled", True)
    days = _days_since(_stored().get(SINCE_FIELD))
    since = _(" ha {0} dias").format(days) if days else ""
    return _result("integration_enabled", False, _("DESLIGADA{0}").format(since), RECONNECT)


def _accounts() -> dict:
    if _enabled_accounts():
        return _result("accounts", True)
    return _result(
        "accounts", False,
        _("Nenhuma conta Inter com sincronizacao ligada"),
        _("Marque Sync Enabled em Inter Company Account"),
    )


def _certificates() -> dict:
    today = getdate()
    troubles = []
    for account in _enabled_accounts():
        name = account.get("name")
        if not account.get("certificate_valid"):
            troubles.append(_("{0}: certificado marcado como invalido").format(name))
            continue
        expiry = account.get("certificate_expiry")
        if not expiry:
            troubles.append(_("{0}: sem data de validade registrada").format(name))
            continue
        days = (getdate(expiry) - today).days
        if days < 0:
            troubles.append(_("{0}: certificado vencido ha {1} dias").format(name, -days))
        elif days <= CERT_WARNING_DAYS:
            troubles.append(_("{0}: certificado vence em {1} dias").format(name, days))
    if not troubles:
        return _result("certificates", True)
    return _result(
        "certificates", False, " / ".join(troubles),
        _("Suba o certificado novo em Inter Company Account e salve"),
    )


def _statement_sync() -> dict:
    synced = [account.get("last_statement_sync") for account in _enabled_accounts()]
    newest = max((get_datetime(when) for when in synced if when), default=None)
    if newest is None:
        return _result(
            "statement_sync", False, _("O extrato nunca foi sincronizado"), RECONNECT
        )
    days = (now_datetime() - newest).days
    if days < SYNC_STALE_DAYS:
        return _result("statement_sync", True)
    return _result(
        "statement_sync", False,
        _("O extrato nao sincroniza ha {0} dias").format(days),
        _("Veja o Inter API Log e o Scheduled Job Log"),
    )


def _api_errors() -> dict:
    calls = _recent_calls(["success"])
    if len(calls) < MIN_CALLS:
        return _result("api_errors", True)
    failed = [call for call in calls if not call.get("success")]
    if len(failed) < ERROR_RATE * len(calls):
        return _result("api_errors", True)
    return _result(
        "api_errors", False,
        _("{0} de {1} chamadas ao banco falharam nas ultimas 24h").format(len(failed), len(calls)),
        _("Veja o Inter API Log"),
    )


def _pix_limit() -> dict:
    """One sentence, never one message per rejection: 388 of these went unnoticed in the incident."""
    rejected = [
        call for call in _recent_calls(["response_code", "response_body"])
        if call.get("response_code") == 422 and PIX_LIMIT_CODE in str(call.get("response_body") or "")
    ]
    if not rejected:
        return _result("pix_limit", True)
    return _result(
        "pix_limit", False,
        _("{0} pagamentos Pix recusados nas ultimas 24h por limite diario").format(len(rejected)),
        _("Aumente o limite Pix no app do banco ou pague por boleto"),
    )


def _jobs() -> dict:
    """Not skipped when the integration is off: the framework stamps ``last_execution`` whatever
    the method then decides, so a late job means the scheduler stopped - news at any time."""
    moment = now_datetime()
    late = []
    for method, hours in WATCHED_JOBS.items():
        job = frappe.db.get_value(JOB_DOCTYPE, {"method": method}, ["stopped", "last_execution"], as_dict=True)
        if not job:
            continue  # not installed on this site: bench migrate creates it
        if job.get("stopped"):
            late.append(_("{0} esta parado").format(method.rsplit(".", 1)[-1]))
            continue
        last = job.get("last_execution")
        if not last:
            late.append(_("{0} nunca rodou").format(method.rsplit(".", 1)[-1]))
            continue
        if moment - get_datetime(last) > timedelta(hours=hours * JOBS_LATE_FACTOR):
            late.append(_("{0} nao roda desde {1}").format(method.rsplit(".", 1)[-1], last))
    if not late:
        return _result("jobs", True)
    return _result(
        "jobs", False, " / ".join(late), _("Verifique o scheduler: bench --site <site> scheduler status")
    )


def _webhook() -> dict:
    accounts = _enabled_accounts()
    if not accounts:
        return _result("webhook", True)  # _accounts() already says it
    expected = site_webhook_url()
    try:
        registered = (InterAPIClient(accounts[0]["name"]).get_webhook() or {}).get("webhookUrl") or ""
    except Exception as error:
        return _result(
            "webhook", False,
            _("Nao foi possivel perguntar ao banco pelo webhook: {0}").format(error),
            _("Confira as credenciais e o certificado"),
        )
    if registered == expected:
        return _result("webhook", True)
    if not registered:
        problem = _("Nenhum webhook registrado no banco: as notificacoes nao chegam")
    else:
        problem = _("O webhook do banco aponta para {0}, e nao para este site").format(registered)
    return _result("webhook", False, problem, _("Use Register Webhook em Inter Company Account"))


CHECKS = (
    _integration_enabled,
    _accounts,
    _certificates,
    _statement_sync,
    _api_errors,
    _pix_limit,
    _jobs,
    _webhook,
)


# ---------------------------------------------------------------------------
# When it interrupts
# ---------------------------------------------------------------------------

def scheduled_check() -> None:
    """Daily. Say it when the state changes, and once a week while it stays wrong.

    Unlike its Telegram counterpart, this does NOT fall silent while the integration is off: that
    is precisely the state that lasted five months without a word.
    """
    previous = status().get("state") or ""
    verdict = check(record=True)
    if verdict["healthy"]:
        # Nothing to say about a channel that works - unless it is coming back from something,
        # which is worth exactly one message. A first run with nothing recorded is not a recovery.
        if previous and previous != "ok":
            _interrupt(verdict, recovered=True)
        return
    if verdict["state"] != previous or _reminder_is_due():
        _interrupt(verdict, recovered=False)


def _reminder_is_due() -> bool:
    last = _stored().get(ALERTED_FIELD)
    if not last:
        return True
    return now_datetime() - get_datetime(last) >= timedelta(days=REMINDER_DAYS)


def _interrupt(verdict: dict, recovered: bool) -> None:
    if recovered:
        subject = _("Banco Inter: comunicacao restabelecida")
        message = _("A comunicacao com o Banco Inter voltou ao normal.")
    else:
        subject = _("Banco Inter: problema na comunicacao")
        message = "\n".join(
            [_("A comunicacao com o Banco Inter tem problemas:"), ""]
            + [f"- {item['problem']}" + (f" ({item['fixable_by']})" if item["fixable_by"] else "")
               for item in verdict["problems"]]
        )
    _best_effort(alert_operator, subject, message)
    _best_effort(frappe.db.set_single_value, SETTINGS, ALERTED_FIELD, now_datetime())


# ---------------------------------------------------------------------------
# Reading and recording
# ---------------------------------------------------------------------------

def _is_enabled() -> bool:
    return bool(frappe.db.get_single_value(SETTINGS, "enabled"))


def _enabled_accounts() -> list:
    return frappe.get_all(
        ACCOUNT_DOCTYPE,
        filters={"sync_enabled": 1},
        fields=["name", "certificate_valid", "certificate_expiry", "last_statement_sync"],
    )


def _recent_calls(fields: list) -> list:
    return frappe.get_all(
        API_LOG,
        filters={"timestamp": [">=", now_datetime() - timedelta(hours=24)]},
        fields=fields,
        limit=2000,
    )


def _stored() -> dict:
    """Every recorded field at once, so a check does not pay a query per field."""
    values = {}
    for field in (STATE_FIELD, STATUS_FIELD, CHECKED_FIELD, SINCE_FIELD, ALERTED_FIELD):
        try:
            values[field] = frappe.db.get_single_value(SETTINGS, field)
        except Exception:
            values[field] = None
    return values


def _record(verdict: dict) -> None:
    """Leave the verdict on the settings form.

    ``checked_on`` moves on every run - it is the honest "when it was last known to work".
    ``since`` moves only when the state does, because the day counts are measured from it.
    """
    values = {
        STATE_FIELD: verdict["state"],
        STATUS_FIELD: verdict["summary"],
        CHECKED_FIELD: now_datetime(),
    }
    if verdict["state"] != _stored().get(STATE_FIELD):
        values[SINCE_FIELD] = now_datetime()
    for field, value in values.items():
        _best_effort(frappe.db.set_single_value, SETTINGS, field, value)


def _days_since(moment) -> int:
    if not moment:
        return 0
    return (now_datetime() - get_datetime(moment)).days


def _best_effort(action, *args) -> None:
    """A verdict that cannot be written must not turn a check into a failure."""
    try:
        action(*args)
    except Exception as error:
        try:
            frappe.log_error(title="Banking health", message=f"{type(error).__name__}: {error}")
        except Exception:
            pass


def _as_json(value) -> str:
    try:
        return json.dumps(value, default=str)[:2000]
    except Exception:
        return str(value)[:2000]
