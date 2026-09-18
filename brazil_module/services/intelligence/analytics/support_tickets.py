"""
Support Tickets — platform Issues (i8talent, DMARC Report, iz4world, ...).

Every product of the house opens its support tickets as an ERPNext `Issue`
on THIS instance: there is no ticket table on their side. `custom_product`
(a Customize Form field) says which product a ticket came from, and
`issue_type` splits a problem ("Support Ticket") from an idea
("Feature Request").

Why "who spoke last" and not the status: a reply written as a Comment does
NOT move the Issue out of "Open" (only an outgoing e-mail does). Replies here
are written as comments, so the status alone would report an answered ticket
as still waiting. The platforms write the customer's message as a Comment
whose `comment_email` is the e-mail that opened the ticket (`raised_by`), so
the author of the last message is the reliable signal.
"""

from datetime import datetime, timedelta

import frappe
from frappe.utils import now_datetime

WAITING_ON_US = "us"
"""The customer spoke last: the ticket waits on us."""

WAITING_ON_CUSTOMER = "customer"
"""We spoke last: the ticket waits on the customer."""

PAUSED = "hold"
"""On Hold: counted, but in neither queue."""

SECTION_TITLE = "*Chamados da Plataforma:*"
NO_PRODUCT = "Sem produto"
FEATURE_REQUEST = "Feature Request"
"""The other half of the counter: an idea, not a problem. Counted apart."""

MAX_LISTED = 5
"""Tickets detailed per product. The rest is summed up in one line."""

MAX_SUBJECT = 60
ONE_DAY = timedelta(hours=24)
ONE_WEEK = timedelta(days=7)

WINDOW_DAYS = 7
CLOSED_STATUSES = ["Resolved", "Closed"]
PRODUCT_FIELD = "custom_product"
ISSUE_FIELDS = ["name", "subject", "status", "issue_type", "raised_by", "customer", "creation"]
MAX_ROWS = 500


def support_tickets_section(is_monday: bool = False, now: datetime | None = None) -> str:
    """The briefing section: read the tickets, then render them.

    The clock is Frappe's, not the process's: `creation` is written in the
    site's timezone, and the container's may be another one.

    A failure is reported IN the section instead of raised. build_briefing()
    drops a section that raises, and a section that quietly disappears from
    the Telegram message is the failure nobody ever notices.
    """
    try:
        moment = now or now_datetime()
        return format_section(collect_snapshot(moment), now=moment, is_monday=is_monday)
    except Exception as e:
        failed = f"{SECTION_TITLE}\n  Nao foi possivel ler os chamados (ver Error Log)"
        try:
            frappe.log_error(str(e), "I8 Briefing Support Tickets Error")
        except Exception:
            # Writing the Error Log is itself a database write, and this guard
            # fires on database trouble: the section still has to come back.
            pass
        return failed


def collect_snapshot(now: datetime, days: int = WINDOW_DAYS) -> dict:
    """Read everything the section needs: a few queries, none per ticket."""
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    fields = _issue_fields()

    open_issues = frappe.get_all(
        "Issue",
        filters={"status": ["not in", CLOSED_STATUSES]},
        fields=fields,
        order_by="creation asc",
        limit_page_length=MAX_ROWS,
    )
    recent = frappe.get_all(
        "Issue",
        filters={"creation": [">=", since]},
        fields=fields,
        order_by="creation desc",
        limit_page_length=MAX_ROWS,
    )
    # `modified` and not a resolution date: ERPNext v15 has no `resolution_date`
    # on Issue (it is `sla_resolution_date`), and that one is only written when
    # the site tracks a Service Level Agreement — which this one does not, so it
    # would count zero resolved tickets forever.
    resolved = frappe.get_all(
        "Issue",
        filters={"status": ["in", CLOSED_STATUSES], "modified": [">=", since]},
        fields=fields + ["modified"],
        order_by="modified desc",
        limit_page_length=MAX_ROWS,
    )

    return {
        "open": open_issues,
        "recent": recent,
        "resolved": [{**issue, "resolved_at": issue.get("modified")} for issue in resolved],
        "last_messages": _last_messages(
            {issue.get("name"): issue.get("raised_by") for issue in open_issues}
        ),
    }


