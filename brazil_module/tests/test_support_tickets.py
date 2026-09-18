import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import frappe

import brazil_module.services.intelligence.analytics.support_tickets as support_tickets
from brazil_module.services.intelligence.analytics.support_tickets import (
    classify_issue,
    collect_snapshot,
    format_section,
    support_tickets_section,
)

NOW = datetime(2026, 9, 17, 8, 0, 0)


def _issue(**overrides) -> dict:
    issue = {
        "name": "ISS-2026-00042",
        "subject": "Nao consigo publicar vaga",
        "status": "Open",
        "issue_type": "Support Ticket",
        "raised_by": "cliente@empresa.com",
        "customer": "i8talent Users",
        "custom_product": "i8talent",
        "creation": datetime(2026, 9, 13, 10, 0, 0),
    }
    issue.update(overrides)
    return issue


def _message(**overrides) -> dict:
    message = {
        "author": "cliente@empresa.com",
        "at": datetime(2026, 9, 15, 14, 0, 0),
        "from_support": False,
    }
    message.update(overrides)
    return message


class TestClassifyIssue(unittest.TestCase):
    """Who spoke last decides whether a ticket is waiting on us."""

    def test_comment_from_requester_waits_on_us_since_that_comment(self):
        state, since = classify_issue(_issue(), _message())
        self.assertEqual(state, "us")
        self.assertEqual(since, datetime(2026, 9, 15, 14, 0, 0))

    def test_message_from_our_side_waits_on_customer_since_that_message(self):
        state, since = classify_issue(_issue(), _message(from_support=True))
        self.assertEqual(state, "customer")
        self.assertEqual(since, datetime(2026, 9, 15, 14, 0, 0))

    def test_open_without_messages_waits_on_us_since_creation(self):
        state, since = classify_issue(_issue(), None)
        self.assertEqual(state, "us")
        self.assertEqual(since, datetime(2026, 9, 13, 10, 0, 0))

    def test_replied_without_messages_waits_on_customer_since_creation(self):
        # The i8talent chat closes as "Replied": it was answered already, and
        # the answer happened when the ticket was written.
        state, since = classify_issue(_issue(status="Replied"), None)
        self.assertEqual(state, "customer")
        self.assertEqual(since, datetime(2026, 9, 13, 10, 0, 0))

    def test_on_hold_is_neither_queue(self):
        state, since = classify_issue(_issue(status="On Hold"), _message())
        self.assertEqual(state, "hold")
        self.assertIsNone(since)

    def test_creation_as_string_is_parsed(self):
        issue = _issue(creation="2026-09-13 10:00:00.123456")
        state, since = classify_issue(issue, None)
        self.assertEqual(state, "us")
        self.assertEqual(since, datetime(2026, 9, 13, 10, 0, 0, 123456))


def _snapshot(**overrides) -> dict:
    snapshot = {"open": [], "last_messages": {}, "recent": [], "resolved": []}
    snapshot.update(overrides)
    return snapshot


