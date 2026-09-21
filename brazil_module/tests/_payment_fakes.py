"""Shared fakes for the outbound payment tests.

A bare ``MagicMock`` frappe makes the regressions that matter vacuous: ``MagicMock().save()``
never raises, and ``frappe.db.get_value()`` returns a mock, so every claim is "skipped" and
no test can fail. These fakes keep real state:

- ``FakeDB``            rows with a separate *committed* copy, a record of ``for_update`` and an
                        ordered ``events`` list shared with the fake bank client.
- ``FakeInterClient``   records every call in the same ``events`` and, when asked to send,
                        asserts invariant I2 on the **committed** row.
- ``FakeSubmittedDoc``  ``save()`` raises ``UpdateAfterSubmitError`` exactly where Frappe v15
                        does, driven by ``allow_on_submit`` read from the real DocType JSON.

Usage::

    frappe = install_frappe_mock()
    ...
    def setUp(self):
        self.db = FakeDB(singles={"Banco Inter Settings": {"enabled": 1}})
        patch_frappe_db(self, self.db)          # frappe.db and frappe.get_all, with cleanup

Events: ``("get_value", doctype, filters, for_update)`` · ``("set_value", doctype, name, dict)`` ·
``("commit",)`` · ``("rollback",)`` · ``("bank", method, payload)`` · ``("save", doctype, name)``.
``get_all``, ``exists``, ``get_single_value`` and ``sql`` are not events (``sql`` has ``sql_calls``).

Never ``patch.object(frappe, "db", ...)`` on the shared mock - see ``patch_frappe``.

What the fake deliberately does NOT mirror is said where it matters; anything it cannot
mirror raises ``NotImplementedError`` instead of returning something plausible.
"""

import copy
import datetime
import json
import os
import re
import sys
from unittest.mock import MagicMock

DOCTYPE = "Inter Payment Order"

_DOCTYPE_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "bancos", "doctype", "inter_payment_order", "inter_payment_order.json",
)
_IDEMPOTENCY_KEY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ORDERING = {
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
}
_UNSUPPORTED_GET_ALL = ("or_filters", "group_by", "as_list", "distinct", "start", "limit_start")


_MISSING = object()


class UpdateAfterSubmitError(Exception):
    """Stands in for ``frappe.UpdateAfterSubmitError``."""


class UniqueViolationError(Exception):
    """Stands in for the database's duplicate-entry error on a unique column."""


class _Row(dict):
    """A ``frappe._dict``: a dict whose keys read as attributes (missing -> ``None``)."""

    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def install_frappe_mock() -> MagicMock:
    """The ``sys.modules`` injection from CLAUDE.md. Returns the mock, new or already installed.

    ``frappe.utils`` is registered even when another test module installed the mock without it
    (``test_qrcode_gen.py`` does): otherwise ``from frappe.utils import ...`` fails at collection
    whenever that module happens to be imported first.
    """
    current = sys.modules.get("frappe")
    if not isinstance(current, MagicMock):
        current = MagicMock()
        current._ = lambda x: x
        sys.modules["frappe"] = current
        sys.modules["frappe.utils"] = current.utils
    sys.modules.setdefault("frappe.utils", current.utils)
    return current


def patch_frappe(test_case, **attributes) -> None:
    """Replace attributes of the shared frappe mock until the test ends.

    Use this, NOT ``patch.object(frappe, "db", fake)``: when ``patch.object`` stops it deletes
    the mock's child and puts it back outside ``_mock_children``, and from then on
    ``frappe.reset_mock()`` no longer reaches ``frappe.db`` - call counts leak into every
    later test module. Here the attribute is only shadowed in the instance ``__dict__`` and
    the child mock is never touched.
    """
    namespace = install_frappe_mock().__dict__
    for attribute, value in attributes.items():
        previous = namespace.get(attribute, _MISSING)
        namespace[attribute] = value
        test_case.addCleanup(_restore, namespace, attribute, previous)


