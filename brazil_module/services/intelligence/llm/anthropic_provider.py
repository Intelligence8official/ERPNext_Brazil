"""
Anthropic, behind the common seam.

The one adapter whose wire format the rest of the app used to speak natively:
tools already travel as `input_schema`, and the transcript shapes below are
what `agent.py` used to build by hand.

Token counting needs no subtraction here — Anthropic's `input_tokens` already
leaves the cached ones out, and reports them in `cache_read_input_tokens`.
"""

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


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, client=None):
        self._api_key = api_key
        self._client = client

    @property
    def client(self):
        """Built on first use and imported here, so a site running on another
        provider does not need this SDK installed."""
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
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

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [_turn_to_message(turn) for turn in messages],
        }
        if tools:
            payload["tools"] = list(tools)
        if timeout:
            payload["timeout"] = timeout

        try:
            response = self.client.messages.create(**payload)
        except Exception as e:
            raise LLMError(self.name, getattr(e, "status_code", None), str(e)) from e

        return _to_completion(response, model, self.name)


def _turn_to_message(turn: Turn) -> dict:
    if isinstance(turn, UserTurn):
        return {"role": "user", "content": turn.text}

    if isinstance(turn, AssistantTurn):
        if turn.provider_state:
            # Its own blocks, in their own order. Thinking blocks are signed,
            # and rebuilding them from text and calls loses the signature.
            return {"role": "assistant", "content": list(turn.provider_state)}

        content = []
        if turn.text:
            content.append({"type": "text", "text": turn.text})
        content.extend(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            for call in turn.tool_calls
        )
        return {"role": "assistant", "content": content}

    if isinstance(turn, ToolResults):
        # Tool results go back as a USER turn: that is how Anthropic reads them.
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": result.call_id, "content": result.content}
                for result in turn.results
            ],
        }

    raise LLMError("anthropic", None, f"Cannot send a {type(turn).__name__} to the model")


def _to_completion(response, model: str, provider: str) -> Completion:
    text = ""
    calls = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            text += block.text
        elif getattr(block, "type", None) == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input or {}))

    usage = getattr(response, "usage", None)
    cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    return Completion(
        text=text,
        tool_calls=tuple(calls),
        provider_state=tuple(getattr(response, "content", None) or ()),
        usage=Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cached_input_tokens=cached,
            cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        ),
        model=model,
        provider=provider,
    )
