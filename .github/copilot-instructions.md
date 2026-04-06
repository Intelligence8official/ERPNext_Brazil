# ERPNext Brazil — Copilot Instructions

## Visão Geral

App Frappe/ERPNext para localização brasileira com três módulos:

- **Fiscal** — NF-e (modelo 55), CT-e (modelo 57), NFS-e. Pipeline: XML parsing → supplier → item → PO match → invoice.
- **Bancos** — Integração Banco Inter via mTLS + OAuth2. Boleto, PIX, pagamentos, conciliação.
- **Intelligence8** — Agente AI (Anthropic) com Telegram, ferramentas ERP, circuit breaker e cost tracking.

## Estrutura do Projeto

```
brazil_module/              # Frappe app root
├── hooks.py                # doc_events, scheduler_events, after_install
├── services/
│   ├── fiscal/             # xml_parser, processor, supplier_manager, item_manager, po_matcher, invoice_creator
│   ├── banking/            # inter_client (HTTP core), boleto, pix, payment, reconciliation, webhook
│   └── intelligence/       # agent, orchestrator, decision_engine, tools/, channels/, analytics/
├── fiscal/doctype/         # DocType JSON + controllers
├── bancos/doctype/         # DocType JSON + controllers
├── utils/                  # Funções puras: cnpj, chave_acesso, formatters, qrcode_gen
├── api/                    # Endpoints whitelisted (@frappe.whitelist)
├── setup/install.py        # Custom fields, roles, after_install/after_migrate
├── public/js/              # Client-side overrides (Sales Invoice, Purchase Invoice, Bank Account)
└── tests/                  # Unit tests com pytest + MagicMock
```

## Convenções de Código

### Python

- Python ≥ 3.10, type annotations em todas as assinaturas
- Linter: `ruff check brazil_module/` (line-length=120, E402 ignorado nos testes)
- Prefira objetos imutáveis (`@dataclass(frozen=True)`, `NamedTuple`)
- Nunca mute parâmetros de entrada — retorne cópias novas
- Funções curtas (<50 linhas), arquivos focados (<800 linhas)
- Trate erros explicitamente, nunca engula exceções silenciosamente

### Frappe Framework

- DocTypes são definidos em JSON (`doctype/<name>/<name>.json`) — não edite manualmente
- Use `frappe.get_single()` para settings DocTypes (singleton)
- Use `frappe.get_all(..., pluck="name")` para listas simples (retorna `list[str]`, não dicts)
- Use `frappe.enqueue()` para jobs assíncronos via Redis
- Hooks vão em `hooks.py` — doc_events, scheduler_events, custom fields
- Endpoints públicos usam `@frappe.whitelist(allow_guest=True)` com cuidado

### JavaScript (Client-side)

- Scripts em `public/js/` estendem formulários existentes do ERPNext
- Use `frappe.ui.form.on()` para hooks em formulários
- Não crie dependências externas — use apenas a API JS do Frappe

## Build & Test

```bash
# Rodar todos os testes
python3 -m pytest brazil_module/tests/ -v

# Teste específico
python3 -m pytest brazil_module/tests/test_cnpj.py -v

# Lint
ruff check brazil_module/
```

## Padrão de Testes (Crítico)

Os testes rodam **fora do Frappe** com `frappe` mockado via `sys.modules`. Siga este padrão:

```python
# 1. Injete o mock ANTES de importar o módulo
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    frappe_mock._ = lambda x: x
    sys.modules["frappe"] = frappe_mock
    sys.modules["frappe.utils"] = frappe_mock.utils

# 2. Só então importe
import frappe
from brazil_module.services.fiscal.some_module import SomeClass
```

**Gotchas:**
- `from frappe.utils import flt` captura o binding no import. Para sobrescrever, patche o módulo: `module_under_test.flt = float`
- `frappe.reset_mock()` NÃO limpa `side_effect` nem `return_value` — limpe explicitamente
- Ignore E402 em testes (imports após setup do mock)

## Domínio Fiscal Brasileiro

- **CNPJ** — 14 dígitos, dois dígitos verificadores mod-11. Validação em `utils/cnpj.py`
- **Chave de Acesso** — 44 dígitos (NF-e/CT-e) ou 50 (NFS-e), dígito verificador mod-11. Validação em `utils/chave_acesso.py`
- **SEFAZ DistDFeInt** — API SOAP 1.2, mTLS (sem assinatura XML), retorna documentos gzip+base64
- **NSU** (Número Sequencial Único) — Controle de último documento buscado no SEFAZ
- XML fiscal segue padrões do ENCAT/SEFAZ — não altere namespaces ou estrutura XML

## Segurança

- Nunca hardcode secrets (API keys, certificados, senhas)
- Certificados digitais (A1/A3) são armazenados no DocType de settings, não em arquivos
- Inter API usa mTLS — client cert + key são obrigatórios
- Valide todo input em endpoints `@frappe.whitelist`
- Webhooks do Inter devem ser validados antes de processar

## Idioma

- Código, commits e docstrings em **inglês**
- Nomes de DocTypes e labels podem estar em **português** (são voltados ao usuário final)
- Respostas e comentários em **português brasileiro**
