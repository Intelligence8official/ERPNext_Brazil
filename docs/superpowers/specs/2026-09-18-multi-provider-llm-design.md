# Multi-provider LLM — design

Date: 2026-09-18
Status: approved, in implementation

## Problem

Intelligence8 talks to the Anthropic SDK directly in four places, and the rest of
the module is shaped around it: model tiers are named `haiku`/`sonnet`/`opus`, the
cost table is keyed by Claude model ids, and the agent's tool loop stores Anthropic
SDK objects inside the conversation it replays to the model.

Abel wants to be able to point the whole system at Google Gemini or OpenAI instead —
to pay less, to not depend on one vendor, to compare quality, and above all to spend
the Google for Startups credits the company holds.

## What the research settled (2026-09-18)

1. **Google credits only work through one door.** Google for Startups credits are
   Google Cloud credits, and the contractual service list covers Vertex AI (renamed
   "Gemini Enterprise Agent Platform" in April 2026). The Gemini Developer API
   (AI Studio) is *not* on that list, and Google's own billing docs say Cloud credits
   cannot pay for Gemini API or AI Studio usage. **The Google adapter therefore runs
   in enterprise mode by default** — project, location and service-account/ADC
   credentials — with the API-key mode kept only for local development.
2. **Credits do not cover third-party models on Vertex.** Running Claude through
   Vertex Model Garden is billed outside the program, so keeping Anthropic means
   paying cash.
3. **Google has no GA deep-reasoning model.** `gemini-3.1-pro-preview` is preview and
   `gemini-3-pro-preview` was retired; the only GA option is the previous-generation
   `gemini-2.5-pro`. Switching everything to Google weakens the deep tier.
4. **Tool names with hyphens are accepted by all three providers** (`erp-read_document`
   is valid; the safe common rule is `^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`). No tool has to be
   renamed and the I8 Module Registry patterns keep working. Parameter names must stay
   free of hyphens, which they already are.
5. **The prices in `cost_tracker.py` are stale even for Anthropic** (Sonnet 5 is
   $2/$10, not $3/$15), so the table is rewritten regardless of this change.

## Decisions

- **One provider for the whole system**, chosen in I8 Agent Settings. No per-module
  provider, no automatic fallback: when the chosen provider fails, the call fails,
  it is logged, and Abel is told (his explicit choice).
- **Own adapters over the native SDKs**, not a gateway library. The OpenAI-compatible
  surfaces lose prompt caching and per-provider token accounting — and token
  accounting is exactly how we measure the credit burn. Google is also moving
  `generate_content` towards a new Interactions API, which is another reason to own a
  seam we can swap one file behind.
- **The conversation becomes ours.** The agent loop stops replaying SDK objects and
  keeps a neutral transcript; each adapter translates it on every call and keeps no
  state.
- **Tier names follow the job, not the vendor**: `fast`, `standard`, `deep` replace
  `haiku`, `sonnet`, `opus` in settings and in the I8 Module Registry, migrated by a
  patch.

## The interface

`brazil_module/services/intelligence/llm/base.py`

```python
ToolCall(id, name, arguments)          # what the model asked for
ToolResult(call_id, name, content)     # what we answered (name: Gemini needs it)
AssistantTurn(text, tool_calls)        # a model turn in the transcript
Usage(input_tokens, output_tokens, cached_input_tokens)
Completion(text, tool_calls, usage, model, provider, wants_tools)
LLMError(provider, status, message)    # every provider failure, one type

class LLMProvider(Protocol):
    name: str
    def complete(self, *, model, system, messages, tools=(),
                 max_tokens, timeout) -> Completion: ...
```

`messages` is a sequence of neutral turns: a user string, an `AssistantTurn`, or a
tuple of `ToolResult`. Tools keep today's shape (`name`, `description`,
`input_schema`) — plain JSON Schema, which each adapter re-keys.

Every adapter raises `LLMError` and nothing else; callers keep their circuit breaker
and their own fallbacks.

## The adapters

| | Anthropic | Google | OpenAI |
|---|---|---|---|
| SDK | `anthropic` (already a dependency) | `google-genai` | `openai` |
| Auth | api key | project + location + service account / ADC (enterprise mode) | api key |
| System prompt | `system=` | `system_instruction` | `instructions` |
| Tool schema key | `input_schema` | `parameters_json_schema`, automatic function calling disabled | internally tagged (Responses API) |
| Tool result | `tool_result` block | `function_response` part, role `user` | `function_call_output` item |
| Cached tokens | `cache_read_input_tokens` | `cached_content_token_count` | `input_tokens_details.cached_tokens` |

