import json
import time

from django.db.models import Q
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from auction.permissions import IsManagerPermission

from .ai.base import (
    ProviderAuthError,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderToolCallError,
    ProviderToolsUnsupportedError,
)
from .ai.factory import get_provider
from .ai.logging_utils import log_turn
from .ai.registry import AI_MODELS, get_model_entry
from .models import AIRuntimeSetting, Conversation, Message
from .serializers import ConversationDetailSerializer, ConversationListSerializer
from .tools import ToolError, filter_tools, redact_args
from .workspaces import get_workspace, public_workspaces

BASE_SYSTEM_PROMPT = """You are the eZone Auction Platform assistant, helping a tournament manager over chat.

You can look up and manage auctions, teams, tournaments, custom tournaments, matches, and player
applications. Every tool call only ever touches data owned by the manager you're talking to — nothing else
exists for you. You never permanently delete anything — the closest thing to that, rejecting a player
application, marks it rejected without erasing it.

Guidelines:
- Always call a tool to look up real data before answering factual questions — never guess ids, scores, or names.
- Before calling create_tournament, create_auction, create_team, create_custom_tournament,
  publish_match_result, update_tournament_settings, or reject_player_application, restate exactly what
  you're about to do in plain language and get a clear yes, unless the manager already gave you the exact
  details needed to act immediately. update_tournament_settings and reject_player_application in particular
  have real consequences (un-defaulting every other tournament, finalizing ratings, marking an applicant
  rejected) — name the consequence, not just the action.
- create_tournament (real bracket + schedule + teams) and create_custom_tournament (a lightweight results
  record with no teams/schedule, for tournaments run outside the platform) are two unrelated tools — don't
  use one when the manager means the other.
- create_tournament generates the full match schedule in the same call — there is no separate "create
  fixture"/"create matches" step and no tool to add a fixture to a tournament that already exists.
  Before calling create_tournament, call list_tournaments with a search for the title to check one doesn't
  already exist — if the manager asks to "create matches", "add the fixture", or anything similar for a
  tournament by name, that almost always means a tournament with that name already exists and already has
  its schedule; use get_tournament_status or list_matches on it instead of calling create_tournament again.
  create_tournament will refuse and return an error if a tournament with that exact title already exists —
  if that happens, look the existing one up and show it rather than retrying with a different title.
  The same duplicate check applies to create_auction, create_team, and create_custom_tournament.
- Only pass a username/password to create_team if the manager explicitly stated both, in their own message,
  in this conversation — never invent, guess, autocomplete, or reuse a credential from anywhere else.
  After creating the login, confirm the username was set up — don't repeat the password back in your reply.
- propose_knockout_seeding only computes and returns a proposed bracket — it does not create or save
  anything. Say so when you show it, and don't imply the bracket is live.
- Player applications and approved Player Cards use two different, unrelated ids — do not pass one where
  the other is expected. get_player_application/approve_player/unverify_player/reject_player_application
  take application_id (an internal number, e.g. 31). get_player_card takes player_id (the EZ#### code, e.g.
  "EZ0034", shown as the player's EZONE ID) and only works once a player is approved. find_best_players and
  list_player_applications each return BOTH fields for every player — application_id and player_id — read
  the field name, don't assume they're interchangeable or guess which one a value is from its shape.
  get_player_card has the rich profile (rating, badges, career goals, match/tournament history) that
  get_player_application doesn't have, and get_player_application doesn't have what get_player_card has.
  If a manager asks for a named player's details/history and get_player_application says not found, that
  usually means you passed a player_id where application_id was expected, or the player is verified and you
  should call get_player_card with their player_id instead — don't conclude the player doesn't exist without
  trying that.
- Use list_matches to find the correct match_id before calling publish_match_result — never guess it.
- If the manager names two teams ("Argentina vs Walkerz", "find the match between X and Y") without saying
  which tournament, use find_matches — it searches every tournament by team name and reports which
  tournament each match is in. Don't guess the matchup as a tournament title and call list_tournaments with
  it; a matchup is not a tournament name.
- Keep replies short and concrete, use markdown (bold, bullet/numbered lists) where it helps readability,
  and refer to teams/tournaments by name, not id, when talking to the manager.
"""

# Hard ceiling on model↔tool round-trips for a single chat turn, so a
# confused model can't loop forever burning API calls.
MAX_TOOL_ITERATIONS = 6

