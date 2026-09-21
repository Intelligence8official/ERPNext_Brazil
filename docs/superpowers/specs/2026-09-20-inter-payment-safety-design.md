# Inter outbound payments — one safe path to the bank

Date: 2026-09-20 · Status: approved scope (full fix of the Inter Payment Order path + weekly scheduler
unified with it) · Revision 2: folds in the four-lens design review (money-safety, Frappe v15 semantics,
Banco Inter API contract, scope/operations).

## 1. What happened

`IPO-2026-00001` (ACC-PINV-2026-00031, R$ 16.800,00, Pix) was executed once on 2026-03-30 and then
re-sent to Banco Inter **every hour** until the integration was switched off on 2026-04-22:
51 requests accepted (HTTP 200, `tipoRetorno: APROVACAO`, each with its own `codigoSolicitacao`),
388 rejected with `422 Limite excedido [PIXP30]`. The bank's daily Pix limit was the only brake.

Root cause (reproduced; verified against Frappe v15 source):

1. Every field of `Inter Payment Order` has `allow_on_submit = 0`. The order is always submitted
   when executed, so `order.save()` after the bank call raises `UpdateAfterSubmitError` —
   **after the bank already accepted the payment**.
2. The `except` branch does `order.status = "Failed"; order.save()` — same error, escapes the handler.
   The order stays `Processing`, `modified` frozen, no `transaction_id`, no Payment Entry.
3. `scheduled_payment_status_check` (hourly) re-calls `execute_payment_order` for any order
   `Processing` for more than 1h; the guard in `execute_payment_order` accepts `Processing`.
   It never asks the bank anything. One new POST per hour, per order, forever.

Aggravating defects found in the same investigation:

- `InterAPIClient._request_with_retry` blindly retries payment POSTs on read timeout, connection
  error and 500/502/503 (up to 4 sends per call) and never sends `x-id-idempotente`.
- Ambiguous outcomes are recorded as `Failed` ("did not happen"), inviting a re-send.
- The code sends `dataAgendamento`; the Pix request field is `dataPagamento`. The bank ignored it
  and treated every send as "pay today" (confirmed in production responses).
- HTTP 200 with `tipoRetorno: APROVACAO` is recorded as `Completed` and would settle the invoice
  although nothing was paid yet.
- No duplicate guard when creating or executing an order; `api.execute_payment` and
  `api.i8_run_payment_scheduling` have no state or role check; cancelling an in-flight order removes
  the only marker that protects the invoice.
- The weekly scheduler (`planning_loop.schedule_weekly_payments`) calls the bank directly and records
  the send only afterwards (draft Payment Entry, failure swallowed). In production it never reached
  the bank only because it filters `Inter Company Account` by a field that does not exist (`enabled`);
  fixing that filter alone would activate the defect.

## 2. Invariants

Every change below exists to make these hold. Reviewers check the code against them.

- **I1 — single path.** `client.send_pix` / `client.pay_barcode` are called only from
  `payment_service.execute_payment_order`. `send_ted` is called from nowhere (the API has no TED
  endpoint). Nothing else talks to the bank's outbound endpoints.
- **I2 — intent before send.** The claim (`status = Processing`, `idempotency_key`, `bank_request_at`)
  is committed *before* the HTTP request is issued.
- **I3 — no automatic re-send.** An order is sent at most once in its life: a claim is only possible
  from `Approved`, and nothing ever moves an order back to `Approved`. No scheduler, retry loop or
  handler sends again on its own.
- **I4 — unknown is not failed.** If the request may have reached the bank and the outcome is unknown,
  the order becomes `Needs Verification`. `Failed` means *the bank definitely does not hold this
  payment* (a response from an explicit allowlist of definitive rejections, or nothing was sent).
- **I5 — persistence cannot be vetoed.** After the claim, order state is written with
  `frappe.db.set_value(...)` + `frappe.db.commit()`. `Document.save()` is never called on a submitted
  order. Every state write updates `modified` (never `update_modified=False`): that is what makes
  Frappe's `check_if_latest` protect cancel and stale forms.
