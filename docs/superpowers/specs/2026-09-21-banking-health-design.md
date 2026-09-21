# Is the bank still talking to us?

Date: 2026-09-21 · Status: approved (read-only monitor; briefing daily, Telegram on change and weekly)

## 1. Why

`Banco Inter Settings.enabled` has been `0` since 2026-04-22, switched off by hand after the owner
noticed — by himself — that the same payment was being re-sent every hour. Five months later nothing
in the system has mentioned it. Seventeen places read that flag and every one of them returns in
silence.

The silence is not the worst part. The daily briefing has kept reporting normality produced by a
dead channel:

- `_bank_balance_section` (`daily_briefing.py:164`) prints `current_balance` with `balance_date`
  beside it and never compares that date to today, so an April balance reads as the current one.
- `_reconciliation_status_section` (`daily_briefing.py:602`) counts every `Bank Transaction` ever
  recorded. With no new statement since April the old ones are all reconciled, so it prints
  **"Conciliacao Bancaria: Em dia (100% conciliado)"**.

Meanwhile `check_urgent_payments` still sends "URGENTE: N pagamentos vencem hoje" every day: with no
integration, no `Inter Payment Order` is ever created, so the `NOT EXISTS` guard matches everything.
The owner is billed daily by the symptom and was never told the cause.

The data to notice all of this has been there the whole time. `Inter API Log` records every call
with its HTTP code, `success`, error text and duration, kept for 90 days. `Inter Company Account`
already stores `certificate_expiry`, read from the real x509. Nothing reads any of it: the only
consumers of `Inter API Log` are the cleanup job that deletes it and a migration patch.

## 2. What this is, and what it is not

A read-only watchman for the *channel*, not for individual payments. Payment-level alerting already
exists: `payment_alerts.alert_operator` is called from nine points in `payment_service.py`, and
`daily_briefing._payment_orders_section` repeats every morning what is still pending. That layer
answers "is this order stuck?". This one answers "is the bank still talking to us at all?".

**It never writes to Banco Inter.** The precedent it copies, `telegram_health.scheduled_check`,
re-registers the webhook by itself when it finds it wrong. The banking equivalent would be an
unattended job writing to the bank at night, which is the shape of the incident this whole branch
exists to fix. When this monitor finds something fixable it says what to do and leaves the button;
a human clicks it.

Decisions taken, so they are not re-litigated later:

- **D1 — read-only.** The one call it makes to the bank is `GET` the webhook registration. No
  writes, ever.
- **D2 — noise.** A line in the daily briefing every day while anything is wrong; Telegram only when
  the state *changes*, plus a weekly reminder while it stays wrong. Over the five idle months that
  is roughly 22 Telegram messages instead of 0 or 150.
- **D3 — "disabled" is the news.** `telegram_health` stays quiet while the agent is off, reasoning
  that warning about a channel nobody turned on is noise. Here the rule is inverted: `enabled = 0`
  is precisely the state that lasted five months unannounced, so it is reported like any other
  problem.
- **D4 — no new history table.** `Inter Sync Log` is already written and never read; adding another
  such table repeats the defect. The verdict lives in `Banco Inter Settings`, where the form shows
  it, exactly as the Telegram verdict does.

## 3. Known limit, stated rather than pretended

**A dead scheduler cannot be detected from inside.** If Frappe's scheduler stops, this check stops
with it, and so does the briefing that would carry its verdict. What *is* detectable is this job
running while another job is late, which is the `jobs` check below. The total-silence case
needs something outside the site (an uptime ping, a cron on the host) and is out of scope here.

Two smaller limits, for the same reason of honesty:

- `certificate_expiry` is recomputed only when someone saves the `Inter Company Account`
  (`inter_company_account.py:24-71`). This monitor reads the stored value. A certificate replaced on
  disk without saving the document shows the old date. Re-reading the x509 here would duplicate the
  controller's logic; the check instead reports a missing date as a problem of its own.
- The webhook check needs a working token. When the credential itself is the problem, that check
  reports "could not ask" rather than "not registered", and the credential problem is what is shown.

## 4. The module

`brazil_module/services/banking/banking_health.py`, around 300 lines, no writes to the bank.