SDKs are imported lazily inside each adapter so the test suite (and any deployment
using only one provider) does not need all three installed.

## Configuration and migration

I8 Agent Settings:

- `llm_provider`: Select (Anthropic / Google / OpenAI), default Anthropic.
- `model_fast`, `model_standard`, `model_deep` (Data) replace `haiku_model`,
  `sonnet_model`, `opus_model`.
- `timeout_fast`, `timeout_standard`, `timeout_deep` replace the three
  `*_timeout_seconds` fields — and are finally wired: `get_timeout()` exists today and
  is never called.
- Google credentials: `google_auth_mode` (Enterprise / API key), `google_project`,
  `google_location` (default `global`, which is the price without the regional 10%
  surcharge), `google_service_account_json` (Password), `google_api_key` (Password).
- `openai_api_key` (Password).
- Environment variables win over the stored values, as with the current
  `ANTHROPIC_API_KEY`.

I8 Module Registry: `default_model` / `escalation_model` options become
`fast|standard|deep`.

Patches (`brazil_module/patches/v1_1/`): copy the three model values and the three
timeouts into the new fields, and rewrite the registry rows. Old values map
`haiku→fast`, `sonnet→standard`, `opus→deep`.

## Cost

- `llm/pricing.py` holds a per-provider table keyed by model id, rewritten with
  today's prices. The Gemini 3.x Flash promo ends on 2026-12-31 (input, output and
  cached input all double) and the Gemini Pro models charge a second rate above 200k
  input tokens — both in the table.
- Input tokens are three buckets that never overlap: full rate, read from the cache
  (a tenth) and written into it (a quarter more than full rate). Each adapter
  normalizes, because the providers report them differently.
- Gemini reports thinking tokens and tool-prompt tokens in counts of their own, and
  bills them as output and input: a turn that spends its whole budget thinking would
  otherwise be recorded as free.
- Token budgets leave room for reasoning. On OpenAI and Google the budget covers
  reasoning tokens too, so a tight one comes back empty and looks like a success.
- An unknown model id is priced at the **most expensive known model of its provider**
  and logged. Today it silently bills at Sonnet rates, which under-reports; the
  budget gate must fail safe, not cheap.
- I8 Cost Log gains `provider` and `cached_tokens`. Every call logs, including the
  orchestrator's router call, which today spends money without a record.

## Failures

`LLMError` → the existing circuit breaker and Error Log, plus one Telegram alert,
rate-limited to one per hour per provider, so a scheduled job that stops working does
not fail silently. The briefing and the anomaly formatter keep their current
fallbacks (raw text instead of formatted).

A whitelisted `test_llm_connection()` with a button in the settings form makes a
one-token call and reports what it got, so a wrong service-account JSON is found when
it is pasted, not at 08:00 the next morning.

## Testing

- One contract test suite every adapter must pass against a fake SDK client:
  transcript conversion, tool call parsing, tool result round-trip, usage extraction,
  error wrapping, and replaying the provider's own turn.
- A second suite builds requests with the providers' OWN types and reads responses
  made of their own objects. A fake written from the adapter only proves the adapter
  agrees with itself; the cache-write bucket was found by this suite, not by us.
- Pricing tests per provider, including cached input and the unknown-model rule.
- Factory tests: the provider switch, tier to model, timeouts, credential resolution.
- The existing agent/orchestrator/briefing/anomaly tests move to the fake provider.

## Out of scope

Hand-written prompt caching (Google and OpenAI cache implicitly; only Anthropic would
need code — a later change), streaming, per-module providers, automatic fallback to a
second provider, and any change to the tool catalogue.

## Phases

1. `base.py` types and protocol, `pricing.py`.
2. Anthropic adapter + contract suite.
3. Google adapter (enterprise mode).
4. OpenAI adapter.
5. `factory.py` reading settings.
6. Rewire the four call sites, starting with the agent's tool loop.
7. DocType fields, patches, install seeds.
8. Dependencies in `pyproject.toml`, documentation.
9. Failure alert and the connection test button.
