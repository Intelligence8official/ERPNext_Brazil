"""
Google Gemini, behind the common seam.

Enterprise mode (what used to be called Vertex AI, renamed Gemini Enterprise
Agent Platform in April 2026) is the default, and it is not a preference: the
Google for Startups credits are Google Cloud credits, and Cloud credits do not
pay for the Gemini Developer API. Project, location and a service account are
what makes a call land on the billing account that holds the credits. The
api-key mode stays for local development.

Payloads go over as plain dicts. The SDK coerces them into its own types, and
keeping dicts here means the adapter can be tested without the SDK installed —
and read without cross-referencing a types module.

Two shapes that differ from the other providers, and cost a rejected request
when got wrong: the model's own turn has role `model`, and a tool result goes
back as role `user` (the API accepts no other roles). Tokens also differ:
`prompt_token_count` INCLUDES the cached ones, so they are subtracted here.
"""

import json
from collections.abc import Sequence

from brazil_module.services.intelligence.llm.base import (
    AssistantTurn,
    Completion,
    LLMError,
    ToolCall,
    ToolResults,
    Turn,
    Usage,
    UserTurn,
    validate_tool_names,
)

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
DEFAULT_LOCATION = "global"
"""The global endpoint is the price without the 10% regional surcharge."""


class GoogleProvider:
    name = "google"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        project: str | None = None,
        location: str = DEFAULT_LOCATION,
        service_account_json: str | None = None,
        client=None,
    ):
        self._api_key = api_key
        self._project = project
        self._location = location or DEFAULT_LOCATION
        self._service_account_json = service_account_json
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self):
        from google import genai

        if self._api_key:
            return genai.Client(api_key=self._api_key)

        # `vertexai=` and not `enterprise=`: the SDK renamed the flag with the
        # product, and the old name is the one both versions answer to.
        kwargs = {"vertexai": True, "project": self._project, "location": self._location}
        credentials = self._credentials()
        if credentials is not None:
            kwargs["credentials"] = credentials
        return genai.Client(**kwargs)

    def _credentials(self):
        """The service account, when one was configured.

        Without it the SDK falls back to application default credentials,
        which is what a deployment on Google Cloud wants. The project is always
        sent alongside: given credentials but no project, the SDK goes back to
        the default credentials and ignores the ones just handed to it.
        """
        if not self._service_account_json:
            return None

        try:
            info = json.loads(self._service_account_json)
        except ValueError as e:
            raise LLMError(self.name, None, f"The Google service account JSON is not valid JSON: {e}") from e

        from google.oauth2 import service_account

        try:
            return service_account.Credentials.from_service_account_info(
                info, scopes=[CLOUD_PLATFORM_SCOPE]
            )
        except Exception as e:
            raise LLMError(self.name, None, f"The Google service account was refused: {e}") from e

    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Turn],
        tools: Sequence[dict] = (),
        max_tokens: int = 4096,
        timeout: int | None = None,
    ) -> Completion:
        validate_tool_names(tools)

        config: dict = {"system_instruction": system, "max_output_tokens": max_tokens}
        if tools:
            config["tools"] = [{"function_declarations": [_declaration(tool) for tool in tools]}]
            # Said out loud although the SDK only ever calls functions it can
            # call — our declarations are dicts, never callables. Deciding
            # whether a tool may run is the agent's job, and a future SDK
            # should not get to change that by default.
            config["automatic_function_calling"] = {"disable": True}
        if timeout:
            # HttpOptions.timeout is MILLISECONDS here, unlike every other SDK
            # in this codebase. Sending seconds would cap each call at 30ms.
            config["http_options"] = {"timeout": int(timeout) * 1000}

        try:
            response = self.client.models.generate_content(
                model=model,
                contents=[_turn_to_content(turn) for turn in messages],
                config=config,
            )
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(self.name, getattr(e, "code", None), str(e)) from e

        return _to_completion(response, model, self.name)


def _declaration(tool: dict) -> dict:
    """`parameters_json_schema` and not `parameters`: the latter is an OpenAPI
    subset with no `additionalProperties`, `$ref` or `$defs`."""
    return {
        "name": tool.get("name"),
        "description": tool.get("description", ""),
        "parameters_json_schema": tool.get("input_schema") or {"type": "object"},
    }


