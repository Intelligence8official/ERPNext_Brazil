# Inter Payment Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make it impossible for the same payable to reach Banco Inter twice, and make the ERP tell the truth about what the bank did.

**Architecture:** One path to the bank (`payment_service.execute_payment_order`), entered only through a committed row-lock claim from `Approved`. Every later state change is a compare-and-set written with `frappe.db.set_value`. The HTTP client never re-sends a payment POST and classifies outcomes as definitive (allowlist) or ambiguous (everything else). The weekly scheduler only creates orders and enqueues them.

**Tech Stack:** Python 3.12, Frappe/ERPNext v15 (mocked in tests via `sys.modules["frappe"]`), `requests`, pytest (`.venv/bin/python -m pytest`), ruff.

**Spec:** `docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md` — read it completely before your task. The spec's invariants I1–I9 are the acceptance criteria of every task.

## Global Constraints

- Package root is `brazil_module/` (the `brazil/brazil/` paths in CLAUDE.md are outdated).
- Run tests with `.venv/bin/python -m pytest <path> -q` (system python has no pytest). During your task run only your own test files plus `brazil_module/tests/test_hooks_cron.py`; the orchestrator runs the full suite between waves.
- TDD: write the failing test, run it and see it fail for the right reason, then implement, then see it pass.
- **Do not commit, stage, push or switch branches.** The orchestrator owns git. Work in the current working tree on branch `fix/inter-payment-safety`.
- Touch only the files listed under your task. If you believe another file must change, report it in your final message instead of editing it.
- Never call Banco Inter or any network endpoint from tests or scripts.
- `Document.save()` must never be called on a submitted `Inter Payment Order` (I5). State is written with `frappe.db.set_value(...)` + `frappe.db.commit()`, never with `update_modified=False`.
- Multi-field reads use `frappe.db.get_value(doctype, name, [fields], as_dict=True, for_update=...)`.
- Status strings, constants and function signatures are exactly those of spec §3 and §4. Do not rename.
- User-facing strings go through `frappe._` / `__()`; English source strings, matching the surrounding code.
- Match the surrounding code's style; functions under 50 lines; no new dependencies.

## File Structure

| File | Task | Responsibility |
|---|---|---|
| `brazil_module/services/banking/payment_guards.py` (new) | T1 | constants, blocking rule, invoice/order guards, account lookup, kill switch |
| `brazil_module/services/banking/payment_alerts.py` (new) | T1 | `alert_operator` — Error Log + Notification Log + Telegram, never raises |
| `brazil_module/tests/_payment_fakes.py` (new) | T1 | `FakeDB`, `FakeInterClient`, `FakeSubmittedDoc`, `install_frappe_mock()` |
| `brazil_module/tests/test_payment_guards.py`, `test_payment_alerts.py`, `test_payment_fakes.py` (new) | T1 | |
| `brazil_module/services/banking/inter_client.py`, `auth_manager.py` | T2 | retry policy, idempotency header, ambiguous classification, 401 fix, `find_barcode_payments` |
| `brazil_module/tests/test_inter_client_banking.py`, `test_auth_manager.py` | T2 | |
| `brazil_module/bancos/doctype/inter_payment_order/inter_payment_order.{json,py,js}`, `inter_payment_order_list.js` (new) | T3 | schema, controller, form and list UI |
| `brazil_module/tests/test_inter_payment_order.py` (new), `test_doctype_banking_validation.py` (IPO parts only) | T3 | |
| `brazil_module/services/banking/payment_service.py`, `reconciliation.py` (`on_payment_entry_submit` only), `brazil_module/hooks.py` (Payment Entry `on_cancel` doc_event only) | T4 | the single path, CAS transitions, polling, Payment Entry, cron |
| `brazil_module/tests/test_payment_service.py`, `test_reconciliation.py` (hook parts only) | T4 | |
| `brazil_module/api/__init__.py` (`create_payment_order`, `execute_payment`, `i8_run_payment_scheduling` only), `brazil_module/public/js/purchase_invoice.js` | T5 | entry points |
| `brazil_module/tests/test_api_payments.py` (new) | T5 | |
| `brazil_module/services/intelligence/recurring/planning_loop.py`, `daily_briefing.py` | T6 | scheduler unified with the order path; daily section |
| `brazil_module/tests/test_planning_loop.py`, `test_planning_loop_payments.py` (new), `test_daily_briefing*.py` (payment section only) | T6 | |
| `brazil_module/patches/v1_1/flag_stuck_payment_orders.py` (new), `brazil_module/patches.txt`, `brazil_module/services/intelligence/action_executor.py` (allowlist line), `CLAUDE.md`, `brazil_module/tests/test_patch_flag_stuck_payment_orders.py` (new), `brazil_module/tests/test_payment_invariants.py` (new) | T7 | migration, docs, repo-wide tripwires |

