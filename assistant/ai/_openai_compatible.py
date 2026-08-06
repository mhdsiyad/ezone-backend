"""Shared plumbing for providers whose API is OpenAI-compatible (Groq, NVIDIA,
and eventually OpenAI/OpenRouter/DeepSeek all speak this exact wire format).
Not part of the public interface — providers subclass this to get client
construction, error translation, and chunk normalization for free, and only
need to supply their own base_url/api_key/name.
"""
from openai import (
    APIConnectionError,
    APITimeoutError,
    OpenAI,
)

from .base import (
    AIProvider,
    ChatResult,
    ProviderAuthError,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderToolCallError,
    ProviderToolsUnsupportedError,
    StreamChunk,
    ToolCallDelta,
    Usage,
)

# Some providers (NVIDIA's hosted models in particular) have been observed
# taking minutes to produce a first token under load — this must sit
# comfortably above that while staying bounded so a genuinely stuck
# connection still fails cleanly.
DEFAULT_TIMEOUT_SECONDS = 320


def _translate(exc, model):
    """Map an openai-sdk exception to our provider-agnostic error types.

    Groq surfaces some failures (rate limits, bad tool-call arguments) as a
    mid-stream error frame rather than an initial non-200 response, which the
    SDK raises as a plain `APIError` rather than the more specific
    `APIStatusError` — so the content-based checks below run regardless of
    exception class, not only inside the `APIStatusError` branch.
    """
    if isinstance(exc, APITimeoutError):
        return ProviderTimeoutError('The AI provider did not respond in time.')
    if isinstance(exc, APIConnectionError):
        return ProviderNetworkError(f'Could not reach the AI provider: {exc}')

    status = getattr(exc, 'status_code', None)
    message = getattr(exc, 'message', None) or str(exc)
    lowered = message.lower()

    if status in (401, 403):
        return ProviderAuthError('The AI provider rejected the API key.')
    if status == 429 or 'rate_limit' in lowered:
        return ProviderRateLimitError(
            'The AI provider is rate-limiting this key (or the request was too large for its '
            'per-minute token limit). Try again shortly, or ask something smaller.'
        )
    if 'tool calling' in lowered or 'tool_calling' in lowered or 'function calling' in lowered:
        return ProviderToolsUnsupportedError(
            f"'{model}' doesn't support tool calling, which this assistant relies on for every "
            f"request — pick a different model."
        )
    if 'did not match schema' in lowered or 'tool call validation failed' in lowered:
        return ProviderToolCallError(
            "The assistant tried to call a tool with invalid arguments and the request was "
            "rejected — try rephrasing, or ask again."
        )
    if status == 404 or 'does not exist' in lowered or 'not found' in lowered:
        return ProviderModelNotFoundError(f"Model '{model}' is not available on this provider.")
    if status is not None:
        return ProviderError(f'AI provider error ({status}): {message}')
    return ProviderError(message)


def _usage_from(chunk_or_response):
    usage = getattr(chunk_or_response, 'usage', None)
    if not usage:
        return None
    return Usage(
        prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
        completion_tokens=getattr(usage, 'completion_tokens', 0) or 0,
        total_tokens=getattr(usage, 'total_tokens', 0) or 0,
    )


class OpenAICompatibleProvider(AIProvider):
    base_url: str = ''
    api_key: str = ''
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def _client(self):
        return OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout_seconds,
            # A single bounded wait beats the SDK's default of silently
            # retrying twice, which would stack multiple long waits back to
            # back on a genuine timeout.
            max_retries=1,
        )

    def chat(self, *, model, messages, **kwargs):
        try:
            response = self._client().chat.completions.create(
                model=model, messages=messages, stream=False, **kwargs,
            )
        except Exception as exc:
            raise _translate(exc, model) from exc
        content = response.choices[0].message.content if response.choices else None
        return ChatResult(content=content, usage=_usage_from(response))

    def stream_chat(self, *, model, messages, **kwargs):
        yield from self._stream(model=model, messages=messages, tools=None, **kwargs)

    def tool_chat(self, *, model, messages, tools, **kwargs):
        yield from self._stream(model=model, messages=messages, tools=tools, **kwargs)

    def _stream(self, *, model, messages, tools, **kwargs):
        try:
            create_kwargs = dict(model=model, messages=messages, stream=True, **kwargs)
            if tools:
                create_kwargs['tools'] = tools
            stream = self._client().chat.completions.create(**create_kwargs)
            for raw_chunk in stream:
                if not raw_chunk.choices:
                    # Some providers send a usage-only trailing chunk with no choices.
                    usage = _usage_from(raw_chunk)
                    if usage:
                        yield StreamChunk(usage=usage)
                    continue

                delta = raw_chunk.choices[0].delta
                tool_call_deltas = [
                    ToolCallDelta(
                        index=tc.index,
                        id=tc.id,
                        name=getattr(tc.function, 'name', None) if getattr(tc, 'function', None) else None,
                        arguments=getattr(tc.function, 'arguments', '') or '' if getattr(tc, 'function', None) else '',
                    )
                    for tc in (getattr(delta, 'tool_calls', None) or [])
                ]
                yield StreamChunk(
                    content=getattr(delta, 'content', None),
                    tool_call_deltas=tool_call_deltas,
                    usage=_usage_from(raw_chunk),
                )
        except ProviderError:
            raise
        except Exception as exc:
            raise _translate(exc, model) from exc