```python
CHECKS = (_integration_enabled, _accounts, _certificates, _statement_sync,
          _api_errors, _pix_limit, _jobs, _webhook)          # order = order shown

def check(record: bool = True) -> dict
    # {"healthy": bool, "state": str, "summary": str, "problems": [dict], "checks": [dict]}

def scheduled_check() -> None                 # daily, decides whether to interrupt
def status() -> dict                          # the last recorded verdict, for the briefing
```

Each check is a function of no arguments returning one dict:

```python
{"name": "statement_sync",           # stable key, never translated
 "healthy": False,
 "problem": "O extrato nao sincroniza ha 152 dias",   # what the operator reads
 "fixable_by": "Reconectar a integracao em Banco Inter Settings"}   # or "" when there is nothing to do
```

`check()` runs all of them, each in its own `try` — a check that raises becomes a problem named
after itself (`"A verificacao <nome> falhou: ..."`) rather than taking the run down. It never
raises: it answers a button as well as a job.

`state` is the stable key used for change detection: `"ok"`, or the sorted names of the unhealthy
checks joined by commas (`"certificates,integration_enabled"`). **Change detection must not use the
prose**, which contains day counts that grow daily and would send a Telegram message every morning.

### The checks

| name | unhealthy when | reads |
|---|---|---|
| `integration_enabled` | `Banco Inter Settings.enabled` is falsy | the Single |
| `accounts` | no `Inter Company Account` with `sync_enabled = 1` | doctype |
| `certificates` | for any enabled account: `certificate_valid = 0`, `certificate_expiry` empty, in the past, or within `CERT_WARNING_DAYS = 30` | doctype |
| `statement_sync` | the newest `last_statement_sync` among enabled accounts is older than `SYNC_STALE_DAYS = 2`, or never set | doctype |
| `api_errors` | in the last 24h, `Inter API Log` has at least `MIN_CALLS = 3` rows and the failure rate is at least `ERROR_RATE = 0.5` | `Inter API Log` |
| `pix_limit` | in the last 24h, any `Inter API Log` row with `response_code = 422` whose `response_body` contains `PIXP30`; reported **aggregated** ("7 pagamentos recusados hoje por limite diario"), never one per row | `Inter API Log` |
| `jobs` | any of the banking `Scheduled Job Type` rows has `last_execution` older than `JOBS_LATE_FACTOR = 3` times its own interval, or has `stopped = 1` | `Scheduled Job Type` |
| `webhook` | `client.get_webhook()` returns an address that is not this site's `webhook_receiver`, or nothing | **one GET to the bank** |

`WATCHED_JOBS` holds the banking method paths and their expected interval in hours:
`payment_service.scheduled_payment_status_check` (1), `statement_sync.scheduled_statement_sync` (6),
`boleto_service.scheduled_boleto_status_check` (0.5), `pix_service.scheduled_pix_status_check`
(0.25).

Checks that depend on the integration being on (`statement_sync`, `api_errors`, `pix_limit`,
`webhook`) are **skipped** when `enabled` is falsy: with the integration off, a stale statement is a
consequence, not a second piece of news. `jobs` is **not** skipped — the framework stamps
`last_execution` whenever a job runs, whatever the method then decides, so a late job means the
scheduler stopped, which is news at any time. They appear in `checks` marked `"skipped": True` so the
form can show why.

### When it interrupts

Five new fields on `Banco Inter Settings`, mirroring `telegram_webhook_status` /
`telegram_webhook_checked_on`:

| field | type | holds |
|---|---|---|
| `banking_health_state` | Data, read-only | the stable key — change detection compares this |
| `banking_health_status` | Small Text, read-only | the sentence shown on the form |
| `banking_health_checked_on` | Datetime, read-only | when it last ran |
| `banking_health_since` | Datetime, read-only | when the current state began — the day count comes from here |
| `banking_health_alerted_on` | Datetime, read-only | when Telegram was last bothered |

`scheduled_check()`:

1. Run `check(record=True)`. `state`, `status` and `checked_on` are written on **every** run —
   `checked_on` is the honest "when it was last known to work" the form shows. `since` is written
   **only when `state` changed**, because it is what the day counts are measured from.
2. Interrupt when the state changed since the last run, or when `alerted_on` is older than
   `REMINDER_DAYS = 7`. Both cases include the recovery edge: going back to `ok` is a state change
   and is worth one message.