class TestFormatSection(unittest.TestCase):
    def test_groups_by_product_with_oldest_wait(self):
        snapshot = _snapshot(
            open=[
                _issue(creation=datetime(2026, 9, 13, 8, 0, 0)),
                _issue(
                    name="ISS-2026-00045",
                    subject="Erro ao exportar candidatos",
                    creation=datetime(2026, 9, 10, 8, 0, 0),
                ),
            ],
            last_messages={"ISS-2026-00045": _message(at=datetime(2026, 9, 16, 8, 0, 0))},
        )

        text = format_section(snapshot, now=NOW)

        self.assertIn("*Chamados da Plataforma:*", text)
        self.assertIn("  i8talent: 2 aguardando voce (mais antigo ha 4 dias)", text)
        self.assertIn("    - ISS-2026-00042: Nao consigo publicar vaga (ha 4 dias)", text)
        self.assertIn("    - ISS-2026-00045: Erro ao exportar candidatos (ha 1 dia)", text)

    def test_oldest_ticket_comes_first(self):
        snapshot = _snapshot(
            open=[
                _issue(name="ISS-RECENTE", creation=datetime(2026, 9, 16, 8, 0, 0)),
                _issue(name="ISS-ANTIGO", creation=datetime(2026, 9, 10, 8, 0, 0)),
            ],
        )

        lines = format_section(snapshot, now=NOW).splitlines()
        detail = [line for line in lines if line.startswith("    - ")]

        self.assertEqual(len(detail), 2)
        self.assertIn("ISS-ANTIGO", detail[0])
        self.assertIn("ISS-RECENTE", detail[1])

    def test_single_ticket_line_shows_its_own_age(self):
        snapshot = _snapshot(open=[_issue(creation=datetime(2026, 9, 16, 8, 0, 0))])

        self.assertIn("  i8talent: 1 aguardando voce (ha 1 dia)", format_section(snapshot, now=NOW))

    def test_falls_back_to_customer_when_product_is_missing(self):
        # iz4world writes no `custom_product`: the Customer is what is left.
        issue = _issue(custom_product=None, customer="iz4World")

        self.assertIn("  iz4World: 1 aguardando voce", format_section(_snapshot(open=[issue]), now=NOW))

    def test_ticket_without_product_and_without_customer_is_still_reported(self):
        issue = _issue(custom_product=None, customer=None)

        self.assertIn("  Sem produto: 1 aguardando voce", format_section(_snapshot(open=[issue]), now=NOW))

    def test_counts_tickets_waiting_on_the_customer_and_paused_ones(self):
        snapshot = _snapshot(
            open=[
                _issue(name="ISS-1", creation=datetime(2026, 9, 16, 8, 0, 0)),
                _issue(name="ISS-2"),
                _issue(name="ISS-3"),
                _issue(name="ISS-4", status="On Hold"),
            ],
            last_messages={
                "ISS-2": _message(from_support=True),
                "ISS-3": _message(from_support=True),
            },
        )

        text = format_section(snapshot, now=NOW)

        self.assertIn("  i8talent: 1 aguardando voce (ha 1 dia), 2 aguardando cliente, 1 pausado", text)

    def test_product_with_no_ticket_waiting_on_us_says_so(self):
        snapshot = _snapshot(
            resolved=[
                _issue(
                    name="ISS-9",
                    custom_product="DMARC Report",
                    status="Resolved",
                    resolved_at=datetime(2026, 9, 17, 2, 0, 0),
                )
            ]
        )

        self.assertIn("  DMARC Report: nada aguardando voce, 1 resolvido em 24h", format_section(snapshot, now=NOW))

    def test_counts_only_tickets_opened_in_the_last_24h_as_new(self):
        snapshot = _snapshot(
            recent=[
                _issue(name="ISS-1", creation=datetime(2026, 9, 17, 7, 0, 0)),
                _issue(name="ISS-2", creation=datetime(2026, 9, 16, 20, 0, 0)),
                _issue(name="ISS-3", creation=datetime(2026, 9, 12, 8, 0, 0)),
            ]
        )

        self.assertIn("  i8talent: nada aguardando voce, 2 novos em 24h", format_section(snapshot, now=NOW))

    def test_lists_at_most_five_tickets_and_sums_up_the_rest(self):
        snapshot = _snapshot(
            open=[_issue(name=f"ISS-{n}", creation=datetime(2026, 9, 10 + n % 5, 8, 0, 0)) for n in range(7)]
        )

        lines = format_section(snapshot, now=NOW).splitlines()

        self.assertEqual(len([line for line in lines if line.startswith("    - ")]), 5)
        self.assertIn("    ... e mais 2", lines)

    def test_long_subject_is_cut_and_kept_on_one_line(self):
        issue = _issue(subject="Erro grave\nno login   do painel " + "x" * 80)

        detail = [line for line in format_section(_snapshot(open=[issue]), now=NOW).splitlines() if " - " in line][0]

        self.assertNotIn("\n", detail)
        self.assertIn("Erro grave no login do painel", detail)
        self.assertLess(len(detail), 100)

    def test_feature_requests_are_counted_apart_as_suggestions(self):
        suggestion = _issue(name="ISS-S1", issue_type="Feature Request", subject="Exportar em PDF")
        snapshot = _snapshot(
            open=[suggestion, _issue(name="ISS-S2", issue_type="Feature Request")],
            recent=[_issue(name="ISS-S3", issue_type="Feature Request", creation=datetime(2026, 9, 17, 7, 0, 0))],
        )

        text = format_section(snapshot, now=NOW)

        self.assertIn("  Sugestoes: 1 nova em 24h, 2 em aberto", text)
        self.assertNotIn("aguardando voce", text)
        self.assertNotIn("Exportar em PDF", text)

    def test_no_suggestions_line_without_feature_requests(self):
        self.assertNotIn("Sugestoes", format_section(_snapshot(open=[_issue()]), now=NOW))

    def test_monday_adds_the_week_balance_per_product(self):
        snapshot = _snapshot(
            recent=[_issue(name=f"ISS-N{n}", creation=datetime(2026, 9, 12, 8, 0, 0)) for n in range(5)],
            resolved=[
                _issue(name=f"ISS-R{n}", status="Resolved", resolved_at=datetime(2026, 9, 12, 8, 0, 0))
                for n in range(3)
            ],
        )

        self.assertIn("    Semana: 5 abertos, 3 resolvidos", format_section(snapshot, now=NOW, is_monday=True))

    def test_week_balance_stays_out_on_other_days(self):
        snapshot = _snapshot(recent=[_issue(creation=datetime(2026, 9, 12, 8, 0, 0))])

        self.assertNotIn("Semana:", format_section(snapshot, now=NOW))

    def test_monday_points_out_tickets_the_customer_left_hanging(self):
        # We answered and nobody came back: a comment never closes a ticket
        # by itself, so these are the ones to close by hand.
        snapshot = _snapshot(
            open=[_issue(name="ISS-1"), _issue(name="ISS-2")],
            last_messages={
                "ISS-1": _message(from_support=True, at=datetime(2026, 9, 1, 8, 0, 0)),
                "ISS-2": _message(from_support=True, at=datetime(2026, 9, 16, 8, 0, 0)),
            },
        )

        text = format_section(snapshot, now=NOW, is_monday=True)

        self.assertIn("1 aguardando cliente ha mais de 7 dias (candidatos a encerrar)", text)

    def test_a_ticket_dated_in_the_future_does_not_read_as_a_day_old(self):
        # Clock skew between the container and the site: a negative age used
        # to come out as "ha 23h".
        snapshot = _snapshot(open=[_issue(creation=datetime(2026, 9, 17, 8, 1, 0))])

        self.assertIn("  i8talent: 1 aguardando voce (agora)", format_section(snapshot, now=NOW))

    def test_says_nothing_is_pending_when_there_are_no_tickets(self):
        text = format_section(_snapshot(), now=NOW)

        self.assertIn("*Chamados da Plataforma:*", text)
        self.assertIn("Nenhum chamado pendente", text)


