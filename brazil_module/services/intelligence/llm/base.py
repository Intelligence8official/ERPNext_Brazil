"""
The seam between Intelligence8 and whichever LLM it is pointed at.

One operation — `complete` — because that is all the app does: a system
prompt, a conversation, a set of tools, and an answer that is either text or
a request to run tools. No streaming, and no state kept on the provider's
side.

The conversation is OURS. The agent used to hand the Anthropic SDK's own
response objects back to the SDK on the next turn, which is what tied the
tool loop to one vendor. Here a transcript is a sequence of plain turns and
each adapter translates the whole thing on every call, holding nothing.

On token counting: `Usage.input_tokens` is what is billed at the full input
rate, `cached_input_tokens` what is read from the cache and
`cache_write_tokens` what is written into it — the three never overlap. The
providers disagree on this (Anthropic's `input_tokens` already excludes both,
Google's `prompt_token_count` includes the cached ones), so each adapter
subtracts before building `Usage`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

TOOL_NAME_RULE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
"""The narrowest of the three providers.

Anthropic allows 256 characters, the Gemini Developer API allows 128 and dots,
Vertex allows 64 — and OpenAI rejects dots outright. This is the intersection,
and every tool the app ships today passes it.
"""


class LLMError(Exception):
    """Any provider failure, under one name.

    The callers already know how to degrade (the briefing sends its raw text,
    the anomaly formatter falls back); what they could not do before is tell
    WHICH provider failed, which is the first thing you want to know when the
    system is pointed at a different one.
    """

    def __init__(self, provider: str, status: int | None, message: str):
        self.provider = provider
        self.status = status
        self.message = message
        super().__init__(str(self))

    def __str__(self) -> str:
        where = f"{self.provider}: " if self.provider else ""
        status = f"{self.status} " if self.status else ""
        return f"{where}{status}{self.message}"


@dataclass(frozen=True)
class ToolCall:
    """What the model asked us to run."""

    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolResult:
    """What we answered. Carries the name as well as the id because Gemini
    matches results by function name, while the other two match by id."""

    call_id: str
    name: str
    content: str


@dataclass(frozen=True)
class UserTurn:
    text: str


@dataclass(frozen=True)
class AssistantTurn:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    provider_state: tuple = ()
    """The provider's own version of this turn, kept opaque and replayed as it
    came. Reasoning models carry state in it that the next turn is refused
    without: OpenAI answers 400 to a function_call whose reasoning item is
    missing, Gemini requires the thought signatures back unchanged, and
    Anthropic refuses thinking blocks that were dropped or reordered."""


@dataclass(frozen=True)
class ToolResults:
    """Every tool result of one turn travels together, as the providers expect."""

    results: tuple[ToolResult, ...] = ()


Turn = UserTurn | AssistantTurn | ToolResults


@dataclass(frozen=True)
class Usage:
    """Input tokens come in three buckets with three prices, and they never
    overlap: full rate, read from the cache (a tenth), and written INTO the
    cache (a quarter more than full rate)."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage
    model: str
    provider: str
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    provider_state: tuple = ()

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    def as_turn(self) -> AssistantTurn:
        """This answer, as the transcript entry the next call replays."""
        return AssistantTurn(
            text=self.text, tool_calls=self.tool_calls, provider_state=self.provider_state
        )


class LLMProvider(Protocol):
    """What every adapter implements. Failures come back as LLMError."""

    name: str

    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Turn],
        tools: Sequence[dict] = (),
        max_tokens: int = 4096,
        timeout: int | None = None,
    ) -> Completion: ...


def validate_tool_names(tools: Sequence[dict]) -> None:
    """Fail before the call, naming the tool at fault.

    A name a provider dislikes comes back as a 400 that rejects the entire
    request, tools and conversation included, with a message that does not say
    which of the twenty-odd tools caused it.
    """
    for tool in tools:
        name = tool.get("name") or ""
        if not TOOL_NAME_RULE.match(name):
            raise LLMError(
                "",
                None,
                f"Tool name '{name}' is not portable: it must match {TOOL_NAME_RULE.pattern}",
            )