def patch_frappe_db(test_case, db: "FakeDB") -> "FakeDB":
    """Serve ``frappe.db`` *and* ``frappe.get_all`` from ``db`` until the test ends."""
    patch_frappe(test_case, db=db, get_all=db.get_all)
    return db


def _restore(namespace: dict, attribute: str, previous) -> None:
    if previous is _MISSING:
        namespace.pop(attribute, None)
    else:
        namespace[attribute] = previous


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def _conditions(filters) -> list[tuple]:
    """Normalise Frappe filters to ``[(field, operator, value), ...]``."""
    if isinstance(filters, dict):
        items = []
        for field, expected in filters.items():
            if isinstance(expected, (list, tuple)):
                operator, value = expected
                items.append((field, str(operator).lower(), value))
            else:
                items.append((field, "=", expected))
        return items
    items = []
    for condition in filters or []:
        field, operator, value = condition[-3:]  # a 4-item condition starts with the doctype
        items.append((field, str(operator).lower(), value))
    return items


def _aligned(actual, expected):
    """Let a date/datetime/number column compare with the string MariaDB would coerce."""
    if isinstance(actual, str) == isinstance(expected, str):
        return actual, expected
    text, other = (actual, expected) if isinstance(actual, str) else (expected, actual)
    if isinstance(other, datetime.datetime):
        parsed = datetime.datetime.fromisoformat(text)
    elif isinstance(other, datetime.date):
        parsed = datetime.date.fromisoformat(text[:10])
    elif isinstance(other, (int, float)):
        parsed = float(text)
    else:
        return actual, expected
    return (parsed, expected) if isinstance(actual, str) else (actual, parsed)


def _like(actual, pattern) -> bool:
    regex = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in str(pattern))
    return re.fullmatch(regex, str(actual), flags=re.IGNORECASE | re.DOTALL) is not None


def _compare(actual, operator: str, expected, coalesce_null: bool) -> bool:
    if operator == "is":
        if expected not in ("set", "not set"):
            raise NotImplementedError(f"FakeDB: 'is' takes 'set' or 'not set', got {expected!r}")
        return (actual not in (None, "")) == (expected == "set")
    if operator in ("=", "!=", "in", "not in") and actual is None and coalesce_null:
        actual = ""  # frappe.get_all builds ifnull(column, '')
    if operator == "=":
        return actual is None if expected is None else actual == expected
    if actual is None:
        return False  # SQL: NULL <op> anything is NULL
    if operator == "!=":
        return actual != expected
    if operator in ("in", "not in"):
        return (actual in list(expected)) == (operator == "in")
    if operator in _ORDERING:
        return _ORDERING[operator](*_aligned(actual, expected))
    if operator == "between":
        low, high = expected
        return _compare(actual, ">=", low, False) and _compare(actual, "<=", high, False)
    if operator == "like":
        return _like(actual, expected)
    raise NotImplementedError(f"FakeDB does not mirror the filter operator {operator!r}")


def _matches(row: dict, conditions: list[tuple], coalesce_null: bool) -> bool:
    return all(_compare(row.get(field), op, value, coalesce_null) for field, op, value in conditions)


def _sorted(rows: list[dict], order_by: str | None) -> list[dict]:
    if not order_by:
        return rows
    for clause in reversed([part.strip() for part in order_by.split(",")]):
        field, _, direction = clause.partition(" ")
        field = field.strip("`").split(".")[-1].strip("`")
        present = [row for row in rows if row.get(field) is not None]
        missing = [row for row in rows if row.get(field) is None]
        descending = direction.strip().lower() == "desc"
        present = sorted(present, key=lambda row: row[field], reverse=descending)
        rows = present + missing if descending else missing + present  # MariaDB: NULLs sort lowest
    return rows


# ---------------------------------------------------------------------------
# FakeDB
# ---------------------------------------------------------------------------