# If the active model times out, gets rate-limited, can't be reached at all,
# rejects the `tools` param outright, or produces a tool call the provider
# rejects as malformed — and hasn't produced a single token/tool-call yet
# this round — try the next model here instead of just failing. The last two
# are the model's own output being wrong rather than the provider being
# unavailable, but a different model is a legitimate fix for both, same as
# for a timeout. Ordered by how much headroom each
# has on Groq's free tier (per-model rate limits, not shared), fastest/most
# available first. Only tool-calling-capable Groq models: compound/
# compound-mini can't take a `tools` param at all, and NVIDIA is excluded
# because its own multi-minute cold starts would defeat the point of a fast
# fallback. Never switches mid-stream — see the got_any_chunk check below —
# so a partial response is always surfaced as a normal error, never mixed
# with a second model's continuation.
FALLBACK_MODELS = [
    'llama-3.1-8b-instant',
    'llama-3.3-70b-versatile',
    'openai/gpt-oss-120b',
    'openai/gpt-oss-20b',
    'qwen/qwen3.6-27b',
]
MAX_MODEL_FALLBACKS = 2

# How much prior conversation to resend each turn — the assistant is stateless
# server-side (history persistence is additive on top, see conversation_id
# below), and this just bounds the prompt.
MAX_HISTORY_MESSAGES = 20

# Tool results are stringified and truncated before going back to the model —
# keeps a huge query result from blowing the context window.
MAX_TOOL_RESULT_CHARS = 8000


def _sse(event_type, **data):
    """One Server-Sent-Events frame carrying a JSON payload."""
    return f'data: {json.dumps({"type": event_type, **data}, default=str)}\n\n'


def _auto_title(text):
    """Cheap heuristic title from the first user message — no extra LLM call,
    just first-sentence-or-first-N-chars, trimmed. Good enough for a sidebar
    label; the user can always rename it."""
    text = ' '.join(text.split())
    for sep in ('. ', '! ', '? ', '\n'):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    return (text[:47] + '…') if len(text) > 47 else (text or 'New chat')


def _next_fallback_model(tried_ids):
    """First FALLBACK_MODELS entry not already tried this turn, or None."""
    for model_id in FALLBACK_MODELS:
        if model_id not in tried_ids:
            return model_id
    return None


class AssistantModelsView(APIView):
    """GET /api/v1/assistant/models/ — the static selectable-model registry."""
    permission_classes = [IsManagerPermission]

    def get(self, request):
        return Response(AI_MODELS)


class AssistantModelSettingView(APIView):
    """GET current active {provider, model}; POST {model} to switch — takes
    effect immediately for every subsequent request, no restart needed."""
    permission_classes = [IsManagerPermission]

    def get(self, request):
        setting = AIRuntimeSetting.get_active()
        return Response({'provider': setting.provider, 'model': setting.model})

    def post(self, request):
        model_id = request.data.get('model')
        entry = get_model_entry(model_id) if model_id else None
        if not entry:
            return Response({'error': f"Unknown model '{model_id}'."}, status=400)

        setting = AIRuntimeSetting.get_active()
        setting.provider = entry['provider']
        setting.model = entry['id']
        setting.save()
        return Response({'provider': setting.provider, 'model': setting.model})


class AssistantWorkspacesView(APIView):
    """GET /api/v1/assistant/workspaces/ — drives the sidebar + suggestions."""
    permission_classes = [IsManagerPermission]

    def get(self, request):
        return Response(public_workspaces())


class ConversationListCreateView(APIView):
    permission_classes = [IsManagerPermission]

    def get(self, request):
        qs = Conversation.objects.filter(owner=request.user)

        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(messages__content__icontains=search)).distinct()

        show_archived = request.query_params.get('archived') == 'true'
        qs = qs.filter(is_archived=show_archived)

        limit = min(int(request.query_params.get('limit', 50) or 50), 200)
        offset = int(request.query_params.get('offset', 0) or 0)
        return Response(ConversationListSerializer(qs[offset:offset + limit], many=True).data)

    def post(self, request):
        workspace = get_workspace(request.data.get('workspace', 'general'))
        setting = AIRuntimeSetting.get_active()
        conversation = Conversation.objects.create(
            owner=request.user,
            workspace=workspace['id'],
            provider=setting.provider,
            model=setting.model,
        )
        return Response(ConversationDetailSerializer(conversation).data, status=201)


class ConversationDetailView(APIView):
    permission_classes = [IsManagerPermission]

    def _get(self, request, pk):
        return get_object_or_404(Conversation, pk=pk, owner=request.user)

    def get(self, request, pk):
        conversation = self._get(request, pk)
        return Response(ConversationDetailSerializer(conversation).data)

    def patch(self, request, pk):
        conversation = self._get(request, pk)
        for field in ('title', 'is_archived', 'is_pinned', 'is_favorite'):
            if field in request.data:
                setattr(conversation, field, request.data[field])
        conversation.save()
        return Response(ConversationDetailSerializer(conversation).data)

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=204)