def _issue_fields() -> list[str]:
    """`custom_product` is a Customize Form field: asking a plain ERPNext for
    it would fail the whole read, so it is only asked for when it exists."""
    fields = list(ISSUE_FIELDS)
    if _has_product_field():
        fields.append(PRODUCT_FIELD)
    return fields


def _has_product_field() -> bool:
    try:
        return bool(frappe.get_meta("Issue").has_field(PRODUCT_FIELD))
    except Exception:
        return False


def _last_messages(requesters: dict[str, str | None]) -> dict[str, dict]:
    """The last message of each ticket — comment or e-mail, whichever is newer.

    Uncapped on purpose: a capped page would leave a chatty ticket with no
    last message, and the status fallback would then date it from its own
    creation — a false "waiting the longest" headline at the top of the
    section, which is the line the briefing is told to highlight.
    """
    names = [name for name in requesters if name]
    if not names:
        return {}

    ours = _support_authors()

    def from_support(issue_name: str, author: str | None) -> bool:
        """Ours when an ERP user wrote it — unless that user is the one who
        opened the ticket. You use your own products, and a ticket you raised
        would otherwise be filed as answered by you and never show up again."""
        who = _normalize_email(author)
        return who in ours and who != _normalize_email(requesters.get(issue_name))
    latest: dict[str, dict] = {}
    reference = {"reference_doctype": "Issue", "reference_name": ["in", names]}

    for row in frappe.get_all(
        "Comment",
        filters={**reference, "comment_type": "Comment"},
        fields=["reference_name", "comment_email", "creation"],
        order_by="creation desc",
        limit_page_length=0,
    ):
        # `comment_email` is who the comment is FROM: the platforms write the
        # customer's comment under the customer's own e-mail.
        author = row.get("comment_email")
        issue_name = row.get("reference_name")
        _keep_latest(
            latest,
            issue_name,
            {
                "author": author,
                "at": _to_datetime(row.get("creation")),
                "from_support": from_support(issue_name, author),
            },
        )

    for row in frappe.get_all(
        "Communication",
        filters={**reference, "communication_type": "Communication"},
        fields=["reference_name", "sender", "sent_or_received", "creation"],
        order_by="creation desc",
        limit_page_length=0,
    ):
        sender = row.get("sender")
        issue_name = row.get("reference_name")
        _keep_latest(
            latest,
            issue_name,
            {
                "author": sender,
                "at": _to_datetime(row.get("creation")),
                # An e-mail that LEFT the ERP is ours whatever mailbox signed it.
                "from_support": row.get("sent_or_received") == "Sent"
                or from_support(issue_name, sender),
            },
        )

    return latest


def _support_authors() -> frozenset[str]:
    """Who writes from OUR side of the counter: the ERP's System Users.

    Comparing the author against the ticket's `raised_by` is not enough. When
    `raised_by` comes in empty ERPNext fills it with the session user, and a
    Frappe username IS an e-mail address — so a service account that opens
    tickets by API looks exactly like a customer, and every reply the customer
    wrote afterwards would be filed as ours, hiding the ticket from the queue.
    Customers are not users of this ERP; whoever answers in the desk is.

    An unreadable list means nobody is recognized as ours, which leaves the
    tickets visible — the side that costs a glance, not a customer waiting.
    """
    try:
        names = frappe.get_all("User", filters={"user_type": "System User"}, pluck="name")
    except Exception as e:
        # Every ticket then reads as waiting on you — the safe direction, but
        # not one to take in silence.
        frappe.log_error(str(e), "I8 Briefing Support Tickets Error")
        return frozenset()
    return frozenset(_normalize_email(name) for name in names if name)


def _keep_latest(latest: dict, issue_name: str | None, message: dict) -> None:
    if not issue_name or message["at"] is None:
        return
    current = latest.get(issue_name)
    if current is None or message["at"] > current["at"]:
        latest[issue_name] = message


