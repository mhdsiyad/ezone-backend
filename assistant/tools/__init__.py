"""Tool functions the AI assistant is allowed to call, plus their JSON-schema
definitions for the model's function-calling API.

Split by domain (auction, fixture, custom_tournament, teams, players) since
the tool count has grown past what's comfortable in one file. Every tool is
scoped to the requesting manager's own data (same ownership filters the
human-facing views use), reuses existing serializers/services where they
exist, and raises ToolError on bad input rather than letting a raw exception
reach the model.
"""
from . import auction, custom_tournament, fixture, players, teams
from .base import ToolError, redact_args

TOOL_FUNCTIONS = {}
TOOL_SCHEMAS = []
for _module in (auction, fixture, custom_tournament, teams, players):
    TOOL_FUNCTIONS.update(_module.FUNCTIONS)
    TOOL_SCHEMAS.extend(_module.SCHEMAS)


def filter_tools(names):
    """Returns (schemas, functions) narrowed to the given tool names — used to
    scope a workspace to only the tools relevant to it."""
    allowed = set(names)
    schemas = [s for s in TOOL_SCHEMAS if s['function']['name'] in allowed]
    functions = {k: v for k, v in TOOL_FUNCTIONS.items() if k in allowed}
    return schemas, functions


__all__ = ['ToolError', 'redact_args', 'TOOL_FUNCTIONS', 'TOOL_SCHEMAS', 'filter_tools']