Waves: **T1 ∥ T2** → **T3 ∥ T4** → **T5 ∥ T6** → **T7**.

---

### Task 1: Guards, alerts and shared test fakes

**Files:** see table.

**Interfaces — Produces** (exact; T3–T7 depend on them):

```python
# payment_guards.py
DOCTYPE = "Inter Payment Order"
IN_FLIGHT_STATUSES = ("Processing", "Awaiting Bank", "Needs Verification")
NON_BLOCKING_STATUSES = ("Failed", "Cancelled")
CANCELLABLE_STATUSES = ("Draft", "Pending Approval", "Approved", "Failed")
AMOUNT_TOLERANCE = 0.01
def is_integration_enabled() -> bool
def is_blocking(status: str, payment_entry: str | None) -> bool
def find_blocking_payment_order(purchase_invoice: str, exclude: str | None = None) -> dict | None
def find_draft_payment_entry(purchase_invoice: str, exclude_order: str | None = None) -> dict | None
def check_invoice_payable(purchase_invoice: str, amount: float, order_name: str | None = None, company: str | None = None) -> str | None
def get_inter_account_for_company(company: str) -> str | None

# payment_alerts.py
def alert_operator(subject: str, message: str, order_name: str | None = None) -> None

# tests/_payment_fakes.py
def install_frappe_mock() -> MagicMock            # the sys.modules injection from CLAUDE.md, returns the mock
class UpdateAfterSubmitError(Exception)
class FakeDB:
    events: list[tuple]                           # ("get_value", doctype, name, for_update) / ("set_value", doctype, name, dict) / ("commit",) / ("rollback",) / ("bank", method, payload)
    def add(self, doctype: str, name: str, **fields) -> None          # inserts into working AND committed copies
    def row(self, doctype, name) -> dict                               # working copy
    def committed_row(self, doctype, name) -> dict                     # last committed copy
    def get_value(self, doctype, filters, fieldname="name", as_dict=False, for_update=False, **kw)
    def get_values(...)                                                # thin wrapper, list of rows
    def set_value(self, doctype, name, field, value=None, **kw)        # field may be a dict; bumps "modified"
    def get_single_value(self, doctype, field, **kw)                   # from FakeDB(singles={...})
    def exists(self, doctype, filters=None)
    def get_all(self, doctype, filters=None, fields=None, pluck=None, **kw)
    def sql(self, *a, **kw)                                            # returns self.sql_result (default [])
    def commit(self) -> None
    def rollback(self) -> None
class FakeInterClient:                            # FakeInterClient(db, doctype_row=(DOCTYPE, name))
    def send_pix(self, payment_data, idempotency_key) -> dict
    def pay_barcode(self, payment_data) -> dict
    def get_pix_payment(self, codigo_solicitacao) -> dict
    def find_barcode_payments(self, **kw) -> list[dict]
    # .responses = {"send_pix": dict | Exception, ...}; .calls = list; send_* assert I2 on db.committed_row(...)
class FakeSubmittedDoc:                           # save() raises UpdateAfterSubmitError when docstatus == 1 and a
    ...                                           # non-allow_on_submit field (read from the real DocType JSON) changed
```