- **I6 — a blocking order protects its invoice, and the database enforces it.** An order is *blocking*
  unless it is `Failed`, `Cancelled`, or `Completed` with a Payment Entry. At most one blocking order
  per Purchase Invoice can exist (unique column `invoice_lock`). Orders that are `Processing`,
  `Awaiting Bank`, `Needs Verification` or `Completed` cannot be cancelled.
- **I7 — settle only what was paid.** The Payment Entry is created only when the bank reports the
  payment as effective (or the operator attests it with a date and a reference).
- **I8 — kill switch.** `Banco Inter Settings.enabled = 0` blocks every send (execution and weekly
  scheduler) and the status polling.
- **I9 — every transition is compare-and-set.** A state change re-reads the row with
  `for_update=True` and writes only when the current status is in the transition's `expected_from`.
  No whitelisted method trusts `self.status` (in `frm.call` the document is rebuilt from client JSON).

## 3. State machine

Statuses of `Inter Payment Order` (Select options, in this order):

`Draft`, `Pending Approval`, `Approved`, `Processing`, `Awaiting Bank` *(new)*,
`Needs Verification` *(new)*, `Completed`, `Failed`, `Cancelled`

```
Draft --submit--> Pending Approval --approve_payment (CAS)--> Approved
Draft --submit--> Approved                         (payment_approval_required = 0)
Approved --claim (row lock, committed)--> Processing
Processing --pre-send guard fails / nothing sent--> Failed
Processing --bank: definitive rejection-----------> Failed
Processing --bank accepted (2xx + bank id)--------> Awaiting Bank   (then one immediate poll)
Processing --outcome unknown / 2xx without id-----> Needs Verification
Processing --stale > 30 min (cron)----------------> Needs Verification
Awaiting Bank --poll: paid------------------------> Completed       (+ Payment Entry)
Awaiting Bank --poll: terminal rejection----------> Failed
Awaiting Bank --poll: FALHA / NAO_DEBITADO, or bank_request_at older than 85 days--> Needs Verification
Needs Verification --resolve(at_bank, ref)--------> Awaiting Bank | Completed | Failed   (by bank status of ref)
Needs Verification --resolve(paid, ref, paid_on)--> Completed       (+ Payment Entry)
Needs Verification --resolve(not_paid, note)------> Failed          (refused only while the bank still decides)
Draft | Pending Approval | Approved | Failed --cancel--> Cancelled
```

Constants (in `payment_guards.py`):

```python
IN_FLIGHT_STATUSES = ("Processing", "Awaiting Bank", "Needs Verification")
NON_BLOCKING_STATUSES = ("Failed", "Cancelled")
CANCELLABLE_STATUSES = ("Draft", "Pending Approval", "Approved", "Failed")
```

An order is **blocking** when `status not in NON_BLOCKING_STATUSES and not (status == "Completed" and payment_entry)`.
`invoice_lock == purchase_invoice` exactly while the order is blocking and has a `purchase_invoice`;
otherwise `invoice_lock` is NULL. A `Completed` order with a Payment Entry stops blocking because the
payment is then reflected in the invoice's `outstanding_amount` (this keeps partial payments and
installments possible).

### Bank status mapping

Pix — `POST /banking/v2/pix`: any 2xx **with** `codigoSolicitacao` → `Awaiting Bank`
(`bank_status` = `AGUARDANDO_APROVACAO` for `tipoRetorno` `APROVACAO`, otherwise the `tipoRetorno`
itself: `PROCESSADO`, `AGENDADO`, unknown values), followed by one immediate best-effort poll. The POST
response never completes an order: it carries no `endToEnd` and `PROCESSADO` is not proof of settlement.
2xx without `codigoSolicitacao` → `Needs Verification`.

Pix — `GET /banking/v2/pix/{codigoSolicitacao}` → `transacaoPix.status`
(`transaction_id` = `transacaoPix.endToEnd` when present):
- paid: `PAGO`, `PIX_PAGO`
- terminal rejection → `Failed`: `REPROVADO`, `EXPIRADO`, `CANCELADO`, `CANCELADO_SEM_SALDO`,
  `AGENDAMENTO_CANCELADO`