class FakeDB:
    """``frappe.db`` over a dict of rows, with a committed copy and an ordered event log.

    Mirrors Frappe v15 where the payment code depends on it:

    - ``get_value``: name or filters; one field -> scalar, several -> tuple, ``as_dict`` -> row
      with attribute access; missing row -> ``None``. Query-builder NULL semantics: a NULL
      column never satisfies ``!=`` / ``not in``.
    - ``get_all``: ``frappe.get_all`` (db_query) semantics: NULL is coalesced to ``''`` for
      ``=``, ``!=``, ``in``, ``not in``. In both, NULL never satisfies ``<`` / ``>``: code that
      wants "NULL or older than" must say so.
    - ``set_value``: bumps ``modified`` unless ``update_modified=False``; a missing row is a
      silent no-op; unique columns raise ``UniqueViolationError`` (``''`` collides, ``None``
      does not) and write nothing.
    - rows default to insertion order (Frappe: ``modified desc``) - pass ``order_by``.
    - row locks are only *recorded* (``for_update``); real lock concurrency needs a bench.
    """

    def __init__(self, singles: dict | None = None, unique: dict | None = None, now=None):
        self.events: list[tuple] = []
        self.reads: list[dict] = []
        self.sql_calls: list[tuple] = []
        self.sql_result: list = []
        self.singles = copy.deepcopy(singles or {})
        self.unique = {DOCTYPE: ("invoice_lock",)} if unique is None else dict(unique)
        self._now = now or datetime.datetime.now
        self._working: dict[str, dict[str, dict]] = {}
        self._committed: dict[str, dict[str, dict]] = {}
        self.after_commit = _Callbacks()

    # -- test set-up and inspection -------------------------------------------------

    def add(self, doctype: str, name: str, **fields) -> None:
        """Insert a row into the working AND the committed copy."""
        if name in self._working.get(doctype, {}):
            raise ValueError(f"FakeDB.add: {doctype} {name} already exists - change it with set_value or row()")
        row = {"name": name, "docstatus": 0, "modified": self._now(), **copy.deepcopy(fields)}
        self._check_unique(doctype, name, row)
        self._working.setdefault(doctype, {})[name] = row
        self._committed.setdefault(doctype, {})[name] = copy.deepcopy(row)

    def row(self, doctype: str, name: str) -> dict:
        """The working copy (what the current transaction sees)."""
        return self._working[doctype][name]

    def committed_row(self, doctype: str, name: str) -> dict:
        """The last committed copy (what another connection - or the bank fake - sees)."""
        return self._committed[doctype][name]

    # -- reads ----------------------------------------------------------------------

    def get_value(self, doctype, filters=None, fieldname="name", as_dict=False, for_update=False, **kw):
        rows = self.get_values(doctype, filters, fieldname, as_dict=as_dict, for_update=for_update, **kw)
        if not rows:
            return None
        row = rows[0]
        return row if (as_dict or len(row) > 1) else row[0]

    def get_values(self, doctype, filters=None, fieldname="name", as_dict=False, for_update=False, **kw):
        fields = [fieldname] if isinstance(fieldname, str) else list(fieldname)
        self.events.append(("get_value", doctype, filters, bool(for_update)))
        self.reads.append({"doctype": doctype, "filters": filters, "fields": fields, "for_update": bool(for_update)})
        if filters is None:
            found = [self.singles[doctype]] if doctype in self.singles else []
        else:
            found = _sorted(self._select(doctype, filters, coalesce_null=False), kw.get("order_by"))
        if fields == ["*"]:
            return [_Row(copy.deepcopy(row)) for row in found]
        if as_dict:
            return [_Row({field: copy.deepcopy(row.get(field)) for field in fields}) for row in found]
        return [tuple(copy.deepcopy(row.get(field)) for field in fields) for row in found]

    def get_single_value(self, doctype, field, **kw):
        return copy.deepcopy(self.singles.get(doctype, {}).get(field))

    def set_single_value(self, doctype, field, value=None, **kw):
        """A Single's field. ``field`` may be a dict, as in ``frappe.db.set_single_value``."""
        values = field if isinstance(field, dict) else {field: value}
        self.singles.setdefault(doctype, {}).update(copy.deepcopy(values))
        self.events.append(("set_single_value", doctype, dict(values)))

    def count(self, doctype, filters=None) -> int:
        return len(self._select(doctype, filters, coalesce_null=True))

    def exists(self, doctype, filters=None):
        if isinstance(doctype, dict):
            filters = {key: value for key, value in doctype.items() if key != "doctype"}
            doctype = doctype["doctype"]
        if filters is None:
            return doctype if doctype in self.singles else None
        found = self._select(doctype, filters, coalesce_null=False)
        return found[0]["name"] if found else None

    def get_all(self, doctype, filters=None, fields=None, pluck=None, **kw):
        unsupported = [key for key in _UNSUPPORTED_GET_ALL if kw.get(key)]
        if unsupported:
            raise NotImplementedError(f"FakeDB.get_all does not mirror {unsupported}")
        fields = [fields] if isinstance(fields, str) else list(fields or ["name"])
        if pluck and pluck not in fields:
            fields = [pluck]
        if any(not re.fullmatch(r"\*|\w+", field) for field in fields):
            raise NotImplementedError(f"FakeDB.get_all only serves plain columns, got {fields}")
        found = _sorted(self._select(doctype, filters, coalesce_null=True), kw.get("order_by"))
        limit = kw.get("limit") or kw.get("limit_page_length") or kw.get("page_length")
        if limit:
            found = found[: int(limit)]
        if pluck:
            return [copy.deepcopy(row.get(pluck)) for row in found]
        if fields == ["*"]:
            return [_Row(copy.deepcopy(row)) for row in found]
        return [_Row({field: copy.deepcopy(row.get(field)) for field in fields}) for row in found]

    def sql(self, *args, **kwargs):
        self.sql_calls.append((args, kwargs))
        return self.sql_result

    # -- writes ---------------------------------------------------------------------

    def set_value(self, doctype, name, field, value=None, update_modified=True, **kw):
        values = copy.deepcopy(field if isinstance(field, dict) else {field: value})
        self.events.append(("set_value", doctype, name, copy.deepcopy(values)))
        # Frappe returns early when the name is None (unless the doctype is a Single).
        targets = [] if name is None else self._select(doctype, name, coalesce_null=False)
        for row in targets:
            self._check_unique(doctype, row["name"], {**row, **values})
        for row in targets:
            row.update(copy.deepcopy(values))
            if update_modified:
                row["modified"] = self._next_modified(row.get("modified"))

    def commit(self) -> None:
        self.events.append(("commit",))
        self._committed = copy.deepcopy(self._working)
        self.after_commit.run()

    def rollback(self) -> None:
        self.events.append(("rollback",))
        self._working = copy.deepcopy(self._committed)
        self.after_commit.reset()

    # -- internals ------------------------------------------------------------------

    def _select(self, doctype, filters, coalesce_null: bool) -> list[dict]:
        table = self._working.get(doctype, {})
        if isinstance(filters, str):
            return [table[filters]] if filters in table else []
        conditions = _conditions(filters)
        return [row for row in table.values() if _matches(row, conditions, coalesce_null)]

    def _check_unique(self, doctype: str, name: str, candidate: dict) -> None:
        for column in self.unique.get(doctype, ()):
            value = candidate.get(column)
            if value is None:
                continue
            for other_name, other in self._working.get(doctype, {}).items():
                if other_name != name and other.get(column) == value:
                    raise UniqueViolationError(
                        f"Duplicate entry {value!r} for unique column {column} of {doctype} "
                        f"({name} collides with {other_name})"
                    )

    def _next_modified(self, previous):
        stamp = self._now()
        if isinstance(previous, datetime.datetime) and stamp <= previous:
            stamp = previous + datetime.timedelta(microseconds=1)
        return stamp