`FakeDB.get_value` semantics mirror Frappe: `filters` is a name or a dict (equality, plus `["in", [...]]`, `["not in", [...]]`, `["!=", x]`, `["<", n]`, `[">", n]`, `["is", "set" | "not set"]`); single field → scalar, list of fields → tuple, or dict-like with attribute access when `as_dict=True`; missing row → `None`.

- [ ] **Step 1 — fakes first, with their own tests** (`test_payment_fakes.py`): commit/rollback restore the working copy from the committed copy; `for_update` recorded in `events`; `FakeInterClient.send_pix` raises `AssertionError` when the committed row is not `Processing` with `idempotency_key` and `bank_request_at`; `FakeSubmittedDoc.save()` raises for a changed `status` on `docstatus == 1` and passes on `docstatus == 0`.
- [ ] **Step 2 — guard tests** (`test_payment_guards.py`, using `FakeDB` via `patch.object(frappe, "db", fake)`), one test per reason of `check_invoice_payable` plus the happy path, and:
  - `is_blocking`: `Failed`/`Cancelled` → False; `Completed` with `payment_entry` → False; `Completed` without → True; every other status → True.
  - `find_blocking_payment_order` ignores the excluded name, `docstatus == 2`, non-blocking rows; returns `{"name", "status"}`.
  - `find_draft_payment_entry` ignores submitted entries and entries whose `inter_payment_order == exclude_order` (it reads `Payment Entry Reference` rows joined to `Payment Entry` — implement with `frappe.get_all` on both doctypes so `FakeDB` can serve it; no raw SQL).
  - `check_invoice_payable`: invoice `docstatus != 1`; `on_hold`; supplier `on_hold` with `hold_type in ("All", "Payments")`; `company` mismatch; `outstanding_amount + AMOUNT_TOLERANCE < amount`; another blocking order; a draft Payment Entry. Reasons are plain English sentences naming the document.
  - `get_inter_account_for_company` filters `{"company": company, "sync_enabled": 1}`.
- [ ] **Step 3 — implement `payment_guards.py`**; run `test_payment_guards.py` green.
- [ ] **Step 4 — `alert_operator` tests** (`test_payment_alerts.py`): each channel failing (Error Log, Notification Log insert, Telegram import/send) never raises and does not stop the others; Telegram is skipped when `telegram_chat_id` is empty; the message contains the order name.
- [ ] **Step 5 — implement `payment_alerts.py`**. Telegram via lazy `from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot` inside the try. Notification Log: one row per enabled user holding role `Banco Inter Manager` (`frappe.get_all("Has Role", filters={"role": ..., "parenttype": "User"}, pluck="parent")`), `document_type = DOCTYPE`, `document_name = order_name`.
- [ ] **Step 6 — `ruff check` on the new files; all three test files green.**

---

### Task 2: HTTP client — never re-send a payment

**Files:** see table.

**Interfaces — Produces:**

```python
class InterAPIError(Exception):                 # __init__(message, status_code=None, response_body=None)
class InterAmbiguousResultError(InterAPIError)
InterAPIClient.send_pix(payment_data: dict, idempotency_key: str) -> dict
InterAPIClient.pay_barcode(payment_data: dict) -> dict
InterAPIClient.get_pix_payment(codigo_solicitacao: str) -> dict
InterAPIClient.find_barcode_payments(*, barcode=None, codigo_transacao=None, start_date=None, end_date=None, filter_date_by="INCLUSAO") -> list[dict]
InterAPIClient._request(method, path, data=None, params=None, api_module="Banking", max_retries=3, retry_safe=None, extra_headers=None) -> dict
```

