---
description: "Use when working on Intelligence8 AI agent: Anthropic integration, Telegram bot, tool schemas, decision engine, orchestrator, circuit breaker, cost tracking, or recurring tasks."
applyTo: "brazil_module/services/intelligence/**, brazil_module/intelligence8/**"
---
# Intelligence8 Module Guidelines

## Arquitetura

O módulo implementa um agente AI com Anthropic Claude que responde a eventos do ERP:

| Componente | Responsabilidade |
|-----------|-----------------|
| `agent.py` | Entry point — recebe eventos, escolhe o tier, executa tool loop |
| `llm/` | Camada de provedor: contrato, adaptadores (Anthropic, Google, OpenAI), fábrica, fachada e tabela de preços |
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

Um provedor atende o sistema inteiro, escolhido em `llm_provider` no I8 Agent Settings
(Anthropic, Google ou OpenAI). O código **nunca** nomeia modelo nem fornecedor: pede um
tier, e a fábrica resolve para o modelo configurado daquele provedor.

- **fast** — eventos simples: classify_email, format_notification, status_check, e toda
  formatação (briefing, anomalias, roteamento de evento)
- **standard** — eventos padrão (default)
- **deep** — eventos complexos: anomaly_detected, high_value_decision, complex_reconciliation

O I8 Module Registry aponta para o mesmo vocabulário (`fast|standard|deep`); linhas antigas
com `haiku|sonnet|opus` continuam sendo lidas até o patch `v1_1.rename_model_tiers` rodar.

**Google gasta crédito só pela porta empresarial** (projeto + região + conta de serviço).
O modo de chave de API existe para desenvolvimento e não consome os créditos de Cloud.

## Padrões

- Settings via `frappe.get_single("I8 Agent Settings")`
- **Nunca** instancie um SDK de provedor fora de `llm/`: use `LLM().complete(...)` para o
  tool loop e `LLM().ask(...)` para uma pergunta única
- Credenciais resolvidas pela fábrica, com variável de ambiente ganhando do valor gravado;
  nunca em plaintext
- Custo é registrado pela própria fachada — não chame `CostTracker` direto numa chamada de modelo
- Falha de provedor chega como `LLMError` (com o nome do provedor) e é avisada no Telegram
  no máximo uma vez por hora
- Circuit breaker protege contra falhas consecutivas — respeite o estado `open`
- Tools seguem o schema padrão Anthropic (`name`, `description`, `input_schema`)
- System prompt é montado dinamicamente em `prompts/system_prompt.py`
