# ERPNext Brazil - Developer Guide

## Quick Commands

```bash
# Run all tests (pytest lives in the project venv; the system python has none)
.venv/bin/python -m pytest brazil_module/tests/ -v

# Run a specific test file
.venv/bin/python -m pytest brazil_module/tests/test_cnpj.py -v

# Run with short tracebacks
.venv/bin/python -m pytest brazil_module/tests/ --tb=short

# Lint
ruff check brazil_module/
```

## Project Structure

This is a Frappe app (package `brazil_module/`). The two modules this guide covers:

- **Fiscal** (`brazil_module/fiscal/`) - NF-e, CT-e, NFS-e document management
- **Bancos** (`brazil_module/bancos/`) - Banco Inter banking integration

### Key Directories

| Path | Purpose |
|---|---|
| `brazil_module/services/fiscal/` | Fiscal service layer (XML parsing, SEFAZ client, processing pipeline) |
| `brazil_module/services/banking/` | Banking service layer (Inter API, boleto, PIX, reconciliation) |
| `brazil_module/services/intelligence/llm/` | Provider layer for the AI agent: one contract, three adapters (Anthropic, Google, OpenAI), pricing and cost |
| `brazil_module/utils/` | Pure utility functions (CNPJ, chave de acesso, formatters, QR code) |
| `brazil_module/fiscal/doctype/` | Fiscal DocType definitions (JSON + Python controllers) |
| `brazil_module/bancos/doctype/` | Banking DocType definitions |
| `brazil_module/setup/` | Installation hooks, custom field definitions, role creation |
| `brazil_module/api/` | Whitelisted API endpoints (webhook receiver, DANFE proxy) |
| `brazil_module/public/js/` | Client-side scripts (Sales Invoice, Purchase Invoice overrides) |
| `brazil_module/tests/` | Unit tests with XML fixtures |
| `brazil_module/patches/` | Migration patches, listed in `brazil_module/patches.txt` (`[pre_model_sync]` / `[post_model_sync]`) |

### Service Layer Architecture

The fiscal processing pipeline (`processor.py`) orchestrates:
1. **XML Parsing** (`xml_parser.py`) - Detects document type, extracts all fields
2. **Supplier Processing** (`supplier_manager.py`) - 5-strategy CNPJ-based search + auto-create
3. **Item Processing** (`item_manager.py`) - 4-strategy item matching + auto-create
4. **PO Matching** (`po_matcher.py`) - Scoring algorithm (value 30pts, items 60pts, date 10pts)
5. **Invoice Creation** (`invoice_creator.py`) - 3-strategy duplicate detection + creation

The banking module follows a similar service pattern with `inter_client.py` as the core HTTP client (mTLS + OAuth2), and domain-specific services for boleto, PIX, payments, and reconciliation.

## Testing Patterns

Tests use `unittest` with `MagicMock` for Frappe framework mocking.

### Mocking Pattern

Since modules import `frappe` at module level, tests must inject the mock into `sys.modules` **before** importing the module under test:

```python
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    frappe_mock._ = lambda x: x
    sys.modules["frappe"] = frappe_mock
    sys.modules["frappe.utils"] = frappe_mock.utils

import frappe
from brazil_module.services.fiscal.some_module import SomeClass
```

### Important: Module-Level Imports

When a module does `from frappe.utils import flt`, the binding is captured at import time. Setting `frappe.utils.flt = float` in `setUp` won't affect it. Instead, patch the module directly:

```python
import brazil_module.services.fiscal.invoice_creator as _ic_mod
_ic_mod.flt = float
```

### Important: reset_mock() Does NOT Clear side_effect

`frappe.reset_mock()` does NOT reset `side_effect` or `return_value`. Always explicitly clear them:

```python
frappe.db.get_value.side_effect = None
```

### Test Isolation for Processor Tests

`test_processor.py` temporarily mocks service dependencies for import, then removes them from `sys.modules` so other test files get the real modules.

### Payment tests need state, not a bare MagicMock