- needs a human → `Needs Verification`: `FALHA`, `NAO_DEBITADO` (not provably terminal; include
  `transacaoPix.erros` in the alert)
- anything else (`CRIADO`, `AGUARDANDO_APROVACAO`, `APROVADO`, `AGENDADO`, `ENVIADO`, `DEBITADO`,
  `PARCIALMENTE_*`, `TRANSACAO_*`, `PIX_ENVIADO`, unknown): still in flight — store in `bank_status`.

Boleto — `POST /banking/v2/pagamento` response and
`GET /banking/v2/pagamento?codigoTransacao=…` → `statusPagamento`:
- paid: `REALIZADO`, `PAGO`, `AGENDADO_REALIZADO`
- terminal rejection → `Failed`: `CANCELADO`, `AGENDADO_CANCELADO`, `ERRO`, `ERRO_PAGAMENTO`,
  `APROVACAO_EXPIRADA`, `REPROVADO`, `NAO_COMPENSADO`, `AGENDADO_NAO_REALIZADO`
- anything else: still in flight. 2xx without `codigoTransacao` → `Needs Verification`.

Boleto poll query: `codigoTransacao` + `filtrarDataPor=INCLUSAO` + `dataInicio = date(bank_request_at) − 1`
+ `dataFim = date(bank_request_at) + 1` (without dates the API only searches the last 30 days).

**A poll never produces `Failed` from absence**: an empty list, 404, 403 (missing read scope → alert the
operator), timeouts and any other poll error leave the order exactly as it is.

Unknown values are deliberately "in flight": the safe direction of failure is an order that keeps
protecting its invoice, never one that frees it.

## 4. Changes by component

### 4.1 `services/banking/payment_guards.py` and `payment_alerts.py` (new)

Read-only checks and constants shared by the controller, the service, the API and the scheduler.

```python
# payment_guards.py
DOCTYPE = "Inter Payment Order"
IN_FLIGHT_STATUSES, NON_BLOCKING_STATUSES, CANCELLABLE_STATUSES   # §3
AMOUNT_TOLERANCE = 0.01

def is_integration_enabled() -> bool                      # Banco Inter Settings.enabled
def is_blocking(status: str, payment_entry: str | None) -> bool
def find_blocking_payment_order(purchase_invoice: str, exclude: str | None = None) -> dict | None
    # {"name", "status"} of another blocking order with docstatus < 2
def find_draft_payment_entry(purchase_invoice: str, exclude_order: str | None = None) -> dict | None
    # {"name"} of a DRAFT Payment Entry (docstatus = 0) referencing the invoice, ignoring entries whose
    # inter_payment_order == exclude_order. Submitted entries are already reflected in outstanding_amount.
def check_invoice_payable(purchase_invoice: str, amount: float, order_name: str | None = None,
                          company: str | None = None) -> str | None
    # None when payable; otherwise a human-readable reason: invoice not submitted / on hold /
    # supplier on payment hold / company mismatch / outstanding_amount + tolerance < amount /
    # another blocking order / a draft Payment Entry exists
def get_inter_account_for_company(company: str) -> str | None
    # Inter Company Account with {"company": company, "sync_enabled": 1}

# payment_alerts.py
def alert_operator(subject: str, message: str, order_name: str | None = None) -> None
    # Error Log + Notification Log (bell) for users with role "Banco Inter Manager" + Telegram when
    # I8 Agent Settings.telegram_chat_id exists. Each channel in its own try/except. Never raises.
```

`brazil_module/tests/_payment_fakes.py` (shared test fakes, see §6) is created together with this package.

### 4.2 `services/banking/inter_client.py` (+ `auth_manager.py` for the 401 fix)

- `_request(..., retry_safe: bool | None = None, extra_headers: dict | None = None)`.
  Default `retry_safe = (method != "POST")`.
- Policy in `_request_with_retry` **for `retry_safe` requests**: unchanged (429 backoff, 5xx, timeouts,
  connection errors are retried), plus 504; the 401 branch refreshes the token correctly (today it
  passes a module-name string as `scopes`).
