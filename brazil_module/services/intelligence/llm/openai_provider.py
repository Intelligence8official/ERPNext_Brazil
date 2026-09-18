"""
OpenAI, behind the common seam.

Built on the Responses API, which is what OpenAI recommends for new work —
Chat Completions stays supported, but the tool format and the token fields
differ between the two, and following the recommended one keeps this adapter
from being rewritten next year.

Two traps this adapter defuses. Tools are internally tagged here (no nested
`function` object), and leaving `strict` unset makes the API ATTEMPT strict
mode, which refuses schemas the other two providers accept — so it is set
explicitly. And `input_tokens` INCLUDES the cached ones, which are subtracted
before they reach the cost table.
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


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, client=None):
        self._api_key = api_key
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import openai

            self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

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

        payload: dict = {
            "model": model,
            "instructions": system,
            "input": [item for turn in messages for item in _turn_to_items(turn)],
            "max_output_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [_tool(tool) for tool in tools]
        if timeout:
            payload["timeout"] = timeout

        try:
            response = self.client.responses.create(**payload)
        except Exception as e:
            raise LLMError(self.name, getattr(e, "status_code", None), str(e)) from e

        return _to_completion(response, model, self.name)


def _tool(tool: dict) -> dict:
    return {
        "type": "function",
        "name": tool.get("name"),
        "description": tool.get("description", ""),
        "parameters": tool.get("input_schema") or {"type": "object"},
        "strict": False,
    }


def _turn_to_items(turn: Turn) -> list[dict]:
    """One turn can become several items: text and calls travel separately."""
    if isinstance(turn, UserTurn):
        return [{"role": "user", "content": turn.text}]

    if isinstance(turn, AssistantTurn):
        items = []
        if turn.text:
            items.append({"role": "assistant", "content": turn.text})
        items.extend(
            {
                "type": "function_call",
                "call_id": call.id,
                "name": call.name,
                "arguments": json.dumps(call.arguments),
            }
            for call in turn.tool_calls
        )
        return items

    if isinstance(turn, ToolResults):
        return [
            {"type": "function_call_output", "call_id": result.call_id, "output": result.content}
            for result in turn.results
        ]

    raise LLMError("openai", None, f"Cannot send a {type(turn).__name__} to the model")


def _to_completion(response, model: str, provider: str) -> Completion:
    text = ""
    calls = []
    for item in getattr(response, "output", None) or []:
        kind = getattr(item, "type", None)
        if kind == "message":
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "output_text":
                    text += part.text
        elif kind == "function_call":
            calls.append(
                ToolCall(
                    id=item.call_id,
                    name=item.name,
                    arguments=_arguments(item.name, item.arguments, provider),
                )
            )

    usage = getattr(response, "usage", None)
    details = getattr(usage, "input_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0)
    return Completion(
        text=text,
        tool_calls=tuple(calls),
        usage=Usage(
            input_tokens=max(int(getattr(usage, "input_tokens", 0) or 0) - cached, 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cached_input_tokens=cached,
        ),
        model=model,
        provider=provider,
    )


def _arguments(name: str, arguments, provider: str) -> dict:
    """Arguments arrive as a JSON string.

    Malformed JSON stops the turn instead of becoming an empty dict: for a tool
    that lists or deletes, no arguments can mean everything.
    """
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments or "{}")
    except (TypeError, ValueError) as e:
        raise LLMError(provider, None, f"Arguments for '{name}' are not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise LLMError(provider, None, f"Arguments for '{name}' are not an object")
    return parsed
