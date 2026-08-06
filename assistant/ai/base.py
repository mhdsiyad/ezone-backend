"""Provider-agnostic contract for LLM backends.

Business logic (views.py) only ever talks to an `AIProvider` instance obtained
from `factory.get_provider(name)` — it never imports Groq/NVIDIA/OpenAI SDKs
directly. Every provider normalizes its wire format into the dataclasses below,
so swapping providers or adding a new one never touches the calling code.
"""
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class ToolCallDelta:
    """One incremental piece of a tool call as it streams in. `index` is the
    tool call's position in this turn (a turn may request several calls in
    parallel); deltas for the same index are concatenated by the caller."""
    index: int
    id: Optional[str] = None
    name: Optional[str] = None
    arguments: str = ''


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class StreamChunk:
    """One normalized frame from a streaming call."""
    content: Optional[str] = None
    tool_call_deltas: list = field(default_factory=list)  # list[ToolCallDelta]
    usage: Optional[Usage] = None  # only ever present on the final chunk


@dataclass
class ChatResult:
    """The full result of a non-streaming call."""
    content: Optional[str]
    usage: Optional[Usage] = None


# --- Errors -----------------------------------------------------------------
# Every provider must translate its SDK's exceptions into one of these so
# callers can handle "rate limited" vs "bad key" vs "model doesn't exist"
# uniformly regardless of which provider raised it.

class ProviderError(Exception):
    """Base class for all provider-layer errors."""


class ProviderAuthError(ProviderError):
    """Invalid or missing API key."""


class ProviderRateLimitError(ProviderError):
    """Provider is rate-limiting this key."""


class ProviderModelNotFoundError(ProviderError):
    """The requested model id doesn't exist (or isn't available) on this provider."""


class ProviderTimeoutError(ProviderError):
    """The provider didn't respond within the configured timeout."""


class ProviderNetworkError(ProviderError):
    """Couldn't reach the provider at all (DNS/connection failure)."""


class ProviderToolsUnsupportedError(ProviderError):
    """This model rejects the `tools` param outright (e.g. Groq's compound
    models). Unlike a timeout/rate-limit, this always fails again on retry
    with the SAME model — but a different, tool-calling-capable model fixes
    it immediately, so it's still worth falling back on."""


class ProviderToolCallError(ProviderError):
    """The model produced a tool call the provider rejected as malformed
    (bad JSON / arguments that don't match the tool's schema) — this is the
    model's own output being wrong, not the model being unavailable, but a
    different model calling the same tool correctly is a legitimate fix, so
    it's included in the fallback-retry set alongside availability errors."""


class AIProvider:
    """Interface every provider implements. `name` is the registry key used
    in AI_PROVIDER / the model registry's `provider` field."""
    name: str = ''

    def chat(self, *, model: str, messages: list, **kwargs) -> ChatResult:
        """Non-streaming, tool-free completion — e.g. for cheap one-shot uses
        like auto-generating a conversation title."""
        raise NotImplementedError

    def stream_chat(self, *, model: str, messages: list, **kwargs) -> Iterator[StreamChunk]:
        """Streaming, tool-free completion."""
        raise NotImplementedError

    def tool_chat(self, *, model: str, messages: list, tools: list, **kwargs) -> Iterator[StreamChunk]:
        """Streaming completion with function/tool calling enabled — what the
        assistant's main chat loop uses on every turn."""
        raise NotImplementedError
