import logging

logger = logging.getLogger('ai')


def log_turn(*, provider, model, latency_ms, tokens=0, tool_call_count=0, error=None):
    if error:
        logger.error(
            'Provider: %s | Model: %s | Latency: %.2fs | Tokens: %s | Tools: %s | Error: %s',
            provider, model, latency_ms / 1000, tokens, tool_call_count, error,
        )
    else:
        logger.info(
            'Provider: %s | Model: %s | Latency: %.2fs | Tokens: %s | Tools: %s',
            provider, model, latency_ms / 1000, tokens, tool_call_count,
        )