def format_section(snapshot: dict, now: datetime, is_monday: bool = False) -> str:
    """Render the briefing section from an already-read snapshot."""
    lines = [SECTION_TITLE]

    products = _product_stats(snapshot, now)
    for product, stats in sorted(products.items(), key=lambda item: (-len(item[1]["waiting"]), item[0])):
        week = _week_line(stats) if is_monday else ""
        summary = _product_line(stats, now)
        if not summary:
            if not week:
                continue
            summary = "nada aguardando voce"
        lines.append(f"  {product}: {summary}")
        lines.extend(_waiting_detail(stats["waiting"], now))
        if week:
            lines.append(week)

    suggestions = _suggestions_line(snapshot, now)
    if suggestions:
        lines.append(suggestions)

    if len(lines) == 1:
        lines.append("  Nenhum chamado pendente")
    return "\n".join(lines)


def _product_stats(snapshot: dict, now: datetime) -> dict[str, dict]:
    """Per product: what waits on us, what waits on them, what moved in 24h."""
    last_messages = snapshot.get("last_messages") or {}
    stats: dict[str, dict] = {}
    since_24h = now - ONE_DAY

    def bucket(issue: dict) -> dict:
        return stats.setdefault(
            product_label(issue),
            {
                "waiting": [],
                "customer": 0,
                "paused": 0,
                "new_24h": 0,
                "resolved_24h": 0,
                "week_new": 0,
                "week_resolved": 0,
                "stale_customer": 0,
            },
        )

    for issue in _tickets(snapshot.get("open")):
        state, since = classify_issue(issue, last_messages.get(issue.get("name")))
        entry = bucket(issue)
        if state == WAITING_ON_US:
            entry["waiting"].append((issue, since))
        elif state == WAITING_ON_CUSTOMER:
            entry["customer"] += 1
            # Answered and never picked up again. A comment does not close a
            # ticket, so after a week these are the ones to close by hand.
            if since is not None and since < now - ONE_WEEK:
                entry["stale_customer"] += 1
        else:
            entry["paused"] += 1

    for issue in _tickets(snapshot.get("recent")):
        entry = bucket(issue)
        entry["week_new"] += 1
        if _is_after(issue.get("creation"), since_24h):
            entry["new_24h"] += 1

    for issue in _tickets(snapshot.get("resolved")):
        entry = bucket(issue)
        entry["week_resolved"] += 1
        if _is_after(issue.get("resolved_at"), since_24h):
            entry["resolved_24h"] += 1

    for entry in stats.values():
        entry["waiting"].sort(key=lambda item: item[1] or datetime.max)
    return stats


def _product_line(stats: dict, now: datetime) -> str:
    """The one-line summary of a product, or "" when it has nothing to say."""
    parts = []
    if stats["waiting"]:
        parts.append(_waiting_summary(stats["waiting"], now))
    if stats["customer"]:
        parts.append(f"{stats['customer']} aguardando cliente")
    if stats["paused"]:
        parts.append(f"{stats['paused']} {_plural(stats['paused'], 'pausado')}")
    if stats["new_24h"]:
        parts.append(f"{stats['new_24h']} {_plural(stats['new_24h'], 'novo')} em 24h")
    if stats["resolved_24h"]:
        parts.append(f"{stats['resolved_24h']} {_plural(stats['resolved_24h'], 'resolvido')} em 24h")

    if not parts:
        return ""
    if not stats["waiting"]:
        parts.insert(0, "nada aguardando voce")
    return ", ".join(parts)


def _week_line(stats: dict) -> str:
    """Monday only: how the week went, and what nobody came back to."""
    parts = []
    if stats["week_new"]:
        parts.append(f"{stats['week_new']} {_plural(stats['week_new'], 'aberto')}")
    if stats["week_resolved"]:
        parts.append(f"{stats['week_resolved']} {_plural(stats['week_resolved'], 'resolvido')}")
    if stats["stale_customer"]:
        parts.append(
            f"{stats['stale_customer']} aguardando cliente ha mais de 7 dias (candidatos a encerrar)"
        )
    return f"    Semana: {', '.join(parts)}" if parts else ""


