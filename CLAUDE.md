# ERPNext Brazil - Developer Guide

## Quick Commands

```bash
# Run all tests
python3 -m pytest brazil/brazil/tests/ -v

# Run a specific test file
python3 -m pytest brazil/brazil/tests/test_cnpj.py -v

# Run with short tracebacks
python3 -m pytest brazil/brazil/tests/ --tb=short

# Lint
ruff check brazil/
```

## Project Structure

This is a Frappe app (`brazil/`) with two modules:

- **Fiscal** (`brazil/fiscal/`) - NF-e, CT-e, NFS-e document management
- **Bancos** (`brazil/bancos/`) - Banco Inter banking integration

### Key Directories

| Path | Purpose |
|---|---|
| `brazil/brazil/services/fiscal/` | Fiscal service layer (XML parsing, SEFAZ client, processing pipeline) |
| `brazil/brazil/services/banking/` | Banking service layer (Inter API, boleto, PIX, reconciliation) |
| `brazil/brazil/utils/` | Pure utility functions (CNPJ, chave de acesso, formatters, QR code) |
| `brazil/brazil/fiscal/doctype/` | Fiscal DocType definitions (JSON + Python controllers) |
| `brazil/brazil/bancos/doctype/` | Banking DocType definitions |
| `brazil/brazil/setup/` | Installation hooks, custom field definitions, role creation |
| `brazil/brazil/api/` | Whitelisted API endpoints (webhook receiver, DANFE proxy) |
| `brazil/brazil/public/js/` | Client-side scripts (Sales Invoice, Purchase Invoice overrides) |
| `brazil/brazil/tests/` | Unit tests with XML fixtures |

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
from brazil.services.fiscal.some_module import SomeClass
```

### Important: Module-Level Imports

When a module does `from frappe.utils import flt`, the binding is captured at import time. Setting `frappe.utils.flt = float` in `setUp` won't affect it. Instead, patch the module directly:

```python
import brazil.services.fiscal.invoice_creator as _ic_mod
_ic_mod.flt = float
```

### Important: reset_mock() Does NOT Clear side_effect

`frappe.reset_mock()` does NOT reset `side_effect` or `return_value`. Always explicitly clear them:

```python
frappe.db.get_value.side_effect = None
```

### Test Isolation for Processor Tests

`test_processor.py` temporarily mocks service dependencies for import, then removes them from `sys.modules` so other test files get the real modules.

## Key Domain Concepts

- **Chave de Acesso** - 44-digit access key (NF-e/CT-e) or 50-digit (NFS-e), with mod-11 check digit
- **CNPJ** - 14-digit Brazilian company tax ID with two mod-11 check digits
- **NF-e** (modelo 55) - Product invoice; **CT-e** (modelo 57) - Transport document; **NFS-e** - Service invoice
- **SEFAZ DistDFeInt** - SOAP 1.2 API for fetching fiscal documents, uses mTLS (no XML signature), returns gzipped+base64 documents
- **NSU** (Numero Sequencial Unico) - Sequential number for tracking last fetched document from SEFAZ

## Frappe Framework Notes

- DocTypes are defined as JSON in `doctype/<name>/<name>.json`
- Hooks are in `brazil/hooks.py` - scheduled tasks, doc_events, custom fields
- `frappe.get_single()` returns singleton settings DocTypes
- `frappe.get_all()` with `pluck="name"` returns a flat list of strings, not dicts
- `frappe.enqueue()` runs background jobs via Redis queue