class ConversationDuplicateView(APIView):
    permission_classes = [IsManagerPermission]

    def post(self, request, pk):
        original = get_object_or_404(Conversation, pk=pk, owner=request.user)
        copy = Conversation.objects.create(
            owner=request.user,
            title=f'{original.title} (copy)',
            workspace=original.workspace,
            provider=original.provider,
            model=original.model,
        )
        Message.objects.bulk_create([
            Message(conversation=copy, role=m.role, content=m.content, tool_calls=m.tool_calls)
            for m in original.messages.all()
        ])
        return Response(ConversationDetailSerializer(copy).data, status=201)


class AssistantChatView(APIView):
    """Streams a chat turn back as SSE: `token` events as the model's reply is
    generated, `tool_call` events as tools run, a `meta` event with
    latency/token/tool-count stats, and a final `done` (or `error`).

    Backward compatible: `{"messages": [...]}` alone behaves exactly as
    before (stateless, default provider/model, all tools). Additively
    accepts `conversation_id` (persists history + auto-titles), `workspace`
    (scopes tools + system prompt), and `model`/`provider` (per-turn override
    so the model switcher can change models without losing the conversation).
    """
    permission_classes = [IsManagerPermission]

    def post(self, request):
        incoming = request.data.get('messages')
        if not isinstance(incoming, list) or not incoming:
            return Response({'error': 'messages is required.'}, status=400)

        conversation = None
        conversation_id = request.data.get('conversation_id')
        if conversation_id:
            conversation = get_object_or_404(Conversation, pk=conversation_id, owner=request.user)

        workspace = get_workspace(request.data.get('workspace') or (conversation.workspace if conversation else 'general'))

        requested_model = request.data.get('model')
        if requested_model:
            entry = get_model_entry(requested_model)
            if not entry:
                return Response({'error': f"Unknown model '{requested_model}'."}, status=400)
            provider_name, model_name = entry['provider'], entry['id']
        elif conversation:
            provider_name, model_name = conversation.provider, conversation.model
        else:
            setting = AIRuntimeSetting.get_active()
            provider_name, model_name = setting.provider, setting.model

        try:
            provider = get_provider(provider_name)
        except ProviderError as e:
            return Response({'error': str(e)}, status=503)

        if provider.name == 'groq' and not provider.api_key:
            return Response({'error': 'AI assistant is not configured — set GROQ_API_KEY in the backend .env.'}, status=503)
        if provider.name == 'nvidia' and not provider.api_key:
            return Response({'error': 'AI assistant is not configured — set NVIDIA_API in the backend .env.'}, status=503)

        # Only role/content are trusted from the client — tool calls and tool
        # results are assembled fresh server-side on every request.
        system_prompt = BASE_SYSTEM_PROMPT + (f"\n\n{workspace['prompt_extra']}" if workspace['prompt_extra'] else '')
        messages = [{'role': 'system', 'content': system_prompt}]
        for m in incoming[-MAX_HISTORY_MESSAGES:]:
            if not isinstance(m, dict):
                continue
            role, content = m.get('role'), m.get('content')
            if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
                messages.append({'role': role, 'content': content})

        if messages[-1]['role'] != 'user':
            return Response({'error': 'The last message must be from the user.'}, status=400)

        user_message_text = messages[-1]['content']
        tool_schemas, tool_functions = filter_tools(workspace['tools'])
        user = request.user
        # No max_tokens cap here deliberately — different Groq models have very
        # different context windows (e.g. groq/compound rejects anything above
        # 8192, others allow far more), so a single hardcoded value inevitably
        # breaks some model. Omitting it lets each model use its own default.
        extra_kwargs = {'temperature': 1, 'top_p': 1, 'seed': 42}
        if provider.name == 'groq':
            extra_kwargs['stream_options'] = {'include_usage': True}

        def stream_events():
            nonlocal provider, model_name
            started_at = time.monotonic()
            total_tool_calls = 0
            total_tokens = 0
            all_tool_calls_for_persist = []
            final_content = ''
            error_label = None
            tried_models = {model_name}
            fallback_count = 0

            try:
                for _ in range(MAX_TOOL_ITERATIONS):
                    tool_calls_acc = {}
                    turn_content = ''
                    got_any_chunk = False

                    while True:
                        try:
                            for chunk in provider.tool_chat(
                                model=model_name, messages=messages, tools=tool_schemas, **extra_kwargs,
                            ):
                                got_any_chunk = True
                                if chunk.usage:
                                    total_tokens = chunk.usage.total_tokens or total_tokens

                                if chunk.content:
                                    turn_content += chunk.content
                                    yield _sse('token', content=chunk.content)

                                for tc_delta in chunk.tool_call_deltas:
                                    acc = tool_calls_acc.setdefault(
                                        tc_delta.index, {'id': None, 'name': None, 'arguments': ''}
                                    )
                                    if tc_delta.id:
                                        acc['id'] = tc_delta.id
                                    if tc_delta.name:
                                        acc['name'] = tc_delta.name
                                    if tc_delta.arguments:
                                        acc['arguments'] += tc_delta.arguments
                            break  # this round completed normally
                        except (
                            ProviderTimeoutError, ProviderRateLimitError, ProviderNetworkError,
                            ProviderToolsUnsupportedError, ProviderToolCallError,
                        ) as e:
                            if got_any_chunk or fallback_count >= MAX_MODEL_FALLBACKS:
                                raise
                            next_model = _next_fallback_model(tried_models)
                            if not next_model:
                                raise
                            fallback_count += 1
                            tried_models.add(next_model)
                            old_model_name = model_name
                            model_name = next_model
                            provider = get_provider(get_model_entry(next_model)['provider'])
                            yield _sse(
                                'fallback', from_model=old_model_name, to_model=model_name,
                                reason=str(e) or 'The previous model was too slow or unavailable.',
                            )
                            # Loop again from the top of `while True` with the new
                            # model/provider — tool_calls_acc/turn_content are still
                            # empty (nothing was received from the failed model).

                    if not tool_calls_acc:
                        final_content = turn_content
                        yield _sse('meta', latency_ms=int((time.monotonic() - started_at) * 1000),
                                    tokens=total_tokens, tool_calls=total_tool_calls,
                                    provider=provider.name, model=model_name)
                        yield _sse('done')
                        return

                    ordered_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
                    messages.append({
                        'role': 'assistant',
                        'content': turn_content or None,
                        'tool_calls': [
                            {
                                'id': tc['id'],
                                'type': 'function',
                                'function': {'name': tc['name'], 'arguments': tc['arguments']},
                            }
                            for tc in ordered_calls
                        ],
                    })

                    for tc in ordered_calls:
                        name = tc['name']
                        try:
                            args = json.loads(tc['arguments'] or '{}')
                        except (json.JSONDecodeError, TypeError):
                            args = {}

                        func = tool_functions.get(name)
                        if not func:
                            result = {'error': f'Unknown or unavailable tool in this workspace: {name}'}
                        else:
                            try:
                                result = func(user, **args)
                            except ToolError as e:
                                result = {'error': str(e)}
                            except TypeError as e:
                                result = {'error': f'Invalid arguments for {name}: {e}'}
                            except Exception as e:
                                result = {'error': f'{name} failed: {e}'}

                        total_tool_calls += 1
                        # The real args go to the tool function above; only a
                        # redacted copy (password etc. masked) is ever streamed
                        # to the client or persisted into chat history.
                        safe_args = redact_args(args)
                        all_tool_calls_for_persist.append({'name': name, 'arguments': safe_args, 'result': result})
                        yield _sse('tool_call', name=name, arguments=safe_args, result=result)
                        messages.append({
                            'role': 'tool',
                            'tool_call_id': tc['id'],
                            'content': json.dumps(result, default=str)[:MAX_TOOL_RESULT_CHARS],
                        })

                error_label = "step limit"
                yield _sse('error', message="I couldn't finish that within my step limit — try a smaller request.")
            except ProviderTimeoutError as e:
                error_label = 'timeout'
                yield _sse('error', message=str(e) or "The AI model didn't respond in time — it can be slow under load. Try again.")
            except ProviderAuthError as e:
                error_label = 'auth'
                yield _sse('error', message=str(e))
            except ProviderRateLimitError as e:
                error_label = 'rate_limit'
                yield _sse('error', message=str(e))
            except ProviderModelNotFoundError as e:
                error_label = 'model_not_found'
                yield _sse('error', message=str(e))
            except ProviderNetworkError as e:
                error_label = 'network'
                yield _sse('error', message=str(e))
            except ProviderError as e:
                error_label = 'provider'
                yield _sse('error', message=str(e))
            except Exception as e:
                error_label = 'unknown'
                yield _sse('error', message=f'Assistant request failed: {e}')
            finally:
                log_turn(
                    provider=provider.name, model=model_name,
                    latency_ms=(time.monotonic() - started_at) * 1000,
                    tokens=total_tokens, tool_call_count=total_tool_calls, error=error_label,
                )
                if conversation is not None:
                    is_first_turn = not conversation.messages.exists()
                    Message.objects.create(conversation=conversation, role='user', content=user_message_text)
                    if final_content or all_tool_calls_for_persist:
                        Message.objects.create(
                            conversation=conversation, role='assistant',
                            content=final_content, tool_calls=all_tool_calls_for_persist,
                        )
                    conversation.provider = provider.name
                    conversation.model = model_name
                    if is_first_turn:
                        conversation.title = _auto_title(user_message_text)
                    conversation.save()

        response = StreamingHttpResponse(stream_events(), content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response