class TestCollectSnapshot(unittest.TestCase):
    """The read layer: which queries go out, and how messages are folded."""

    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.side_effect = self._get_all
        frappe.get_meta.side_effect = None
        frappe.get_meta.return_value.has_field.return_value = True
        # A side_effect outlives reset_mock(), and the frappe mock is shared by
        # every test file: leaving this one behind breaks whoever runs next.
        self.addCleanup(setattr, frappe.get_all, "side_effect", None)
        self.calls = []
        self.system_users = ["abel@intelligence8.com"]
        self.open_issues = []
        self.recent_issues = []
        self.resolved_issues = []
        self.comments = []
        self.communications = []

    def _get_all(self, doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **kwargs):
        filters = filters or {}
        self.calls.append(
            {
                "doctype": doctype,
                "filters": filters,
                "fields": fields or [],
                "order_by": order_by,
                "limit_page_length": limit_page_length,
            }
        )
        if doctype == "User":
            return list(self.system_users)
        if doctype == "Comment":
            return self.comments
        if doctype == "Communication":
            return self.communications
        if "modified" in filters:
            return self.resolved_issues
        if "creation" in filters:
            return self.recent_issues
        return self.open_issues

    def _issue_calls(self) -> list[dict]:
        return [call for call in self.calls if call["doctype"] == "Issue"]

    def test_reads_open_recent_and_resolved_issues(self):
        collect_snapshot(NOW)

        calls = self._issue_calls()
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["filters"]["status"], ["not in", ["Resolved", "Closed"]])
        self.assertEqual(calls[1]["filters"]["creation"], [">=", "2026-09-10 08:00:00"])
        self.assertEqual(calls[2]["filters"]["status"], ["in", ["Resolved", "Closed"]])
        # v15 has no `resolution_date` on Issue (it is `sla_resolution_date`, and
        # only the SLA machinery fills it): asking for it breaks the whole read.
        self.assertEqual(calls[2]["filters"]["modified"], [">=", "2026-09-10 08:00:00"])
        self.assertNotIn("resolution_date", calls[2]["fields"])

    def test_asks_for_the_product_field_when_the_instance_has_it(self):
        collect_snapshot(NOW)

        self.assertTrue(all("custom_product" in call["fields"] for call in self._issue_calls()))

    def test_leaves_the_product_field_out_when_the_instance_lacks_it(self):
        # A plain ERPNext has no `custom_product`: asking for it would fail.
        frappe.get_meta.return_value.has_field.return_value = False

        collect_snapshot(NOW)

        self.assertTrue(all("custom_product" not in call["fields"] for call in self._issue_calls()))

    def test_keeps_the_latest_message_of_each_issue(self):
        self.open_issues = [_issue(name="ISS-1")]
        self.comments = [
            {"reference_name": "ISS-1", "comment_email": "cliente@empresa.com", "creation": datetime(2026, 9, 14, 9, 0)},
        ]
        self.communications = [
            {
                "reference_name": "ISS-1",
                "sender": "suporte@intelligence8.com",
                "sent_or_received": "Sent",
                "creation": datetime(2026, 9, 15, 9, 0),
            },
        ]

        snapshot = collect_snapshot(NOW)

        self.assertEqual(
            snapshot["last_messages"]["ISS-1"],
            {"author": "suporte@intelligence8.com", "at": datetime(2026, 9, 15, 9, 0), "from_support": True},
        )

    def test_a_comment_written_by_an_erp_user_is_ours(self):
        # Answering in the desk writes a Comment signed by whoever wrote it,
        # and everyone who can open the desk is a System User.
        self.system_users = ["abel@intelligence8.com"]
        self.open_issues = [_issue(name="ISS-1")]
        self.comments = [
            {"reference_name": "ISS-1", "comment_email": "Abel@Intelligence8.com", "creation": datetime(2026, 9, 14, 9, 0)},
        ]

        self.assertTrue(collect_snapshot(NOW)["last_messages"]["ISS-1"]["from_support"])

    def test_a_comment_from_outside_the_erp_is_the_customers(self):
        # The service account that opens tickets by API is a System User and
        # can end up in `raised_by`; what decides is WHO SIGNED the comment.
        self.system_users = ["abel@intelligence8.com", "api@intelligence8.com"]
        self.open_issues = [_issue(name="ISS-1", raised_by="api@intelligence8.com")]
        self.comments = [
            {"reference_name": "ISS-1", "comment_email": "cliente@empresa.com", "creation": datetime(2026, 9, 14, 9, 0)},
        ]

        self.assertEqual(
            collect_snapshot(NOW)["last_messages"]["ISS-1"],
            {"author": "cliente@empresa.com", "at": datetime(2026, 9, 14, 9, 0), "from_support": False},
        )

    def test_a_comment_from_the_person_who_opened_it_is_never_ours(self):
        # You use your own products: the requester can be an ERP user too.
        # Without this the ticket you opened would be filed as answered by
        # you and would never show up in the queue again.
        self.system_users = ["abel@intelligence8.com"]
        self.open_issues = [_issue(name="ISS-1", raised_by="abel@intelligence8.com")]
        self.comments = [
            {"reference_name": "ISS-1", "comment_email": "abel@intelligence8.com", "creation": datetime(2026, 9, 14, 9, 0)},
        ]

        self.assertFalse(collect_snapshot(NOW)["last_messages"]["ISS-1"]["from_support"])

    def test_an_unreadable_user_list_is_logged(self):
        # Nobody recognized as ours means every ticket reads as waiting on
        # you. That is the safe direction, but it cannot happen in silence.
        self.open_issues = [_issue(name="ISS-1")]
        self.comments = [
            {"reference_name": "ISS-1", "comment_email": "abel@intelligence8.com", "creation": datetime(2026, 9, 14, 9, 0)},
        ]
        original = self._get_all

        def failing(doctype, **kwargs):
            if doctype == "User":
                raise Exception("db down")
            return original(doctype, **kwargs)

        frappe.get_all.side_effect = failing

        snapshot = collect_snapshot(NOW)

        self.assertFalse(snapshot["last_messages"]["ISS-1"]["from_support"])
        self.assertTrue(frappe.log_error.called)

    def test_a_received_email_is_not_ours(self):
        self.open_issues = [_issue(name="ISS-1")]
        self.communications = [
            {
                "reference_name": "ISS-1",
                "sender": "cliente@empresa.com",
                "sent_or_received": "Received",
                "creation": datetime(2026, 9, 14, 9, 0),
            },
        ]

        self.assertFalse(collect_snapshot(NOW)["last_messages"]["ISS-1"]["from_support"])

    def test_reads_every_message_instead_of_one_capped_page(self):
        # A capped page leaves a chatty ticket with no last message, and the
        # status fallback then dates it from its creation — a false "oldest
        # waiting" headline, which is exactly what the briefing highlights.
        self.open_issues = [_issue(name="ISS-1")]

        collect_snapshot(NOW)

        message_calls = [call for call in self.calls if call["doctype"] in ("Comment", "Communication")]
        self.assertTrue(all(call["limit_page_length"] == 0 for call in message_calls))

    def test_does_not_look_for_messages_without_open_issues(self):
        collect_snapshot(NOW)

        self.assertEqual([call["doctype"] for call in self.calls], ["Issue", "Issue", "Issue"])

    def test_reads_the_newest_messages_first(self):
        # The read is capped, so it has to keep the LAST message of each
        # ticket: reading oldest-first would answer with the opening message
        # and report an answered ticket as waiting on us.
        self.open_issues = [_issue(name="ISS-1")]

        collect_snapshot(NOW)

        message_calls = [call for call in self.calls if call["doctype"] in ("Comment", "Communication")]
        self.assertEqual(len(message_calls), 2)
        self.assertTrue(all(call["order_by"] == "creation desc" for call in message_calls))

    def test_reads_the_newest_tickets_first_in_the_window(self):
        collect_snapshot(NOW)

        self.assertEqual(self._issue_calls()[1]["order_by"], "creation desc")

    def test_resolved_rows_say_when_they_were_resolved(self):
        self.resolved_issues = [
            _issue(name="ISS-9", status="Resolved", modified=datetime(2026, 9, 17, 2, 0, 0))
        ]

        snapshot = collect_snapshot(NOW)

        self.assertEqual(snapshot["resolved"][0]["resolved_at"], datetime(2026, 9, 17, 2, 0, 0))

    def test_returns_the_rows_it_read(self):
        self.open_issues = [_issue(name="ISS-1")]
        self.recent_issues = [_issue(name="ISS-2")]
        self.resolved_issues = [_issue(name="ISS-3")]

        snapshot = collect_snapshot(NOW)

        self.assertEqual([i["name"] for i in snapshot["open"]], ["ISS-1"])
        self.assertEqual([i["name"] for i in snapshot["recent"]], ["ISS-2"])
        self.assertEqual([i["name"] for i in snapshot["resolved"]], ["ISS-3"])