def _suggestions_line(snapshot: dict, now: datetime) -> str:
    """Feature Requests are product backlog, not a queue: one line for all."""
    open_count = len([i for i in snapshot.get("open") or [] if _is_suggestion(i)])
    new_24h = len(
        [
            i
            for i in snapshot.get("recent") or []
            if _is_suggestion(i) and _is_after(i.get("creation"), now - ONE_DAY)
        ]
    )

    parts = []
    if new_24h:
        parts.append(f"{new_24h} {_plural(new_24h, 'nova', 'novas')} em 24h")
    if open_count:
        parts.append(f"{open_count} em aberto")
    return f"  Sugestoes: {', '.join(parts)}" if parts else ""


def _tickets(issues) -> list[dict]:
    return [issue for issue in issues or [] if not _is_suggestion(issue)]


def _is_suggestion(issue: dict) -> bool:
    return issue.get("issue_type") == FEATURE_REQUEST


def product_label(issue: dict) -> str:
    """Which product a ticket came from.

    `custom_product` is the label the platforms write. An instance without
    that Customize Form field — or a product that does not write it, like
    iz4world — still has the Customer to go by.
    """
    return (issue.get("custom_product") or issue.get("customer") or NO_PRODUCT).strip() or NO_PRODUCT


def _waiting_summary(waiting: list, now: datetime) -> str:
    oldest = _age_label(waiting[0][1], now)
    if len(waiting) == 1:
        return f"1 aguardando voce ({oldest})"
    return f"{len(waiting)} aguardando voce (mais antigo {oldest})"


def _waiting_detail(waiting: list, now: datetime) -> list[str]:
    lines = [
        f"    - {issue.get('name')}: {_subject(issue)} ({_age_label(since, now)})"
        for issue, since in waiting[:MAX_LISTED]
    ]
    if len(waiting) > MAX_LISTED:
        lines.append(f"    ... e mais {len(waiting) - MAX_LISTED}")
    return lines


def _subject(issue: dict) -> str:
    """One line, short enough to read on a phone. The text is the customer's."""
    subject = " ".join((issue.get("subject") or "").split())
    if len(subject) > MAX_SUBJECT:
        return subject[: MAX_SUBJECT - 3] + "..."
    return subject


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _is_after(value, moment: datetime) -> bool:
    parsed = _to_datetime(value)
    return parsed is not None and parsed >= moment


def _age_label(since: datetime | None, now: datetime) -> str:
    if since is None:
        return "sem data"
    delta = now - since
    if delta.total_seconds() < 0:
        return "agora"
    if delta.days >= 1:
        return f"ha {delta.days} dia{'s' if delta.days > 1 else ''}"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"ha {hours}h"
    return "ha menos de 1h"


def classify_issue(issue: dict, last_message: dict | None) -> tuple[str, datetime | None]:
    """Return (state, waiting_since) for one Issue.

    `state` is WAITING_ON_US, WAITING_ON_CUSTOMER or PAUSED. `waiting_since`
    is when the current wait started — the moment the last message was
    written, or the ticket's own creation when nobody has written since.
    A paused ticket has no wait to measure.
    """
    if issue.get("status") == "On Hold":
        return PAUSED, None

    opened_at = _to_datetime(issue.get("creation"))

    if last_message is None:
        # Never written to: "Open" still waits on us, and anything else (the
        # i8talent chat closes as "Replied") was answered when it was opened.
        state = WAITING_ON_US if issue.get("status") == "Open" else WAITING_ON_CUSTOMER
        return state, opened_at

    written_at = _to_datetime(last_message.get("at")) or opened_at
    if last_message.get("from_support"):
        return WAITING_ON_CUSTOMER, written_at

    return WAITING_ON_US, written_at


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _to_datetime(value) -> datetime | None:
    """Frappe hands back datetimes, but a raw SQL read can hand back strings."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None