With a bare `MagicMock` frappe, `save()` never raises and `frappe.db.get_value()` returns a mock, so
every claim is "skipped" and no payment test can fail. Use the shared fakes in
`brazil_module/tests/_payment_fakes.py` (`FakeDB`, `FakeInterClient`, `FakeSubmittedDoc`,
`install_frappe_mock`, `patch_frappe_db`) wherever order state matters. Never
`patch.object(frappe, "db", ...)` on the shared mock - use `patch_frappe` / `patch_frappe_db`.

## Outbound payments - invariants

In production the same R$ 16.800,00 Pix was re-sent to Banco Inter every hour for three weeks. Every
change to `Inter Payment Order`, `services/banking/payment_service.py`, `payment_guards.py`,
`inter_client.py` or anything that schedules payments must keep these true. The design, the state
machine and the bank status mapping are in
`docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md` - read it before touching that code.

- **I1 - single path.** `send_pix` / `pay_barcode` are called only from `payment_service.execute_payment_order`; `send_ted` from nowhere.
- **I2 - intent before send.** The claim (`Processing`, `idempotency_key`, `bank_request_at`) is committed before the HTTP request.
- **I3 - no automatic re-send.** A claim is only possible from `Approved` and nothing moves an order back there; no cron, retry loop or handler sends again. Payment POSTs are never retried by the client.
- **I4 - unknown is not failed.** An outcome that may have reached the bank is `Needs Verification`; `Failed` means the bank definitely does not hold the payment.
- **I5 - persistence cannot be vetoed.** After the claim, state is written with `frappe.db.set_value` + `frappe.db.commit()`; never `Document.save()` on a submitted order, never `update_modified=False`.
- **I6 - a blocking order protects its invoice.** One blocking order per Purchase Invoice, enforced by the unique column `invoice_lock`; in-flight and `Completed` orders cannot be cancelled.
- **I7 - settle only what was paid.** The Payment Entry exists only once the bank reports the payment as effective (or the operator attests it).
- **I8 - kill switch.** `Banco Inter Settings.enabled = 0` blocks every send, the weekly scheduler and the polling.
- **I9 - every transition is compare-and-set.** Re-read the row with `for_update=True` and write only from the expected statuses; never trust `self.status` in a whitelisted method.

```bash
# The payment suite, including the repo-wide static tripwires (test_payment_invariants.py)
.venv/bin/python -m pytest brazil_module/tests/test_payment_invariants.py brazil_module/tests/test_payment_service.py \
  brazil_module/tests/test_payment_guards.py brazil_module/tests/test_inter_payment_order.py \
  brazil_module/tests/test_inter_client_banking.py brazil_module/tests/test_patch_flag_stuck_payment_orders.py -q
```

Tests never call Banco Inter or any network endpoint. Real row-lock concurrency can only be checked on
a bench (two consoles); deploy and rollback steps are in section 5 of the spec.

## Key Domain Concepts

- **Chave de Acesso** - 44-digit access key (NF-e/CT-e) or 50-digit (NFS-e), with mod-11 check digit
- **CNPJ** - 14-digit Brazilian company tax ID with two mod-11 check digits
- **NF-e** (modelo 55) - Product invoice; **CT-e** (modelo 57) - Transport document; **NFS-e** - Service invoice
- **SEFAZ DistDFeInt** - SOAP 1.2 API for fetching fiscal documents, uses mTLS (no XML signature), returns gzipped+base64 documents
- **NSU** (Numero Sequencial Unico) - Sequential number for tracking last fetched document from SEFAZ

## Frappe Framework Notes

- DocTypes are defined as JSON in `doctype/<name>/<name>.json`
- Hooks are in `brazil_module/hooks.py` - scheduled tasks, doc_events, custom fields
- `frappe.get_single()` returns singleton settings DocTypes
- `frappe.get_all()` with `pluck="name"` returns a flat list of strings, not dicts
- `frappe.enqueue()` runs background jobs via Redis queue