- Policy **for non-`retry_safe` requests** (payment POSTs) — no sleeping in the worker, one send:
  - `allow_redirects=False`.
  - Retried without risk: HTTP 401 on the first attempt (token refresh, once) and
    `requests.exceptions.ConnectTimeout` (caught **before** `ConnectionError`/`Timeout`, its base classes).
  - **Definitive** (raise `InterAPIError` with `status_code`): HTTP 400, 401 (after the refresh), 403, 404,
    405, 406, 415, 422, 429. The bank does not hold the operation.
  - **Everything else is ambiguous** → raise `InterAmbiguousResultError` immediately, no second send:
    read timeout, non-connect `ConnectionError`, `SSLError` (it can surface after the body was written),
    any 3xx, 408, 409 (Inter documents 409 on `/pagamento` as a generic internal error), any other 4xx,
    every 5xx.
- New exception `InterAmbiguousResultError(InterAPIError)`. `InterAPIError` gains `status_code: int | None`
  and `response_body`. Contract for callers: for a non-`retry_safe` request, every `InterAPIError` that is
  **not** `InterAmbiguousResultError` is definitive. (`InterAuthError` from the token step is raised before
  anything is sent and is not an `InterAPIError`.)
- Every attempt that ends a call without an HTTP response (ambiguous, timeout, connection error) writes
  an `Inter API Log` row (`success = 0`, `response_code = 0`). Today those leave no trace.