# ---------------------------------------------------------------------------
# FakeInterClient
# ---------------------------------------------------------------------------

class _FakeAuth:
    """``client.auth``: only what the payment path touches."""

    def __init__(self, client):
        self._client = client

    def get_cert_paths(self) -> tuple[str, str]:
        # Kept out of ``calls``: that list is what the BANK was asked, and a test reads payloads
        # from it positionally.
        self._client.cert_calls.append(True)
        return self._client._respond("get_cert_paths")


class _Callbacks:
    """``frappe.db.after_commit``: work that must wait for the transaction to end."""

    def __init__(self):
        self._functions: list = []

    def add(self, func) -> None:
        self._functions.append(func)

    def run(self) -> None:
        pending, self._functions = self._functions, []
        for func in pending:
            func()

    def reset(self) -> None:
        self._functions = []


class FakeInterClient:
    """The slice of ``InterAPIClient`` the payment path uses. It never touches the network.

    ``responses[method]`` is what the bank answers: a value, an ``Exception`` instance (raised)
    or a callable receiving the method's arguments. Every call is appended to ``calls`` and, as
    ``("bank", method, payload)``, to ``db.events`` - so a test can assert that the claim's
    ``("commit",)`` comes before the send.

    ``send_pix`` / ``pay_barcode`` assert **I2** first: the *committed* row of ``doctype_row``
    is ``Processing`` with ``idempotency_key`` and ``bank_request_at``, and the key being sent
    is the committed one. A violation is kept in ``i2_violations`` as well, so it survives a
    caller that swallows the ``AssertionError``. There is no ``send_ted``: nothing may call it.
    """

    DEFAULT_RESPONSES = {
        "send_pix": {"tipoRetorno": "APROVACAO", "codigoSolicitacao": "fake-codigo-solicitacao"},
        "pay_barcode": {"statusPagamento": "AGUARDANDO_APROVACAO", "codigoTransacao": "fake-codigo-transacao"},
        "get_pix_payment": {"transacaoPix": {"status": "AGUARDANDO_APROVACAO"}},
        "find_barcode_payments": [],
        "get_webhook": {},
        "get_cert_paths": ("/private/files/inter.crt", "/private/files/inter.key"),
    }
    # Kept equal to the real client by test_payment_fakes.py (it reads inter_client.py).
    FIND_BARCODE_PAYMENTS_KEYWORDS = (
        "barcode", "codigo_transacao", "start_date", "end_date", "filter_date_by", "max_retries",
    )
    PAYMENT_DATE_FILTERS = ("INCLUSAO", "PAGAMENTO", "VENCIMENTO")

    def __init__(self, db: FakeDB, doctype_row: tuple | None = None):
        self.db = db
        self.doctype_row = doctype_row
        self.responses: dict = {}
        self.calls: list[tuple] = []
        self.i2_violations: list[str] = []
        self.cert_calls: list[bool] = []
        # The real client resolves the mTLS certificate through auth, and a missing file raises a
        # plain OSError there - which is why the service resolves it among its pre-send guards.
        self.auth = _FakeAuth(self)

    def send_pix(self, payment_data: dict, idempotency_key: str) -> dict:
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY.match(idempotency_key):
            raise ValueError(f"idempotency_key must be a lowercase UUID, got {idempotency_key!r}")
        self.calls.append(("send_pix", copy.deepcopy(payment_data), idempotency_key))
        self.db.events.append(("bank", "send_pix", copy.deepcopy(payment_data)))
        self._assert_intent_committed(idempotency_key)
        return self._respond("send_pix", payment_data, idempotency_key)

    def pay_barcode(self, payment_data: dict) -> dict:
        self.calls.append(("pay_barcode", copy.deepcopy(payment_data)))
        self.db.events.append(("bank", "pay_barcode", copy.deepcopy(payment_data)))
        self._assert_intent_committed(None)
        return self._respond("pay_barcode", payment_data)

    def get_pix_payment(self, codigo_solicitacao: str, max_retries: int | None = None) -> dict:
        self.calls.append(("get_pix_payment", codigo_solicitacao, max_retries))
        self.db.events.append(("bank", "get_pix_payment", codigo_solicitacao))
        return self._respond("get_pix_payment", codigo_solicitacao)

    def get_webhook(self, webhook_type: str = "pix") -> dict:
        self.calls.append(("get_webhook", webhook_type))
        self.db.events.append(("bank", "get_webhook", webhook_type))
        return self._respond("get_webhook", webhook_type)

    def find_barcode_payments(self, **kw) -> list[dict]:
        self._refuse_like_the_real_client(kw)
        self.calls.append(("find_barcode_payments", dict(kw)))
        self.db.events.append(("bank", "find_barcode_payments", dict(kw)))
        return self._respond("find_barcode_payments", **kw)

    def _refuse_like_the_real_client(self, kw: dict) -> None:
        """``InterAPIClient.find_barcode_payments`` is keyword-only and validates before any request."""
        unknown = sorted(set(kw) - set(self.FIND_BARCODE_PAYMENTS_KEYWORDS))
        if unknown:
            raise TypeError(f"find_barcode_payments() got an unexpected keyword argument {unknown[0]!r}")
        if (kw.get("start_date") is None) != (kw.get("end_date") is None):
            raise ValueError("start_date and end_date must be given together")
        if kw.get("filter_date_by", "INCLUSAO") not in self.PAYMENT_DATE_FILTERS:
            raise ValueError(f"filter_date_by must be one of {', '.join(self.PAYMENT_DATE_FILTERS)}")

    def _respond(self, method: str, *args, **kwargs):
        response = self.responses.get(method, self.DEFAULT_RESPONSES[method])
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(*args, **kwargs)
        return copy.deepcopy(response)

    def _assert_intent_committed(self, idempotency_key: str | None) -> None:
        problem = self._intent_problem(idempotency_key)
        if problem:
            self.i2_violations.append(problem)
            raise AssertionError(f"I2 violated - sent to the bank before the intent was committed: {problem}")

    def _intent_problem(self, idempotency_key: str | None) -> str | None:
        if not self.doctype_row:
            return "FakeInterClient was built without doctype_row, it cannot tell which order is being sent"
        doctype, name = self.doctype_row
        try:
            committed = self.db.committed_row(doctype, name)
        except KeyError:
            return f"{doctype} {name} does not exist in the committed database"
        if committed.get("status") != "Processing":
            return f"committed status of {name} is {committed.get('status')!r}, expected 'Processing'"
        for field in ("idempotency_key", "bank_request_at"):
            if not committed.get(field):
                return f"committed row of {name} has no {field}"
        if idempotency_key is not None and committed["idempotency_key"] != idempotency_key:
            return f"key sent for {name} ({idempotency_key}) is not the committed one ({committed['idempotency_key']})"
        return None