- [ ] **Step 1 — make the test file order-independent.** At the top of `test_inter_client_banking.py`, load the real exception classes even if `sys.modules["requests"]` is a `MagicMock` left by another test module: temporarily pop every `requests*` key from `sys.modules`, `importlib.import_module("requests.exceptions")`, keep a reference (`REAL_EXC`), restore the popped entries. In `setUp`, `patch.object(_ic_mod, "requests", SimpleNamespace(request=MagicMock(), exceptions=REAL_EXC))` with `addCleanup`. Patch `_ic_mod.time.sleep` so no test sleeps. Verify: the file passes alone, after `test_auth_manager.py`, and before it (`pytest a.py b.py` both orders).
- [ ] **Step 2 — failing tests for the non-`retry_safe` policy** (spec §4.2), each asserting the exact number of `requests.request` calls:
  - `ReadTimeout`, `ConnectionError("Connection aborted")`, `SSLError`, HTTP 302, 408, 409, 500, 502, 503, 504 on `send_pix` and on `pay_barcode` → **1 call**, `InterAmbiguousResultError`.
  - `ConnectTimeout` then 200 → 2 calls, success. `ConnectTimeout` exhausted → `InterConnectionError`, not ambiguous.
  - 400, 403, 404, 406, 422, 429 → 1 call, `InterAPIError` with `status_code`, `not isinstance(..., InterAmbiguousResultError)`, no sleep.
  - 401 then 200 → token refreshed once via the auth manager, 2 calls; 401 twice → `InterAPIError(status_code=401)`.
  - `allow_redirects=False` is passed for non-`retry_safe` requests.
  - `send_pix` sends header `x-id-idempotente`; an empty / upper-case / non-UUID key → `ValueError` and **0 calls**.
  - an ambiguous failure writes one `Inter API Log` row with `success = 0`, `response_code = 0` (or the HTTP code).
- [ ] **Step 3 — tests that `retry_safe` behaviour is preserved** for GET: 500 → retried, read timeout → retried then `InterTimeoutError`, `SSLError` → `InterCertificateError` without retry, 429 → backoff.
- [ ] **Step 4 — implement.** Track `request_issued` inside the loop; catch `ConnectTimeout` **before** `Timeout`/`ConnectionError`. Replace the broken 401 branch with a proper forced refresh: add `InterAuthManager.get_valid_token(scopes=None, force_refresh: bool = False)` (tests in `test_auth_manager.py`) and call it with `force_refresh=True`.
- [ ] **Step 5 — `find_barcode_payments` and `get_pix_payment` rename**, with tests for the query parameters (`codBarraLinhaDigitavel`, `codigoTransacao`, `dataInicio`, `dataFim`, `filtrarDataPor`) and for a non-list response → `[]`.
- [ ] **Step 6 — ruff + both test files green in the three orders of Step 1.**

---

### Task 3: DocType `Inter Payment Order`

**Files:** see table.

**Interfaces — Consumes:** everything from T1; from T4 (patch these names in tests — do not import them at module level): `payment_service.enqueue_payment_execution(name) -> bool`, `poll_bank_status(name) -> dict`, `resolve_verification(name, outcome, *, bank_reference="", paid_on=None, note="") -> dict`, `create_payment_entry_for_order(name, paid_on=None) -> str | None`.

**Interfaces — Produces:** the JSON schema of spec §4.3 (fields `idempotency_key`, `bank_status`, `bank_request_at`, `invoice_lock`; status options of §3) and the whitelisted controller methods `approve_payment`, `execute_payment`, `check_bank_status`, `resolve_verification(outcome, bank_reference="", paid_on=None, note="")`, `create_payment_entry`.

