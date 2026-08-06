from django.conf import settings

from ._openai_compatible import OpenAICompatibleProvider

GROQ_BASE_URL = 'https://api.groq.com/openai/v1'

# Groq is normally fast (sub-few-seconds to first token) — unlike NVIDIA's
# multi-minute cold starts, there's no legitimate reason for it to sit silent
# this long. A shorter timeout here is what makes the model-fallback retry in
# views.py actually useful: at the shared 320s default, "try another model"
# would rarely fire before the user gave up waiting anyway. Kept short enough
# that a stuck/overloaded model gets swapped out while the wait still feels
# like "one retry," not a second long stall stacked on the first.
GROQ_TIMEOUT_SECONDS = 20


class GroqProvider(OpenAICompatibleProvider):
    """Groq's OpenAI-compatible chat completions API."""
    name = 'groq'

    def __init__(self):
        self.api_key = settings.GROQ_API_KEY
        self.base_url = GROQ_BASE_URL
        self.timeout_seconds = GROQ_TIMEOUT_SECONDS