# ---------------------------------------------------------------------------
# FakeSubmittedDoc
# ---------------------------------------------------------------------------

def _load_schema(json_path: str | None) -> tuple[frozenset, frozenset]:
    """``(every fieldname, the allow_on_submit ones)`` from a DocType JSON; empty without one."""
    if not json_path:
        return frozenset(), frozenset()
    with open(json_path) as fh:
        fields = json.load(fh).get("fields", [])
    names = frozenset(df["fieldname"] for df in fields if df.get("fieldname"))
    allowed = frozenset(df["fieldname"] for df in fields if df.get("allow_on_submit"))
    return names, allowed


class FakeSubmittedDoc:
    """A document loaded from ``FakeDB`` whose ``save()`` fails where Frappe v15's does.

    ``Document.save()`` on ``docstatus == 1`` runs ``_validate_update_after_submit``: every
    DocType field without ``allow_on_submit`` whose value differs from the database raises
    ``UpdateAfterSubmitError``. That is the production defect (spec section 1): the order is always
    submitted when executed, so ``order.save()`` raised *after* the bank accepted the payment.

    The schema is read from the real ``inter_payment_order.json`` at construction (pass
    ``json_path`` for another schema). A doctype without a schema protects every field.
    Use it as ``frappe.get_doc.side_effect = lambda doctype, name: FakeSubmittedDoc(db, name, doctype)``.
    """

    _OWN = ("_db", "doctype", "name", "meta_fields", "allow_on_submit", "comments", "notified", "_values")

    def __init__(self, db: FakeDB, name: str, doctype: str = DOCTYPE, json_path: str | None = None):
        if json_path is None and doctype == DOCTYPE:
            json_path = _DOCTYPE_JSON
        meta_fields, allow_on_submit = _load_schema(json_path)
        object.__setattr__(self, "_db", db)
        object.__setattr__(self, "doctype", doctype)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "meta_fields", meta_fields)
        object.__setattr__(self, "allow_on_submit", allow_on_submit)
        object.__setattr__(self, "comments", [])
        object.__setattr__(self, "notified", 0)
        object.__setattr__(self, "_values", copy.deepcopy(db.row(doctype, name)))

    def __getattr__(self, key):
        values = object.__getattribute__(self, "_values")
        if key in values:
            return values[key]
        meta_fields = object.__getattribute__(self, "meta_fields")
        if not key.startswith("_") and (not meta_fields or key in meta_fields):
            return None  # a field of the doctype that was never set
        raise AttributeError(f"{object.__getattribute__(self, 'doctype')} has no field {key!r}")

    def __setattr__(self, key, value):
        if key in self._OWN:
            object.__setattr__(self, key, value)
        else:
            self._values[key] = value

    def get(self, key, default=None):
        value = self._values.get(key)
        return default if value is None else value

    def set(self, key, value) -> None:
        self._values[key] = value

    def as_dict(self) -> dict:
        return _Row(copy.deepcopy(self._values), doctype=self.doctype)

    def reload(self):
        object.__setattr__(self, "_values", copy.deepcopy(self._db.row(self.doctype, self.name)))
        return self

    def db_set(self, fieldname, value=None, update_modified=True, commit=False, **kw) -> None:
        values = fieldname if isinstance(fieldname, dict) else {fieldname: value}
        self._db.set_value(self.doctype, self.name, values, update_modified=update_modified)
        self._values.update(copy.deepcopy(values))
        if commit:
            self._db.commit()

    def add_comment(self, comment_type="Comment", text=None, **kw) -> None:
        self.comments.append((comment_type, text))

    def notify_update(self) -> None:
        object.__setattr__(self, "notified", self.notified + 1)

    def save(self, *args, **kwargs):
        stored = self._db.row(self.doctype, self.name)
        # Record the attempt BEFORE validating. A save on a submitted order is exactly the defect
        # that caused the incident, it always raises, and every caller swallows that exception - so
        # recording it afterwards would make "the service never saved" an assertion that cannot fail.
        self._db.events.append(("save", self.doctype, self.name))
        if stored.get("docstatus") == 1:
            self._validate_update_after_submit(stored)
        stored.update(copy.deepcopy(self._values))
        stored["modified"] = self._db._next_modified(stored.get("modified"))
        self._values["modified"] = stored["modified"]
        return self

    def _validate_update_after_submit(self, stored: dict) -> None:
        candidates = self.meta_fields or (set(self._values) | set(stored)) - {"name", "docstatus", "modified"}
        for key in sorted(candidates):
            mine, theirs = self._values.get(key), stored.get(key)
            if key in self.allow_on_submit or not (mine or theirs) or mine == theirs:
                continue
            raise UpdateAfterSubmitError(
                f"Not allowed to change {key} after submission from {theirs!r} to {mine!r}"
            )