def _turn_to_content(turn: Turn) -> dict:
    if isinstance(turn, UserTurn):
        return {"role": "user", "parts": [{"text": turn.text}]}

    if isinstance(turn, AssistantTurn):
        if turn.provider_state:
            # Gemini requires the thought signatures of a turn to come back
            # exactly as they were received.
            return turn.provider_state[0]

        parts = []
        if turn.text:
            parts.append({"text": turn.text})
        parts.extend(
            {"function_call": {"name": call.name, "args": call.arguments}} for call in turn.tool_calls
        )
        return {"role": "model", "parts": parts}

    if isinstance(turn, ToolResults):
        return {"role": "user", "parts": [_function_response(result) for result in turn.results]}

    raise LLMError("google", None, f"Cannot send a {type(turn).__name__} to the model")


def _block_reason(response) -> str:
    """Why nothing came back. `block_reason_message` only exists on Vertex."""
    feedback = getattr(response, "prompt_feedback", None)
    reason = getattr(feedback, "block_reason", None) or feedback or "no reason given"
    message = getattr(feedback, "block_reason_message", None)
    return f"{reason}: {message}" if message else str(reason)


def _empty_answer_reason(candidate) -> str:
    reason = getattr(candidate, "finish_reason", None) or "no finish reason"
    message = getattr(candidate, "finish_message", None) or ""
    if str(reason) == "MALFORMED_FUNCTION_CALL":
        # Not a refusal: the model could not produce a call that fits one of
        # the tool schemas we sent.
        return f"MALFORMED_FUNCTION_CALL — a tool schema was rejected by the model. {message}".strip()
    return f"The model answered nothing ({reason}) {message}".strip()


def _function_response(result) -> dict:
    """One tool result, as Gemini reads it.

    The id goes back only when the model issued one: two parallel calls to the
    same tool are told apart by id, never by name. A synthetic id of ours
    would be answering something nobody asked.
    """
    response = {"function_response": {"name": result.name, "response": _as_object(result.content)}}
    if result.call_id and not result.call_id.startswith(f"{result.name}-"):
        response["function_response"]["id"] = result.call_id
    return response


def _as_object(content: str) -> dict:
    """Always under `output`.

    A function response is an object, and Gemini gives two of its keys a
    meaning: `output` is the result and `error` is a failure — anything else
    alongside an `error` key is thrown away. Several of our tools answer
    `{"error": ...}`, so wrapping keeps the payload whole and the semantics
    ours.
    """
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return {"output": content}
    return {"output": parsed}


def _to_completion(response, model: str, provider: str) -> Completion:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise LLMError(provider, None, f"The prompt was refused ({_block_reason(response)})")

    text = ""
    calls = []
    parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []
    for index, part in enumerate(parts):
        if getattr(part, "text", None):
            text += part.text
        call = getattr(part, "function_call", None)
        if call is not None:
            calls.append(
                ToolCall(
                    # Gemini matches results by function name and may send no
                    # id at all; the agent loop matches by id, so make one.
                    id=getattr(call, "id", None) or f"{call.name}-{index}",
                    name=call.name,
                    arguments=dict(getattr(call, "args", None) or {}),
                )
            )

    if not text and not calls:
        # An empty turn is never useful, and the agent loop would take it for
        # an answer. The reason tells apart a refusal, a truncation and a tool
        # schema Gemini could not parse.
        raise LLMError(provider, None, _empty_answer_reason(candidates[0]))

    usage = getattr(response, "usage_metadata", None)
    cached = int(getattr(usage, "cached_content_token_count", 0) or 0)
    prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
    # Gemini reports four DISJOINT counts. Thinking tokens are billed as
    # output and the tokens the tool results took are billed as input; reading
    # only the first two of the four bills a fraction of the call.
    thoughts = int(getattr(usage, "thoughts_token_count", 0) or 0)
    tool_prompt = int(getattr(usage, "tool_use_prompt_token_count", 0) or 0)
    return Completion(
        text=text,
        tool_calls=tuple(calls),
        provider_state=(getattr(candidates[0], "content", None),),
        usage=Usage(
            # `prompt_token_count` includes the cached tokens; the two must not
            # overlap once they reach the cost table.
            input_tokens=max(prompt_tokens - cached, 0) + tool_prompt,
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0) + thoughts,
            cached_input_tokens=cached,
        ),
        model=model,
        provider=provider,
    )
