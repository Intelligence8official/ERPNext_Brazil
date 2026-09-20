"""
Is Telegram still able to reach us?

A bot token does not expire, so there is no validity to renew or to show. What
does break is the webhook: the address Telegram delivers to was registered by
hand, outside this app, and a site that moves — or an address someone clears —
leaves the bot silent with nothing in the ERP to say why.

So this asks Telegram two questions (who is the bot, and how is delivery
going), tells apart what registering again can fix from what it cannot, and
writes down the verdict and its date. That last part is the honest version of
"until when is it valid": not an expiry, but when it was last known to work.

The one credential here that CAN be renewed is the webhook secret, which is
ours rather than Telegram's — and the order of that renewal matters: Telegram
has to accept the new secret before it is stored, or the two sides end up
holding different ones and every update is rejected.
"""

import secrets

import frappe
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}"
WEBHOOK_ENDPOINT = "/api/method/brazil_module.api.telegram_webhook"
TIMEOUT = 10

MANY_PENDING = 20
"""A queue this long means updates are arriving and not being consumed."""

STATUS_FIELD = "telegram_webhook_status"
CHECKED_FIELD = "telegram_webhook_checked_on"
SETTINGS = "I8 Agent Settings"


def webhook_url() -> str:
    """Where Telegram should be delivering, for this site."""
    return f"{frappe.utils.get_url().rstrip('/')}{WEBHOOK_ENDPOINT}"


def check(record: bool = True) -> dict:
    """Ask Telegram how things are. Never raises: this answers a button."""
    expected = webhook_url()
    try:
        me = _call("getMe")
        if not me.get("ok"):
            return _status(False, False, f"Telegram recusou o token: {me.get('description')}", url=expected, record=record)

        info = _call("getWebhookInfo")
        if not info.get("ok"):
            return _status(False, False, f"Telegram recusou a consulta: {info.get('description')}", url=expected, record=record)
    except Exception as e:
        return _status(False, False, f"Telegram inacessivel: {e}", url=expected, record=record)

    result = info.get("result") or {}
    registered = (result.get("url") or "").strip()
    pending = int(result.get("pending_update_count") or 0)
    bot = (me.get("result") or {}).get("username", "")

    if not registered:
        return _status(
            False, True, "Nenhum webhook registrado: o Telegram nao tem para onde entregar",
            bot=bot, url="", pending=pending, record=record,
        )

    if registered != expected:
        return _status(
            False, True, f"O webhook aponta para {registered}, e nao para este site",
            bot=bot, url=registered, pending=pending, record=record,
        )

    # From here the address is right, so registering again fixes nothing.
    last_error = result.get("last_error_message")
    if last_error:
        return _status(
            False, False, f"O Telegram nao conseguiu entregar: {last_error}",
            bot=bot, url=registered, pending=pending,
            last_error_at=_as_datetime(result.get("last_error_date")), record=record,
        )

    if pending > MANY_PENDING:
        return _status(
            False, False, f"{pending} mensagens paradas na fila do Telegram",
            bot=bot, url=registered, pending=pending, record=record,
        )

    return _status(True, False, "", bot=bot, url=registered, pending=pending, record=record)


def register_webhook(rotate_secret: bool = False) -> dict:
    """Point Telegram at this site.

    With `rotate_secret`, the secret is renewed — Telegram first, storage
    second. If it cannot be stored after Telegram took it, the old one is put
    back there: a secret only this side knows is the same as no bot at all.
    """
    current = _secret()
    new_secret = secrets.token_urlsafe(32) if rotate_secret else current

    try:
        answer = _call("setWebhook", url=webhook_url(), secret_token=new_secret)
    except Exception as e:
        return {"ok": False, "message": f"Telegram inacessivel: {e}"}

    if not answer.get("ok"):
        # Nothing changed on either side.
        return {"ok": False, "message": answer.get("description") or "O Telegram recusou o registro"}

    if rotate_secret:
        try:
            frappe.db.set_single_value(SETTINGS, "telegram_webhook_secret", new_secret)
        except Exception as e:
            _restore(current)
            return {"ok": False, "message": f"O segredo novo nao pode ser gravado ({e}); o anterior foi mantido"}

    return {"ok": True, "message": "Webhook registrado", "url": webhook_url(), **check()}


def scheduled_check() -> None:
    """Daily: fix what can be fixed, say what cannot.

    Nothing to watch while the agent is off — a message that arrived would
    go nowhere anyway, and a nightly warning about it is pure noise.
    """
    if not frappe.db.get_single_value(SETTINGS, "enabled"):
        return

    status = check()
    if status["healthy"]:
        return

    if not status["fixable"]:
        _warn(f"Telegram: {status['problem']}")
        return

    # Same secret: the address was the problem, and a silent rotation in the
    # middle of the night is one more thing that can go wrong.
    result = register_webhook(rotate_secret=False)
    if result.get("ok"):
        _warn(f"Telegram: {status['problem']}. Ja registrei o webhook de novo e o bot voltou a responder.")
    else:
        _warn(f"Telegram: {status['problem']}. Tentei registrar de novo e nao consegui: {result.get('message')}")


def _status(healthy: bool, fixable: bool, problem: str, *, bot="", url="", pending=0, last_error_at=None, record=True) -> dict:
    status = {
        "healthy": healthy,
        "fixable": fixable,
        "problem": problem,
        "bot": bot,
        "url": url,
        "pending": pending,
        "last_error_at": last_error_at,
    }
    if record:
        _record(status)
    return status


def _record(status: dict) -> None:
    """Leave the verdict on the settings form. Best effort: a status that
    cannot be written must not turn a check into a failure."""
    try:
        frappe.db.set_single_value(SETTINGS, STATUS_FIELD, _summary(status))
        frappe.db.set_single_value(SETTINGS, CHECKED_FIELD, frappe.utils.now_datetime())
    except Exception:
        pass


def _summary(status: dict) -> str:
    if status["healthy"]:
        bot = f"@{status['bot']}" if status["bot"] else "bot"
        return f"OK — {bot} entregando em {status['url']}"
    return status["problem"]


def _call(method: str, **payload) -> dict:
    response = requests.post(
        f"{TELEGRAM_API.format(token=_token())}/{method}", json=payload, timeout=TIMEOUT
    )
    return response.json()


def _restore(secret: str) -> None:
    try:
        _call("setWebhook", url=webhook_url(), secret_token=secret)
    except Exception as e:
        frappe.log_error(str(e), "I8 Telegram Webhook Rollback Error")


def _token() -> str:
    from brazil_module.intelligence8.doctype.i8_agent_settings.i8_agent_settings import I8AgentSettings

    return I8AgentSettings.get_telegram_token()


def _secret() -> str:
    try:
        return frappe.get_single(SETTINGS).get_password("telegram_webhook_secret") or ""
    except Exception:
        return ""


def _warn(message: str) -> None:
    """Say it on Telegram if Telegram still works, and in the desk either way."""
    try:
        _tell_operator(message)
    except Exception as e:
        frappe.log_error(str(e), "I8 Telegram Health Alert Error")

    try:
        from brazil_module.services.intelligence.notifications import notify_desk

        notify_desk(title="I8 Telegram", message=message)
    except Exception:
        pass

    frappe.log_error(message, "I8 Telegram Health")


def _tell_operator(message: str) -> None:
    from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot

    chat_id = frappe.db.get_single_value(SETTINGS, "telegram_chat_id")
    if chat_id:
        TelegramBot().send_message(chat_id, message)


def _as_datetime(epoch):
    if not epoch:
        return None
    from datetime import datetime

    return datetime.fromtimestamp(int(epoch))
