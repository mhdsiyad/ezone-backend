"""Custom Tournament tools — a lightweight "results poster" record (title,
winner/runner-up text, banner copy) for tournaments played outside eZone's
own bracket engine. Unrelated to create_tournament/FixtureCompetition: no
teams, no schedule, no money. Banner/logo images aren't exposed here (no file
upload from chat) — only the text fields.
"""
from django.db.models import Q

from auction.models import CustomTournament
from auction.serializers import CustomTournamentSerializer

from .base import ToolError

_TEXT_FIELDS = (
    'title', 'format_type', 'status', 'completion_percentage',
    'winner_name', 'runner_up_name', 'winner_description_1',
    'winner_description_2', 'winner_quote', 'champions_squad',
)


def list_custom_tournaments(user, search=None, **kwargs):
    qs = CustomTournament.objects.filter(manager=user)
    if search:
        qs = qs.filter(Q(title__icontains=search))
    return CustomTournamentSerializer(qs.order_by('-created_at')[:50], many=True).data


def get_custom_tournament(user, custom_tournament_id, **kwargs):
    try:
        tournament = CustomTournament.objects.get(id=custom_tournament_id, manager=user)
    except CustomTournament.DoesNotExist:
        raise ToolError(f'No custom tournament with id {custom_tournament_id} found for this manager.')
    return CustomTournamentSerializer(tournament).data


def create_custom_tournament(user, title, **fields):
    if not title or not isinstance(title, str):
        raise ToolError('title is required.')

    existing = CustomTournament.objects.filter(manager=user, title__iexact=title).first()
    if existing:
        raise ToolError(
            f"A custom tournament called '{title}' already exists (id {existing.id}). "
            f"Use update_custom_tournament instead of creating a duplicate."
        )

    payload = {k: v for k, v in fields.items() if k in _TEXT_FIELDS and v is not None}
    payload['title'] = title

    serializer = CustomTournamentSerializer(data=payload)
    if not serializer.is_valid():
        raise ToolError(f'Invalid custom tournament data: {serializer.errors}')
    tournament = serializer.save(manager=user)
    return CustomTournamentSerializer(tournament).data


def update_custom_tournament(user, custom_tournament_id, **fields):
    try:
        tournament = CustomTournament.objects.get(id=custom_tournament_id, manager=user)
    except CustomTournament.DoesNotExist:
        raise ToolError(f'No custom tournament with id {custom_tournament_id} found for this manager.')

    payload = {k: v for k, v in fields.items() if k in _TEXT_FIELDS and v is not None}
    if not payload:
        raise ToolError(f'Provide at least one field to update: {", ".join(_TEXT_FIELDS)}.')

    serializer = CustomTournamentSerializer(tournament, data=payload, partial=True)
    if not serializer.is_valid():
        raise ToolError(f'Invalid custom tournament data: {serializer.errors}')
    serializer.save()
    return serializer.data


FUNCTIONS = {
    'list_custom_tournaments': list_custom_tournaments,
    'get_custom_tournament': get_custom_tournament,
    'create_custom_tournament': create_custom_tournament,
    'update_custom_tournament': update_custom_tournament,
}

SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'list_custom_tournaments',
            'description': (
                'List the manager\'s "custom tournaments" — lightweight results-poster records '
                '(title, winner/runner-up) for tournaments played outside eZone\'s own bracket engine. '
                'Not the same thing as create_tournament / get_tournament_status.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {'search': {'type': 'string', 'description': 'Optional title search.'}},
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_custom_tournament',
            'description': 'Get full detail on one custom tournament record.',
            'parameters': {
                'type': 'object',
                'properties': {'custom_tournament_id': {'type': 'integer'}},
                'required': ['custom_tournament_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'create_custom_tournament',
            'description': 'Record a new custom tournament (title + optional winner/runner-up text, status, description, quote).',
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'format_type': {'type': 'string', 'description': 'Free text, e.g. "Knockout", "League".'},
                    'status': {'type': 'string', 'enum': ['upcoming', 'ongoing', 'completed'], 'description': "Default 'upcoming'."},
                    'winner_name': {'type': 'string'},
                    'runner_up_name': {'type': 'string'},
                    'winner_description_1': {'type': 'string'},
                    'winner_description_2': {'type': 'string'},
                    'winner_quote': {'type': 'string'},
                    'champions_squad': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Optional list of squad player names.'},
                },
                'required': ['title'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'update_custom_tournament',
            'description': 'Edit an existing custom tournament record\'s fields.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'custom_tournament_id': {'type': 'integer'},
                    'title': {'type': 'string'},
                    'format_type': {'type': 'string'},
                    'status': {'type': 'string', 'enum': ['upcoming', 'ongoing', 'completed']},
                    'winner_name': {'type': 'string'},
                    'runner_up_name': {'type': 'string'},
                    'winner_description_1': {'type': 'string'},
                    'winner_description_2': {'type': 'string'},
                    'winner_quote': {'type': 'string'},
                    'champions_squad': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['custom_tournament_id'],
            },
        },
    },
]