- [ ] **Step 1 — JSON contract tests** (`test_inter_payment_order.py`, reading the real JSON): status options equal the §3 list in order; `IN_FLIGHT ∪ NON_BLOCKING ∪ CANCELLABLE ⊂` options; the four new fields exist, are `read_only` and `no_copy`; `invoice_lock` is `unique` and `hidden`; `status`, `transaction_id`, `approval_code`, `execution_date`, `inter_response`, `payment_entry` are `no_copy`; new fields are present in `field_order`.
- [ ] **Step 2 — edit the JSON** (keep key ordering and indentation style of the file; bump `modified`).
- [ ] **Step 3 — controller tests** (import the controller with `frappe.model.document.Document` mocked as a plain base class; `patch.object(frappe, "db", FakeDB(...))`; `frappe.throw.side_effect` = a real exception class):
  - `before_insert` on a document carrying `status="Cancelled"`, `idempotency_key`, `approval_code`, `transaction_id`, `bank_status`, `bank_request_at`, `execution_date`, `inter_response`, `payment_entry` → all cleared, `status == "Draft"`.
  - `validate`: TED rejected; boleto without `boleto_due_date` rejected; `barcode` normalised to digits; `check_invoice_payable` reason → throw; payable → `invoice_lock == purchase_invoice`; no `purchase_invoice` → `invoice_lock` stays empty.
  - `approve_payment`: DB row `Pending Approval` → `Approved`; a document whose in-memory `status` says `Pending Approval` while the DB row says `Processing` → throws, DB untouched.
  - `execute_payment`: DB row not `Approved` → throws; kill switch off → throws; never writes `status`; calls `enqueue_payment_execution(name)`; when it returns `False` the user message says it is already queued or running.
  - `before_cancel`: DB status in `Processing` / `Awaiting Bank` / `Needs Verification` / `Completed` → throws even if `self.status` says `Approved`; read uses `for_update=True`. `on_cancel` sets `Cancelled` and `invoice_lock = None` with `frappe.db.set_value` (not `update_modified=False`).
  - `check_bank_status`, `resolve_verification`, `create_payment_entry` delegate with the exact arguments; the last two call `frappe.only_for(("Banco Inter Manager", "System Manager"))`.
- [ ] **Step 4 — implement the controller** (lazy imports of `payment_service` inside methods, like today).
- [ ] **Step 5 — JS.** Form: buttons per status (spec §4.3), each doing `await frm.reload_doc()` before the call; *Resolve Verification* dialog with outcome select, `bank_reference`, `paid_on` (Date, mandatory for `paid`), `note`, and for `not_paid` three mandatory checkboxes; indicator map gains `Awaiting Bank` (purple) and `Needs Verification` (red); `purchase_invoice(frm)` reads `outstanding_amount`; after *Execute Payment* show an alert and reload after 4s. List view file with `get_indicator` by `status`.
- [ ] **Step 6 — ruff, tests green.**

---

### Task 4: `payment_service.py` — the single path

**Files:** see table.

**Interfaces — Consumes:** T1 (guards, alerts, fakes), T2 (`InterAPIClient`, `InterAPIError`, `InterAmbiguousResultError`; `InterAuthError` from `auth_manager`), the T3 field names.

**Interfaces — Produces:** the functions of spec §4.4 with those exact signatures.

