---
description: "Use when working on Intelligence8 AI agent: Anthropic integration, Telegram bot, tool schemas, decision engine, orchestrator, circuit breaker, cost tracking, or recurring tasks."
applyTo: "brazil_module/services/intelligence/**, brazil_module/intelligence8/**"
---
# Intelligence8 Module Guidelines

## Arquitetura

O módulo implementa um agente AI com Anthropic Claude que responde a eventos do ERP:

| Componente | Responsabilidade |
|-----------|-----------------|
| `agent.py` | Entry point — recebe eventos, seleciona modelo, executa tool loop |
| `orchestrator.py` | Coordenação de fluxos multi-step, trace IDs |
| `decision_engine.py` | Decisões autônomas vs. que precisam aprovação humana |
| `context_builder.py` | Monta contexto ERP para o prompt |
| `cost_tracker.py` | Tracking de custo por chamada Anthropic |
| `circuit_breaker.py` | Proteção contra falhas em cascata |
| `tools/` | Tool schemas e executores para o agente |
| `channels/` | Canais de comunicação (Telegram, etc.) |
| `analytics/` | Análise de dados e anomalias |
| `recurring/` | Tarefas agendadas (despesas, follow-ups, briefings) |

## Seleção de Modelo

O agente seleciona o modelo Anthropic baseado no tipo de evento:
- **Haiku** — eventos simples: classify_email, format_notification, status_check
- **Sonnet** — eventos padrão (default)
- **Opus** — eventos complexos: anomaly_detected, high_value_decision, complex_reconciliation

## Padrões

- Settings via `frappe.get_single("I8 Agent Settings")`
- API key Anthropic via `settings.get_password("anthropic_api_key")` — nunca em plaintext
- Toda chamada Anthropic deve passar pelo `cost_tracker` para controle de gastos
- Circuit breaker protege contra falhas consecutivas — respeite o estado `open`
- Tools seguem o schema padrão Anthropic (`name`, `description`, `input_schema`)
- System prompt é montado dinamicamente em `prompts/system_prompt.py`