class TestSupportTicketsSection(unittest.TestCase):
    """The entry point the briefing calls."""

    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.side_effect = None
        frappe.get_all.return_value = []
        frappe.get_meta.side_effect = None
        frappe.get_meta.return_value.has_field.return_value = True
        self.addCleanup(setattr, frappe.get_all, "side_effect", None)

    def test_renders_the_section(self):
        self.assertIn("*Chamados da Plataforma:*", support_tickets_section(now=NOW))

    def test_says_it_could_not_read_instead_of_vanishing(self):
        # build_briefing() swallows a section that raises, and a section that
        # silently disappears from Telegram is the failure nobody notices.
        frappe.get_all.side_effect = Exception("Unknown column 'tabIssue.whatever'")

        text = support_tickets_section(now=NOW)

        self.assertIn("*Chamados da Plataforma:*", text)
        self.assertIn("Nao foi possivel ler os chamados", text)

    def test_survives_an_error_log_that_fails_too(self):
        # The guard fires on database trouble, and writing the Error Log is a
        # database write: after a rollback it fails as well.
        frappe.get_all.side_effect = Exception("db down")
        frappe.log_error.side_effect = Exception("db down too")
        self.addCleanup(setattr, frappe.log_error, "side_effect", None)

        self.assertIn("Nao foi possivel ler os chamados", support_tickets_section(now=NOW))

    def test_a_broken_clock_does_not_make_the_section_vanish_either(self):
        with patch.object(support_tickets, "now_datetime", side_effect=Exception("no clock")):
            text = support_tickets_section()

        self.assertIn("Nao foi possivel ler os chamados", text)

    def test_reads_the_window_from_the_frappe_clock(self):
        captured = []
        frappe.get_all.side_effect = lambda doctype, filters=None, **kwargs: captured.append(filters or {}) or []

        with patch.object(support_tickets, "now_datetime", return_value=NOW):
            support_tickets_section()

        self.assertEqual(captured[1]["creation"], [">=", "2026-09-10 08:00:00"])


if __name__ == "__main__":
    unittest.main()