3. Interrupting means `payment_alerts.alert_operator(subject, message, order_name=None)` — already
   written and tested, already fans out to Error Log, desk bell and Telegram, already never raises.
   This module only decides *when*; `alert_operator` decides *where*. `alerted_on` is stamped after.

Writes use `frappe.db.set_single_value` inside a best-effort wrapper: a verdict that cannot be stored
must not turn a check into a failure.

## 5. What the operator sees

**Briefing** — a new `_banking_health_section` in `section_funcs` (`daily_briefing.py:132`), placed
immediately before `_bank_balance_section`, because whether the channel is alive decides how to read
the balance below it. It **reads the recorded verdict**; it does not re-run the checks. Returns `""`
when everything is healthy, so a working integration adds no noise. When something is wrong:

```
*Comunicacao bancaria:*
  DESLIGADA ha 152 dias — reconecte em Banco Inter Settings
  Certificado vence em 12 dias
  (verificado hoje as 03:00)
```

Like `_payment_orders_section`, a failure inside it is reported **in** the section rather than making
it disappear: a banking-health section that vanishes silently reads as "nothing to worry about".

**The two sections that lie today:**

- `_bank_balance_section`: when `balance_date` is not today, the line says how old it is —
  `Inter (I8): R$ 12.345,67 (saldo de 22/04, ha 152 dias)`. The balance is never printed as if it
  were current.
- `_reconciliation_status_section`: "Em dia (100% conciliado)" is only printed when a statement
  arrived recently. Otherwise it says there is no new statement and names the date of the last one.

**Form** — `Banco Inter Settings` shows the verdict and its date, and a *Verificar conexao* button
calling a new whitelisted `api.check_banking_health()`, which returns `banking_health.check()`.
Pattern and placement copy `i8_check_telegram_webhook` (`api/__init__.py:820`,
`i8_agent_settings.js:23`). The existing yellow warning in `banco_inter_settings.js:3-5` is fixed
along the way: it is overwritten by the Sandbox `set_intro` below it, because `set_intro` replaces
rather than accumulates.

**Schedule** — `hooks.py` `"daily"`, next to `telegram_health.scheduled_check`.

## 6. Testing

Unit tests with the fakes already in `brazil_module/tests/_payment_fakes.py`
(`FakeDB`, `FakeInterClient`, `patch_frappe`). New file `test_banking_health.py`.

- **Each check in isolation**, healthy and unhealthy, plus its edge: no accounts at all; a
  certificate expiring exactly on the 30th day; a statement synced exactly `SYNC_STALE_DAYS` ago;
  two failing calls (below `MIN_CALLS`) staying healthy; seven `PIXP30` rows producing **one**
  problem and not seven; a job stopped versus merely late.
- **Skipping**: with `enabled = 0`, the dependent checks do not run and do not reach the bank.
- **A check that raises** becomes a problem and the other checks still run.
- **The noise policy**, with a controlled clock: state change alerts; the next day does not; day 8
  does; returning to healthy alerts once and then stays quiet. `since` survives a day count changing
  while the state does not — the regression that would otherwise send a message every morning.
- **Read-only**, as a static tripwire in `test_payment_invariants.py`: `banking_health.py` names no
  write method of the client (`send_pix`, `pay_barcode`, `send_ted`, `register_webhook`,
  `create_boleto`, `cancel_boleto`, `create_pix_charge`) and contains no `set_value` against a
  doctype other than `Banco Inter Settings`.
- **The two corrected sections**: a balance dated today prints without an age; a balance dated 152
  days ago prints the age; with no recent statement the reconciliation line does not say "em dia".

Then a mutation pass over the checks and the noise policy, on a scratch copy, as was done for the
rest of this branch — including the baseline check that the scratch copy carries `CLAUDE.md` and
`docs/`, without which four unrelated tests fail and mask the result.

## 7. Out of scope

- Detecting a fully dead scheduler (section 3).
- Writing to the bank to repair anything, including re-registering the webhook (D1).
- `Banco Inter Settings.alert_email`, `send_error_alerts`, `send_payment_received_alerts`: dead
  fields no code reads. Either they become an e-mail channel or they leave the form — a separate
  decision.
- Reviving `Inter Sync Log` as a health history (D4).
- The daily "URGENTE" nag from `check_urgent_payments`: correct in its own terms, and the cause is
  what this spec addresses.
