from django.conf import settings

from ._openai_compatible import OpenAICompatibleProvider


class NvidiaProvider(OpenAICompatibleProvider):
    """NVIDIA's OpenAI-compatible chat completions API — the original
    provider, kept available as a secondary/fallback option."""
    name = 'nvidia'

    def __init__(self):
        self.api_key = settings.NVIDIA_API_KEY
        self.base_url = settings.NVIDIA_BASE_URL