- [ ] **Step 1 — rewrite `test_payment_service.py` around the fakes.** Delete the old tests (they encode the defect, notably `test_retries_stuck_orders`). Patch `_ps_mod.InterAPIClient` (import it at module level in `payment_service.py` so tests can patch it) with a factory returning `FakeInterClient`. Tests, grouped:
  - **claim**: `Approved` → exactly one `("bank", "send_pix", …)` event, and it comes after a `("commit",)` that followed the claim `set_value`; the claim read has `for_update=True` and includes `idempotency_key`; key is a lowercase UUID; second `execute_payment_order` on the same `FakeDB` → `{"status": "skipped"}`, zero new bank events; statuses `Processing`, `Awaiting Bank`, `Needs Verification`, `Completed`, `Failed`, `Draft`, `docstatus 0/2` → skipped, zero bank events.
  - **kill switch / staleness**: disabled → `blocked`, no writes; `requested_at` 16 minutes old → `skipped` + `alert_operator`, no writes.
  - **pre-send guards** → `Failed`, zero bank events, `invoice_lock` cleared: `check_invoice_payable` reason; no Inter account; payload building error.
  - **payload**: Pix `valor` is a float with 2 decimals, `descricao` ≤ 140, no `dataAgendamento`, `dataPagamento` only for a future `scheduled_date`; boleto `valorPagar` is `"%.2f"`, `dataVencimento` present.
  - **classification**: `InterAmbiguousResultError` → `Needs Verification`; `InterAPIError(status_code=422)` → `Failed` with the bank message in `inter_response`; `InterAuthError` → `Failed`; `RuntimeError` and a fake `JobTimeoutException` → `Needs Verification` **and re-raised**; 406 message mentions the bill is already paid at the bank.
  - **2xx mapping**: Pix `APROVACAO` → `Awaiting Bank`, `approval_code == codigoSolicitacao`, `bank_status == "AGUARDANDO_APROVACAO"`, then exactly one `get_pix_payment`; Pix `PROCESSADO` → `Awaiting Bank` (not `Completed`); 2xx without id → `Needs Verification`; boleto `REALIZADO` → `Completed`, `AGUARDANDO_APROVACAO` → `Awaiting Bank`, `ERRO` → `Failed`.
  - **the regression**: the module never calls `save()` — use `FakeSubmittedDoc` wherever a document is loaded and assert the flow ends `Awaiting Bank`; plus `assert ".save(" not in inspect.getsource(_ps_mod)`.
  - **CAS**: `mark_failed` on a `Completed` row → `False`, no `set_value`; `mark_completed` with `expected_from=("Awaiting Bank",)` on `Needs Verification` → `False`; every successful `mark_*` issues state `set_value` → `commit` before any comment/alert, and a final `commit`.
  - **poll**: every status of the §3 tables for Pix and boleto lands in the right state; `[]`, a 404 `InterAPIError`, a 403 (alerts once) and a timeout leave the row untouched; no `approval_code` → `{"status": "no_bank_id"}`; boleto poll passes `codigo_transacao`, `filter_date_by="INCLUSAO"` and a ±1-day window around `bank_request_at`.
  - **resolve**: `at_bank` without reference → throws; reference already used by another order → throws; valid → `approval_code` set and mapped; `paid` without `paid_on`/reference → throws; `paid` → `Completed` and Payment Entry dated `paid_on`; `not_paid` with `approval_code` set → throws; without `note` → throws; from any status other than `Needs Verification` → throws.
  - **Payment Entry**: built from the invoice (party, `credit_to`), `allocated_amount = min(amount, outstanding)`; existing submitted entry with `inter_payment_order == name` → adopted, none created; existing draft → submitted; invoice already settled → none created, comment only; insert raising → `frappe.db.rollback()` called before logging, one `alert_operator`, order still `Completed`; success sets `payment_entry` and clears `invoice_lock`.
  - **cron matrix**: for every status × {PIX, Boleto Payment}, `scheduled_payment_status_check()` produces **zero** `send_*` events; `Awaiting Bank` → polled; more than 15 → only 15 polled and the cap is logged; `Processing` older than 30 min or with empty `bank_request_at` → `Needs Verification`; younger → untouched; `Awaiting Bank` older than 85 days → `Needs Verification`; kill switch off → nothing.
  - **hooks**: `on_payment_entry_cancel` clears `payment_entry`, restores `invoice_lock` (a unique-key exception is swallowed + alert); `on_payment_entry_submit` no longer writes `status` and links `payment_entry` only when empty.
  - **`create_payment_order_for_invoice`**: party, company, account, amount = outstanding, `boleto_due_date` default = invoice `due_date`; `submit=False` leaves a draft; no Inter account → throws.
  - **`enqueue_payment_execution`**: passes `queue="short"`, `timeout=600`, `job_id=f"inter_payment_order::{name}"`, `deduplicate=True`, `payment_order_name`, `requested_at`; returns `False` when `frappe.enqueue` returns `None`.
