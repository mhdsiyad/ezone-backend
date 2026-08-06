"""Provider factory — the one place that knows which provider classes exist.
Adding a future provider (OpenAI, Gemini, Anthropic, DeepSeek, OpenRouter) is
one new class in this package plus one line in `_PROVIDERS` below; nothing
elsewhere in the codebase needs to change.
"""
from .base import ProviderError
from .groq_provider import GroqProvider
from .nvidia_provider import NvidiaProvider

_PROVIDERS = {
    'groq': GroqProvider,
    'nvidia': NvidiaProvider,
}


def get_provider(name):
    provider_cls = _PROVIDERS.get(name)
    if not provider_cls:
        raise ProviderError(f"Unknown AI provider '{name}'.")
    return provider_cls()


def available_providers():
    return list(_PROVIDERS.keys())
