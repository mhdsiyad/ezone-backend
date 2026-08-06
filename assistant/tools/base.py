class ToolError(Exception):
    """Raised by a tool function on bad input — surfaced back to the model as
    a tool-result error string so it can retry or explain to the user."""
    pass


# Argument keys that must never be persisted or streamed back verbatim — the
# tool function itself still receives the real value, this only redacts what
# gets recorded in the SSE `tool_call` event and the saved Message.tool_calls.
SENSITIVE_ARG_KEYS = {'password'}


def redact_args(args):
    if not isinstance(args, dict):
        return args
    return {k: ('***' if k in SENSITIVE_ARG_KEYS else v) for k, v in args.items()}