- [ ] **Step 2 — implement**, in this order, running the matching test group after each: CAS core (`_transition` + the four `mark_*`) → claim + `execute_payment_order` → mapping + `poll_bank_status` → `create_payment_entry_for_order` → `resolve_verification` → `scheduled_payment_status_check` → `create_payment_order_for_invoice`, `enqueue_payment_execution` → hooks. Keep each function under 50 lines; status tables as module-level frozensets (`PIX_PAID`, `PIX_REJECTED`, `PIX_NEEDS_HUMAN`, `BOLETO_PAID`, `BOLETO_REJECTED`).
- [ ] **Step 3 — `hooks.py`**: add `"on_cancel": "brazil_module.services.banking.payment_service.on_payment_entry_cancel"` to the existing `Payment Entry` doc_events entry (do not touch scheduler_events); `test_hooks_cron.py` stays green.
- [ ] **Step 4 — ruff, `test_payment_service.py` + `test_reconciliation.py` + `test_hooks_cron.py` green.**

---

### Task 5: API entry points and the invoice dialog

**Files:** see table.

**Interfaces — Consumes:** `payment_service.create_payment_order_for_invoice`, the controller's `execute_payment`, `payment_guards.is_integration_enabled`, `get_inter_account_for_company`.

- [ ] **Step 1 — tests** (`test_api_payments.py`; `@frappe.whitelist()` must be a pass-through decorator in the mock):
  - `create_payment_order` with `purchase_invoice` → delegates to `create_payment_order_for_invoice(..., submit=False)` and returns `{"status": "success", "payment_order": name}`; a guard exception → `{"status": "error", "message": <reason>}`; without `purchase_invoice` → the existing behaviour, account via `get_inter_account_for_company`.
  - `execute_payment` → `frappe.get_doc(...).execute_payment()`; it no longer calls `frappe.enqueue` itself.
  - `i8_run_payment_scheduling` → `frappe.only_for` called with both roles; kill switch off → `{"status": "blocked"}` and nothing enqueued; enqueues with `job_id="inter_weekly_payments"`, `deduplicate=True`, `queue="long"`, `timeout=1500`; `frappe.enqueue` returning `None` → `{"status": "already_queued"}`.
- [ ] **Step 2 — implement; remove the `TED` option from the dialog in `purchase_invoice.js`.**
- [ ] **Step 3 — ruff, tests green.**

---

### Task 6: Weekly scheduler unified with the order path; daily briefing section

**Files:** see table.

**Interfaces — Consumes:** `payment_guards.check_invoice_payable`, `is_integration_enabled`, `get_inter_account_for_company`, `NON_BLOCKING_STATUSES`; `payment_service.create_payment_order_for_invoice`, `enqueue_payment_execution`.

- [ ] **Step 1 — tests** (`test_planning_loop_payments.py`):
  - tripwire: `inspect.getsource(planning_loop)` contains none of `InterAPIClient`, `send_pix`, `pay_barcode`, `send_ted`.
  - kill switch off → nothing created; `auto_schedule_payments` off → nothing.
  - Pix invoice → `create_payment_order_for_invoice(name, "PIX", pix_key=…, scheduled_date=due_date)` **then** `enqueue_payment_execution(order)`; order returned `Pending Approval` → not enqueued, listed under "waiting for approval"; supplier without Pix key → error, nothing created.
  - Boleto invoice → `"Boleto Payment"`, `barcode`, `boleto_due_date = due_date`; missing barcode → error.
  - `check_invoice_payable` reason → `skipped` with the reason in the Telegram summary; a `Failed` order does not block (guard returns `None`) → a new order is created.
  - `create_payment_order_for_invoice` raising → error entry, loop continues with the next invoice; `rq.timeouts.JobTimeoutException` (import guarded; fall back to a local sentinel when `rq` is absent) is re-raised, not swallowed.
  - credit-card branch unchanged (draft entry + submit), account via `get_inter_account_for_company`.
  - the three SQL strings contain `NOT IN ('Failed', 'Cancelled')` and the `Completed`-with-entry exception; run each statement against an in-memory `sqlite3` schema (`%s` → `?`) to prove: `Failed` does not block, `Awaiting Bank` / `Needs Verification` / draft entry block, `Completed` with `payment_entry` does not block.
  - summary sections: queued · waiting for approval · credit card · skipped · errors.
