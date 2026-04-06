---
description: "Use when working on fiscal services: NF-e, CT-e, NFS-e processing, XML parsing, SEFAZ integration, supplier/item matching, invoice creation, or fiscal utilities (CNPJ, chave de acesso)."
applyTo: "brazil_module/services/fiscal/**, brazil_module/fiscal/**, brazil_module/utils/**"
---
# Fiscal Module Guidelines

## Pipeline de Processamento

O `NFProcessor` (processor.py) orquestra em ordem:
1. **XML Parsing** (xml_parser.py) — detecta tipo do documento (NF-e/CT-e/NFS-e), extrai campos
2. **Supplier** (supplier_manager.py) — busca CNPJ com 5 estratégias + auto-criação
3. **Items** (item_manager.py) — match com 4 estratégias + auto-criação
4. **PO Matching** (po_matcher.py) — scoring: valor 30pts, itens 60pts, data 10pts
5. **Invoice Creation** (invoice_creator.py) — detecção de duplicata (3 estratégias) + criação

Não altere a ordem do pipeline. Cada etapa depende da anterior.

## SEFAZ / XML

- `dfe_client.py` — SOAP 1.2, mTLS (sem assinatura XML), retorna gzip+base64
- `cert_utils.py` — extrai cert/key de arquivo PFX (A1)
- XML segue padrões ENCAT/SEFAZ. **Nunca altere namespaces ou estrutura XML.**
- NSU (Número Sequencial Único) é o cursor de paginação — sempre persista o último valor

## Validações Fiscais

- **CNPJ** — 14 dígitos, dois dígitos verificadores mod-11 (`utils/cnpj.py`)
- **Chave de Acesso** — 44 dígitos (NF-e/CT-e) ou 50 (NFS-e), mod-11 (`utils/chave_acesso.py`)
- Use as funções existentes em `utils/` — não reimplemente validações

## Padrões

- Settings via `frappe.get_single("Nota Fiscal Settings")`
- Documentos cancelados (`cancelada=True`) não podem ser processados
- Use `frappe.enqueue()` para processamento em lote
- Toda função de serviço recebe o doc como parâmetro, não o nome