- `send_pix(payment_data: dict, idempotency_key: str) -> dict` — header `x-id-idempotente`; the key must
  match `[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}` (`str(uuid.uuid4())`), else
  `ValueError` before any request. It stays non-`retry_safe`: the key is a second line of defence, not a
  licence to retry (the bank's key retention is undocumented).
- `pay_barcode(payment_data)` — unchanged signature, non-`retry_safe`.
- `get_pix_payment(codigo_solicitacao: str) -> dict` (rename of the misleading `e2e_id` parameter).
- New `find_barcode_payments(*, barcode=None, codigo_transacao=None, start_date=None, end_date=None,
  filter_date_by="INCLUSAO") -> list[dict]` → `GET /banking/v2/pagamento`.
- `test_inter_client_banking.py` becomes order-independent: it loads the **real** `requests.exceptions`
  even when another test module left a `MagicMock` in `sys.modules["requests"]`, and patches the client
  module's `requests` per test. It must pass alone, in the full suite, and before/after
  `test_auth_manager.py`.

### 4.3 DocType `Inter Payment Order`

JSON:
- `status` options as in §3 (default `Draft`).
- New read-only, `no_copy` fields: `idempotency_key` (Data), `bank_status` (Data),
  `bank_request_at` (Datetime) in the result section; `invoice_lock` (Data, `unique`, hidden).
- `no_copy: 1` on `status`, `transaction_id`, `approval_code`, `execution_date`, `inter_response`,
  `payment_entry` (covers *Duplicate*; **Amend ignores `no_copy` in Frappe v15**, see `before_insert`).

Controller:
- `before_insert()`: force `status = "Draft"` and clear `idempotency_key`, `bank_status`,
  `bank_request_at`, `transaction_id`, `approval_code`, `execution_date`, `inter_response`,
  `payment_entry`. An amended, duplicated or agent-created order always starts clean.
- `validate()`: existing checks, plus — `payment_type == "TED"` is rejected ("Banco Inter's Banking API
  has no TED endpoint"); `Boleto Payment` requires `boleto_due_date` and normalises `barcode` to digits;
  when `purchase_invoice` is set: throw if `check_invoice_payable(...)` returns a reason, then set
  `invoice_lock = purchase_invoice`. A unique-key violation on `invoice_lock` is the race backstop.
- `approve_payment()`: `frappe.only_for(("Banco Inter Manager", "System Manager"))`; CAS
  `Pending Approval → Approved`.
- `execute_payment()`: `check_permission("submit")`; re-read status from the DB (must be `Approved`);
  kill switch; `payment_service.enqueue_payment_execution(name)`; **does not write status**; tells the
  user when the execution was already queued/running.
- `before_cancel()`: read the status from the DB with `for_update=True`; throw unless it is in
  `CANCELLABLE_STATUSES`. `on_cancel()`: `status = Cancelled`, `invoice_lock = NULL`.
- `check_bank_status()`: `check_permission("write")`; delegates to `payment_service.poll_bank_status`.
- `resolve_verification(outcome, bank_reference="", paid_on=None, note="")`:
  `frappe.only_for((...))`; delegates to `payment_service.resolve_verification`.
- `create_payment_entry()`: `frappe.only_for((...))`; delegates to
  `payment_service.create_payment_entry_for_order` (for a `Completed` order whose entry failed).

JS: buttons per status (*Execute Payment*, *Check Bank Status*, *Resolve Verification* — a dialog with
the three outcomes; `not_paid` shows three explicit confirmations: not in the statement, not in the
bank's approval queue, not in the bank's scheduled payments — and *Create Payment Entry*); every button
does `frm.reload_doc()` before calling; indicator colours for the new statuses; `purchase_invoice(frm)`
fills `amount` from `outstanding_amount`. `inter_payment_order_list.js` shows the status indicator in
the list view.

### 4.4 `services/banking/payment_service.py` (+ `reconciliation.py` hook, `hooks.py`)

```python
def enqueue_payment_execution(payment_order_name: str) -> bool
    # frappe.enqueue(execute_payment_order, queue="short", timeout=600,
    #   job_id=f"inter_payment_order::{name}", deduplicate=True,
    #   payment_order_name=name, requested_at=str(now_datetime()))  -> False when already queued/running
def execute_payment_order(payment_order_name: str, requested_at: str | None = None) -> dict
    # {"status": "awaiting_bank" | "completed" | "needs_verification" | "failed" | "skipped" | "blocked", ...}
def poll_bank_status(payment_order_name: str) -> dict
def resolve_verification(payment_order_name: str, outcome: str, *, bank_reference: str = "",
                         paid_on=None, note: str = "") -> dict
def create_payment_entry_for_order(payment_order_name: str, paid_on=None) -> str | None
def create_payment_order_for_invoice(invoice_name: str, payment_type: str, *, pix_key: str = "",
        barcode: str = "", scheduled_date=None, boleto_due_date=None, submit: bool = True) -> str
def scheduled_payment_status_check() -> None
def on_payment_entry_cancel(doc, method=None) -> None      # doc_event hook

# compare-and-set core — every one returns True only when it wrote
def mark_awaiting_bank(name, *, expected_from, bank_id, bank_status, response=None) -> bool
def mark_completed(name, *, expected_from, transaction_id="", bank_status="", response=None, paid_on=None) -> bool
def mark_failed(name, reason, *, expected_from, bank_status="", response=None) -> bool
def mark_needs_verification(name, reason, *, expected_from) -> bool
```

`mark_*`: lock the row (`get_value(..., ["status", "docstatus", "payment_entry"], for_update=True)`);
if the status is not in `expected_from` → rollback, log, return `False`, write nothing. Otherwise
`set_value` + **commit** (the safe state first); then timeline comment, alert and realtime
`notify_update`, each best-effort, followed by a **second commit** so nothing is left pending if the
caller re-raises. `mark_failed` also sets `invoice_lock = NULL`. `mark_completed` then calls
`create_payment_entry_for_order`. `mark_needs_verification` and non-interactive `mark_failed` call
`alert_operator`.

`expected_from` per caller: job result → `("Processing",)`; cron stale → `("Processing",)`;
poll → `("Awaiting Bank",)`; resolve → `("Needs Verification",)`.

`execute_payment_order`:
1. Kill switch off → `{"status": "blocked"}`. `requested_at` older than 15 minutes → `{"status": "skipped"}`
   + alert (workers were down; the operator may have paid elsewhere — he clicks again if he still wants it).
   No state change in either case.
2. **Claim**: `get_value(DOCTYPE, name, ["status", "docstatus", "idempotency_key", ...], for_update=True)`
   — reading a new column here makes an un-migrated schema fail *before* anything is sent. Unless
   `docstatus == 1 and status == "Approved"` → rollback, `{"status": "skipped"}` (second job, double click,
   order already in flight all end here). Then `set_value({status: "Processing",
   idempotency_key: str(uuid4()), bank_request_at: now, + cleared result fields})` and **commit**.
3. Pre-send guards — a failure here is `mark_failed` (nothing was sent): `check_invoice_payable(...)`,
   TED, missing Inter account, client construction, payload building.
   Payload: Pix `valor` as a number rounded to 2 places, `descricao` ≤ 140 chars, `dataPagamento` only
   when `scheduled_date` is after today; boleto `valorPagar` as a `"%.2f"` string, `dataVencimento`
   mandatory, `dataPagamento` only when `scheduled_date` is after today. `dataAgendamento` is never sent.
4. Send. `InterAmbiguousResultError` → `mark_needs_verification`. Any other `InterAPIError` /
   `InterAuthError` → `mark_failed` (definitive by the client contract; for HTTP 406 "título já
   liquidado" the message tells the operator the bank says the bill is already paid). **Any other
   exception** (including RQ's `JobTimeoutException`) → `mark_needs_verification`, then re-raise.
5. 2xx → mapping of §3 (`mark_awaiting_bank`, then one best-effort `poll_bank_status`; boleto may go
   straight to `mark_completed` / `mark_failed`).

`poll_bank_status`: needs `approval_code` (the bank id); without one → `{"status": "no_bank_id"}`.
Applies the §3 mapping with `expected_from=("Awaiting Bank",)`; poll errors never change state.

`resolve_verification` (only from `Needs Verification`):
- `at_bank`: `bank_reference` required; validated with a GET (`get_pix_payment` / `find_barcode_payments`);
  refused if another order already carries that id; sets `approval_code` and applies the §3 mapping.
- `paid`: `bank_reference` and `paid_on` required → `mark_completed(..., paid_on=paid_on)`.
- `not_paid`: `note` required → `mark_failed`. Refused only while *the bank still decides*: it has
  a bank id, its last answer was not one of the inconclusive ones (`FALHA`, `NAO_DEBITADO`), and the
  request is younger than the 90 days after which the bank stops answering. Without those two
  exceptions the order is a dead end — the statuses that send it to a human all carry a bank id, and
  `at_bank` would only map the same answer to the same state while the invoice stays locked.

`create_payment_entry_for_order` (idempotent; never changes the order status): adopt an existing
Payment Entry with `inter_payment_order == name` (submitted → link it; draft → submit it); if the invoice
no longer has enough outstanding → do not create, comment on the timeline; otherwise build it from the
**invoice** (party, `paid_to = credit_to`, `allocated_amount = min(amount, outstanding)`,
`posting_date = reference_date = paid_on or execution date`), insert + submit. On any exception:
`frappe.db.rollback()` first, then log + one `alert_operator`. On success set `payment_entry` and
`invoice_lock = NULL`. **There is no automatic retry** — a failed entry is almost always deterministic
(closed period, frozen account); the operator gets one alert, the daily briefing line and the
*Create Payment Entry* button.

`on_payment_entry_cancel` (new `doc_events` entry in `hooks.py`): when the cancelled entry has
`inter_payment_order`, clear the order's `payment_entry` (so Frappe's back-link check lets the entry be
cancelled) and restore `invoice_lock`; comment on the timeline. `reconciliation.on_payment_entry_submit`
stops writing `status` (a second, unguarded writer today); it only links `payment_entry` when empty.

`scheduled_payment_status_check` (same hook name, new meaning — **it never sends**):
- kill switch;
- `Awaiting Bank` orders (`docstatus = 1`) → `poll_bank_status`, at most 15 per run (log what was capped);
  older than 85 days since `bank_request_at` → `mark_needs_verification` (GET /pix only covers 90 days);
- `Processing` orders with `bank_request_at IS NULL OR bank_request_at < now − 30 min` (fallback on
  `modified`) → `mark_needs_verification`.

### 4.5 `api/__init__.py` and `public/js/purchase_invoice.js`

- `create_payment_order`: when `purchase_invoice` is given, delegates to
  `create_payment_order_for_invoice(..., submit=False)` (account via `get_inter_account_for_company`,
  `boleto_due_date` defaults to the invoice `due_date`, party forced from the invoice); guard failures
  surface as the returned error message.
- `execute_payment`: loads the doc and delegates to `doc.execute_payment()` (permission + state inside).
- `i8_run_payment_scheduling`: `frappe.only_for(("Banco Inter Manager", "System Manager"))`, kill switch,
  `job_id="inter_weekly_payments"`, `deduplicate=True`, `queue="long"`, `timeout=1500`.
- The *Pay via Inter* dialog loses the `TED` option.

### 4.6 `services/intelligence/recurring/planning_loop.py` and `daily_briefing.py`

- `schedule_weekly_payments`: also requires `is_integration_enabled()`.
- `_schedule_single_payment`: both pre-checks (the Payment Entry SQL and the status-blind
  `frappe.db.exists("Inter Payment Order", …)` — a **fourth** guard that today lets a `Failed` order block
  forever) are replaced by `check_invoice_payable(...)`. The per-invoice cache lock is removed (the
  database guard replaces it).
- `_schedule_pix_payment` / `_schedule_boleto_payment`: no client, no draft Payment Entry. They call
  `create_payment_order_for_invoice(...)` (Pix key from the supplier; barcode and `boleto_due_date` from
  the invoice; `scheduled_date = due_date`). If the order comes out `Approved` they call
  `enqueue_payment_execution(name)` — **the scheduler never talks to the bank**; each order runs as its
  own job and reports through its own alerts.
- Telegram summary sections: queued for execution · waiting for approval in the ERP · credit card ·
  skipped (invoice + reason) · errors.
- The three SQL `NOT EXISTS` guards (weekly selection, overdue alert, urgent alert) use the blocking
  rule: ignore orders in `NON_BLOCKING_STATUSES` and `Completed` orders with a Payment Entry. A failed
  order must neither block a new attempt nor silence the alert.
- `_create_payment_entry_draft` stays for the credit-card branch only; its account lookup uses
  `get_inter_account_for_company`.
- `daily_briefing`: a section that repeats every day what a single alert cannot: orders in
  `Needs Verification` (name, amount, days), `Awaiting Bank` for more than 24h (with `bank_status`),
  `Failed` in the last 24h, `Completed` without Payment Entry, and `Draft` / `Pending Approval` /
  `Approved` orders idle for more than 24h (they block their invoice too).

### 4.7 Patch, allowlist, docs

- `patches.txt` moves to the sectioned format: `[pre_model_sync]` with the two existing lines (Patch Log is
  by module name — they do not re-run) and `[post_model_sync]` with
  `brazil_module.patches.v1_1.flag_stuck_payment_orders`. Without sections Frappe treats every patch as
  pre-model-sync, i.e. before the new columns exist.
- The patch (plain `frappe.db.set_value`, no `mark_*`, no external alert, idempotent): every submitted
  order still `Processing` becomes `Needs Verification`; every blocking order gets its `invoice_lock`
  (first one per invoice wins, others are reported); each flagged order gets a timeline comment listing
  the `codigoSolicitacao` values found in `Inter API Log` for it, with the instruction to check the bank
  statement and the bank's approval queue (pending Pix requests cannot be cancelled by API; unapproved
  ones expire).
- `action_executor.ACTION_ALLOWLIST`: remove the latent, unused `"Inter Payment Order": ["create"]`.
- `CLAUDE.md`: fix the outdated `brazil/brazil/` paths and document the payment invariants.

## 5. Deploy and rollback

Precondition: `Banco Inter Settings.enabled = 0`, and it stays 0 until step 7. **Never re-enable with the
old code on disk**: `IPO-2026-00001` is still `Processing` and the old cron would send it again at the
next full hour.

1. Backup. 2. `git pull`. 3. `bench --site <site> migrate`. 4. `bench build --app brazil_module` and
`bench restart` (web, workers, scheduler).
5. Console checks: `IPO-2026-00001` is `Needs Verification`;
`frappe.db.has_column("Inter Payment Order", "idempotency_key")`; no order is `Processing`.
6. Resolve `IPO-2026-00001` from the bank statement (count debits of R$ 16.800,00 between 2026-03-30 and
2026-04-22; more than one → the excess is a credit to recover from the supplier). The Pix query endpoint
only covers 90 days, so this one is resolved with `paid` + date + reference, or `not_paid` + note.
7. First week: `payment_approval_required = 1` or `auto_schedule_payments = 0`; obtain a token and confirm
the granted `scope` includes `pagamento-pix.read` and `pagamento-boleto.read` (polling depends on them);
run one small manual payment end to end; only then `enabled = 1`.

Rollback: `enabled = 0` **first**; confirm no order is `Processing`; revert the code; the new columns stay.

## 6. Out of scope (noted, not changed)

- Removing the `TED` payment type (it is now rejected in `validate()`).
- `pix-pagamento` / boleto payment webhooks: polling is enough for now.
- Re-sending a `Needs Verification` Pix with the same idempotency key, and looking payments up by barcode
  or in the statement to resolve ambiguity automatically: the bank's behaviour is undocumented and cannot
  be tested here. A human resolves those, with the bank id when there is one.
- Weekend/holiday adjustment of `dataPagamento`; `cpfCnpjBeneficiario` validation for boletos.
- `expense_scheduler.daily_check` firing the same event on consecutive days (duplicate *payables*, not
  duplicate sends) and `banking_tools._has_existing_payment` (it errs on the side of blocking).

## 7. Testing

Unit tests with the project's `sys.modules["frappe"]` mock pattern — but a bare `MagicMock` makes the
important regressions vacuous (`MagicMock().save()` never raises; `get_value()` returns a `MagicMock`, so
every claim is "skipped"). Shared fakes in `brazil_module/tests/_payment_fakes.py`:

- `FakeDB`: `get_value / set_value / get_single_value / exists / commit / rollback` over a dict of rows,
  with a separate *committed* copy, a record of `for_update`, and an ordered `events` list.
- `FakeInterClient`: `send_pix / pay_barcode / get_pix_payment / find_barcode_payments` append to the
  same `events`; at send time it asserts the **committed** row already has `status == "Processing"`,
  `idempotency_key` and `bank_request_at` (I2).
- `FakeSubmittedDoc`: `save()` raises `UpdateAfterSubmitError` when `docstatus == 1`, driven by
  `allow_on_submit` read from the real DocType JSON.

Regression tests that matter most:

- positive control: `Approved` → exactly one send; a second `execute_payment_order` on the same `FakeDB` →
  zero sends;
- cron matrix (every status × payment type): `send_*` is never called;
- `JobTimeoutException` / `RuntimeError` during the send → `Needs Verification` + re-raise;
- static tripwires: `".save("` does not appear in `payment_service.py`; `.send_pix(` / `.pay_barcode(` /
  `.send_ted(` appear only in `payment_service.py` and `inter_client.py` (I1);
- JSON contract: `status` options equal the §3 list in order; `IN_FLIGHT ∪ NON_BLOCKING ∪ CANCELLABLE ⊂`
  options; new fields are `read_only` + `no_copy`; `invoice_lock` is `unique`;
- client, with the real `requests.exceptions`: read timeout, `ConnectionError("Connection aborted")`,
  `SSLError`, 302, 409 and 500/502/503/504 on a payment POST → exactly **one** `requests.request` call and
  `InterAmbiguousResultError`; `ConnectTimeout` retries; 422 and 429 → `InterAPIError`, not ambiguous, one
  call; 401 → refresh + one retry; an invalid idempotency key → `ValueError` and zero calls;
- CAS: `mark_failed` on a `Completed` order writes nothing; `approve_payment` with a forged `status` in the
  document does not touch an order that is not `Pending Approval` in the DB; cancel of an in-flight or
  `Completed` order is refused; an inserted document with result fields filled comes out clean;
- poll: `[]`, 404 and 403 leave the state unchanged;
- weekly scheduler: the order exists before anything is enqueued, nothing in `planning_loop.py` touches the
  client, a `Failed` order does not block the next run, skipped invoices appear in the summary.

Real lock concurrency can only be verified on a bench (two consoles) — a manual staging step.