- [ ] **Step 2 — implement** in `planning_loop.py`: remove the cache locks of `_schedule_single_payment` (keep the daily lock of `schedule_weekly_payments`), the client calls and `_create_payment_entry_draft` usage for Pix/Boleto.
- [ ] **Step 3 — daily briefing section** (tests first, in the existing briefing test module or a new `test_daily_briefing_payments.py`): lists `Needs Verification` (name, amount, days), `Awaiting Bank` > 24h with `bank_status`, `Failed` in the last 24h, `Completed` without Payment Entry, and `Draft`/`Pending Approval`/`Approved` idle > 24h; empty when there is nothing; never raises.
- [ ] **Step 4 — ruff, the planning-loop and briefing test files green.**

---

### Task 7: Patch, allowlist, docs, repo-wide tripwires

**Files:** see table.

- [ ] **Step 1 — patch tests** (`test_patch_flag_stuck_payment_orders.py`, with `FakeDB`): submitted `Processing` orders → `Needs Verification`; blocking orders get `invoice_lock` (first per invoice wins, the second is reported via `frappe.log_error`, no exception); `Failed`/`Cancelled`/`Completed`-with-entry stay NULL; a timeline comment lists the `codigoSolicitacao` values parsed from `Inter API Log.response_body` rows whose `request_body` mentions the order's invoice; running twice changes nothing; no `alert_operator` / Telegram call.
- [ ] **Step 2 — implement the patch; convert `patches.txt`** to:

```
[pre_model_sync]
brazil_module.patches.v1_0.migrate_from_old_apps
brazil_module.patches.v1_1.rename_model_tiers

[post_model_sync]
brazil_module.patches.v1_1.flag_stuck_payment_orders
```

- [ ] **Step 3 — `action_executor.ACTION_ALLOWLIST`**: remove `"Inter Payment Order": ["create"]`; adjust any test asserting the allowlist.
- [ ] **Step 4 — `test_payment_invariants.py`** (repo-wide, static): `.send_pix(`, `.pay_barcode(`, `.send_ted(` appear only in `payment_service.py` and `inter_client.py` under `brazil_module/` (tests excluded), and `.send_ted(` appears in neither outside its definition; `payment_service.py` contains no `.save(`; no `update_modified=False` in `payment_service.py` or the controller; `hooks.py` cron key `"0 * * * *"` still lists `scheduled_payment_status_check`.
- [ ] **Step 5 — `CLAUDE.md`**: replace `brazil/brazil/` with `brazil_module/` throughout and add a short "Outbound payments — invariants" section pointing to the spec (I1–I9 in one line each, and the `.venv/bin/python -m pytest` command).
- [ ] **Step 6 — ruff, the two new test files green.**

---

## Self-Review (done while writing)

- Spec coverage: §4.1 → T1; §4.2 → T2; §4.3 → T3; §4.4 → T4; §4.5 → T5; §4.6 → T6; §4.7 → T7; §5 is operator documentation (no task); §7's tests are distributed across the tasks above, with the repo-wide tripwires in T7.
- Names used across tasks match §3/§4: `enqueue_payment_execution`, `execute_payment_order`, `poll_bank_status`, `resolve_verification`, `create_payment_entry_for_order`, `create_payment_order_for_invoice`, `mark_*` with `expected_from`, `check_invoice_payable`, `find_blocking_payment_order`, `find_draft_payment_entry`, `is_integration_enabled`, `get_inter_account_for_company`, `alert_operator`.
- No file is owned by two tasks in the same wave.
