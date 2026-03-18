# Brazil ERP

Fiscal documents (NF-e, CT-e, NFS-e) and Banking integration for ERPNext.

A [Frappe](https://frappeframework.com) app that adds Brazilian fiscal compliance and Banco Inter banking operations to [ERPNext](https://erpnext.com).

---

## Features

### Fiscal Module

- **DF-e Integration** — Automated fetching of NF-e, CT-e, and NFS-e from SEFAZ via the DF-e Distribution API, with rate-limit awareness and NSU tracking.
- **Email Monitoring** — Automatically detects and imports NF XML/PDF/ZIP attachments from incoming emails.
- **XML Parsing** — Full extraction of header, supplier, items, taxes, transport, and payment data from Brazilian fiscal XML.
- **Supplier Management** — Auto-creation and linking of suppliers by CNPJ, with state/municipal registration support.
- **Item Management** — Auto-creation and linking of items by NCM code, supplier item code, or past invoice history.
- **Purchase Order Matching** — Scoring algorithm that matches incoming NFs to open Purchase Orders by supplier, items, and value proximity.
- **Purchase Invoice Creation** — One-click or automatic creation of Purchase Invoices from parsed Nota Fiscal data, with duplicate detection.
- **NF-e Event Handling** — Tracks cancellation and correction events (Eventos) from SEFAZ.
- **NF Reconciliation Report** — Cross-references Notas Fiscais with Purchase Invoices to identify discrepancies.

### Banking Module (Banco Inter)

- **Multi-Company Support** — Configure separate Inter Company Accounts per company, each with its own mTLS certificate and OAuth2 credentials.
- **Boleto / BoletoPIX** — Issue boletos from Sales Invoices, download PDFs, track payment status, and auto-cancel expired boletos.
- **PIX Charges** — Create immediate or scheduled PIX charges with QR code generation, status polling, and payment confirmation.
- **Outbound Payments** — Execute PIX, TED, and boleto payments through Inter Payment Orders with approval workflow.
- **Bank Statement Sync** — Periodic import of bank statements (extrato) into ERPNext Bank Transactions with daily balance tracking.
- **Auto-Reconciliation** — Matches Bank Transactions to Sales Invoices, Purchase Invoices, Payment Entries, Journal Entries, and Expense Claims.
- **Webhooks** — Receives real-time payment notifications for boletos and PIX charges from Banco Inter.
- **API Logging** — Every Inter API call is logged with request/response data for debugging and audit.

---

## Architecture

```
brazil/
├── bancos/             # Banking doctypes (settings, boleto, PIX, payments, logs)
├── fiscal/             # Fiscal doctypes (Nota Fiscal, NF items, events, settings)
├── services/
│   ├── banking/        # Banco Inter service layer
│   │   ├── auth_manager.py      # OAuth2 + mTLS certificate handling
│   │   ├── inter_client.py      # Core HTTP client with retry & logging
│   │   ├── boleto_service.py    # Boleto lifecycle management
│   │   ├── pix_service.py       # PIX charge lifecycle management
│   │   ├── payment_service.py   # Outbound payment execution
│   │   ├── statement_sync.py    # Bank statement import
│   │   ├── reconciliation.py    # Auto-reconciliation engine
│   │   ├── webhook_handler.py   # Incoming webhook processing
│   │   └── cleanup.py           # Log retention management
│   └── fiscal/         # Fiscal service layer
│       ├── dfe_client.py        # SEFAZ DF-e API client
│       ├── email_monitor.py     # Email attachment scanner
│       ├── xml_parser.py        # NF XML parser
│       ├── invoice_creator.py   # Purchase Invoice creation
│       ├── invoice_parser.py    # International invoice text/PDF parsing
│       ├── processor.py         # NF processing pipeline orchestrator
│       ├── supplier_manager.py  # Supplier auto-creation
│       ├── item_manager.py      # Item auto-creation
│       ├── po_matcher.py        # Purchase Order matching
│       └── cert_utils.py        # A1 certificate utilities
├── public/js/          # Client-script overrides (Sales Invoice, Purchase Invoice, Bank Account)
├── setup/              # Installation hooks & custom field creation
└── utils/              # CNPJ validation, chave de acesso, QR code, formatters
```

---

## Requirements

| Dependency | Version |
|---|---|
| Python | >= 3.10 |
| Frappe | >= 15.0.0 |
| ERPNext | >= 15.0.0 |
| cryptography | >= 46.0.0 |
| requests | >= 2.32.0 |
| pypdf | >= 4.0.0 |
| qrcode[pil] | >= 7.4.0 |
| Pillow | >= 10.0.0 |

---

## Installation

Add the app to your `apps.json` file used by your self-hosted ERPNext setup:

```json
[
  {
    "url": "https://github.com/frappe/erpnext",
    "branch": "version-16"
  },
  {
    "url": "https://github.com/Intelligence8official/ERPNext_Brazil.git",
    "branch": "main"
  }
]
```

Then rebuild your environment and install the app on your site:

```bash
bench --site your-site.local migrate
```

---

## Configuration

### Fiscal

1. Go to **Nota Fiscal Settings** and enable the module.
2. Upload your **A1 digital certificate** (.pfx/.p12) and enter the password.
3. Configure your company CNPJ and state code (UF).
4. Optionally enable **email monitoring** by selecting an Email Account to watch for NF attachments.
5. Set auto-processing preferences: supplier auto-creation, item auto-creation, and Purchase Invoice auto-submission.

### Banking (Banco Inter)

1. Go to **Banco Inter Settings** and enable the module.
2. Create an **Inter Company Account** for each company:
   - Upload the mTLS **certificate** (.crt) and **private key** (.key) provided by Banco Inter.
   - Enter your **Client ID** and **Client Secret** from the Inter developers portal.
   - Select the environment (Production or Sandbox).
   - Link to the ERPNext **Bank Account**.
3. Enable features as needed: boleto generation, PIX charges, outbound payments, statement sync.
4. Optionally configure a **webhook URL** to receive real-time payment notifications.

---

## Scheduled Tasks

| Frequency | Task | Description |
|---|---|---|
| Every 5 min | Email monitor | Check emails for NF attachments |
| Every 10 min | DF-e fetch | Fetch new documents from SEFAZ |
| Every 15 min | PIX status check | Poll PIX charge payment status |
| Every 30 min | Boleto status check | Poll boleto payment status |
| Every hour | Payment status check | Check outbound payment status |
| Every 6 hours | Statement sync | Import bank statements |
| Daily | Balance update, cancel expired boletos, cleanup logs | Maintenance tasks |
| Weekly | Cleanup old XMLs, API logs, webhook logs | Retention management |

---

## Roles

| Role | Description |
|---|---|
| Brazil NF Manager | Full access to fiscal configuration and all Nota Fiscal operations |
| Brazil NF User | Read/process Notas Fiscais, create Purchase Invoices |
| Banco Inter Manager | Full access to banking configuration and all Inter operations |
| Banco Inter User | Create boletos/PIX charges, view statements and logs |

---

## Doctypes

### Fiscal

| DocType | Purpose |
|---|---|
| **Nota Fiscal** | Core document representing an NF-e, CT-e, NFS-e, or international invoice |
| **Nota Fiscal Item** | Line items within a Nota Fiscal (child table) |
| **Nota Fiscal Evento** | Cancellation/correction events from SEFAZ |
| **Nota Fiscal Settings** | Global fiscal configuration (certificates, DF-e, email, auto-processing) |
| **NF Company Settings** | Per-company fiscal settings (CNPJ, UF, NSU tracking) |
| **NF Import Log** | Audit log for document imports |

### Banking

| DocType | Purpose |
|---|---|
| **Banco Inter Settings** | Global banking configuration |
| **Inter Company Account** | Per-company Inter API credentials and certificates |
| **Inter Boleto** | Boleto/BoletoPIX document linked to Sales Invoice |
| **Inter PIX Charge** | PIX charge document with QR code |
| **Inter Payment Order** | Outbound payment (PIX, TED, boleto payment) with approval workflow |
| **Inter Sync Log** | Bank statement sync history |
| **Inter API Log** | Full request/response audit log for every API call |
| **Inter Webhook Log** | Incoming webhook event log |

---

## Custom Fields

The app adds custom fields to standard ERPNext doctypes:

- **Supplier** — `inscricao_estadual` (IE), `inscricao_municipal` (IM)
- **Item** — `ncm_code`, `cest_code`, `origem_mercadoria`, `codigo_servico`
- **Purchase Invoice** — `nota_fiscal` (link), `chave_de_acesso`
- **Sales Invoice** — Inter boleto/PIX reference fields
- **Bank Account** — Inter account link

---

## License

MIT — see [pyproject.toml](pyproject.toml) for details.

**Developed by [Intelligence8](mailto:contact@intelligence8.com)**
