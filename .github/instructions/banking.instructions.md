---
description: "Use when working on banking integration: Banco Inter API, boleto, PIX, payments, reconciliation, statement sync, webhooks, or mTLS/OAuth2 authentication."
applyTo: "brazil_module/services/banking/**, brazil_module/bancos/**"
---
# Banking Module Guidelines

## Arquitetura

Todas as chamadas HTTP passam pelo `InterAPIClient` (inter_client.py) que garante:
- mTLS (certificado + chave do cliente)
- OAuth2 Bearer token (gerenciado por `auth_manager.py`)
- Logging em `Inter API Log` DocType
- Retry com exponential backoff
- Tratamento padronizado de erros

## Serviços de Domínio

| Arquivo | Responsabilidade |
|---------|-----------------|
| `boleto_service.py` | Emissão, consulta, cancelamento de boletos |
| `pix_service.py` | Cobranças PIX, QR codes, status |
| `payment_service.py` | Pagamentos outbound (TED, PIX, boletos de terceiros) |
| `reconciliation.py` | Conciliação bancária automática |
| `statement_sync.py` | Sincronização de extratos bancários |
| `webhook_handler.py` | Receptor de webhooks do Inter |
| `cleanup.py` | Limpeza de dados antigos |

## Segurança (Crítico)

- **mTLS obrigatório** — client cert + key para toda chamada ao Inter
- Certificados armazenados no DocType `Inter Company Account`, nunca em arquivos
- `auth_manager.py` gerencia tokens OAuth2 — não faça chamadas diretas à API de token
- **Valide webhooks** antes de processar — verifique origem e integridade
- Nunca logue dados sensíveis (tokens, certificados, senhas)

## URLs Base

- Produção: `https://cdpj.partners.bancointer.com.br`
- Sandbox: `https://cdpj-sandbox.partners.uatinter.co`

O ambiente é determinado pelo `Inter Company Account` — nunca hardcode a URL.

## Padrões

- Settings via `frappe.get_single("Banco Inter Settings")` ou `frappe.get_doc("Inter Company Account", name)`
- Cada serviço recebe `company_account_name: str` no construtor
- Use `InterAPIClient` para qualquer chamada HTTP — não use `requests` diretamente
- Datas no formato ISO 8601 (`YYYY-MM-DD`) para a API do Inter
