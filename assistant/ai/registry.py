"""Static catalog of selectable models. The active model always comes from
persisted runtime settings (see models.AIRuntimeSetting) or an explicit
per-request override — never hardcoded in business logic. Add a model here
and it's immediately selectable everywhere; nothing else needs to change.
"""

AI_MODELS = [
    {'id': 'groq/compound', 'name': 'Compound', 'provider': 'groq'},
    {'id': 'groq/compound-mini', 'name': 'Compound Mini', 'provider': 'groq'},
    {'id': 'openai/gpt-oss-120b', 'name': 'GPT OSS 120B', 'provider': 'groq'},
    {'id': 'openai/gpt-oss-20b', 'name': 'GPT OSS 20B', 'provider': 'groq'},
    {'id': 'llama-3.3-70b-versatile', 'name': 'Llama 3.3 70B', 'provider': 'groq'},
    {'id': 'llama-3.1-8b-instant', 'name': 'Llama 3.1 8B Instant', 'provider': 'groq'},
    {'id': 'qwen/qwen3.6-27b', 'name': 'Qwen 3.6 27B', 'provider': 'groq'},
    # NVIDIA kept selectable as the original/fallback provider.
    {'id': 'z-ai/glm-5.2', 'name': 'GLM 5.2', 'provider': 'nvidia'},
]

_BY_ID = {m['id']: m for m in AI_MODELS}


def get_model_entry(model_id):
    """Returns the registry entry for a model id, or None if unknown."""
    return _BY_ID.get(model_id)
