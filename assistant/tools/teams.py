"""Team DB tools — auction.Team, the model create_tournament already uses
internally and what the dashboard's "Teams DB" button opens. create_team can
mint a real captain login (username/password) exactly like the human form —
the password itself is redacted before being included in any SSE event or
persisted chat history (see tools/base.py's redact_args), so it never sits in
plaintext in the conversation record; the tool function itself still uses the
real value to create the account.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q

from auction.models import Team
from auction.serializers import TeamCreateSerializer, TeamRegistrySerializer, TeamSerializer

from .base import ToolError

User = get_user_model()


def list_teams(user, auction_id=None, search=None, **kwargs):
    qs = Team.objects.filter(created_by=user).distinct()
    if auction_id:
        qs = qs.filter(auctionteam__auction_id=auction_id)
    if search:
        qs = qs.filter(Q(name__icontains=search))
    return TeamSerializer(qs.order_by('name')[:100], many=True).data


def get_team_details(user, team_id, **kwargs):
    try:
        team = Team.objects.get(id=team_id, created_by=user)
    except Team.DoesNotExist:
        raise ToolError(f'No team with id {team_id} found for this manager.')
    return TeamRegistrySerializer(team).data


def create_team(user, name, username=None, password=None, primary_color=None, **kwargs):
    if not name or not isinstance(name, str):
        raise ToolError('name is required.')

    existing = Team.objects.filter(created_by=user, name__iexact=name).first()
    if existing:
        raise ToolError(
            f"A team called '{name}' already exists (id {existing.id}). "
            f"Use update_team instead of creating a duplicate."
        )

    if (username and not password) or (password and not username):
        raise ToolError('Provide both username and password to set up a captain login, or neither to skip it.')

    if username:
        serializer = TeamCreateSerializer(data={
            'name': name, 'username': username, 'password': password,
            **({'primary_color': primary_color} if primary_color else {}),
        })
        if not serializer.is_valid():
            raise ToolError(f'Invalid team data: {serializer.errors}')
        data = serializer.validated_data
        team = Team.objects.create(
            name=data['name'], primary_color=data.get('primary_color', '#1F3322'), created_by=user,
        )
        captain = User.objects.create_user(username=data['username'], password=data['password'], role='captain')
        captain.save()
        team.captain_username = data['username']
        team.save()
    else:
        team = Team.objects.create(
            name=name.strip(), primary_color=primary_color or '#1F3322', created_by=user,
        )

    return TeamSerializer(team).data


def update_team(user, team_id, name=None, primary_color=None, **kwargs):
    try:
        team = Team.objects.get(id=team_id, created_by=user)
    except Team.DoesNotExist:
        raise ToolError(f'No team with id {team_id} found for this manager.')

    payload = {}
    if name is not None:
        payload['name'] = name
    if primary_color is not None:
        payload['primary_color'] = primary_color
    if not payload:
        raise ToolError('Provide at least one of: name, primary_color.')

    serializer = TeamSerializer(team, data=payload, partial=True)
    if not serializer.is_valid():
        raise ToolError(f'Invalid team data: {serializer.errors}')
    serializer.save()
    return serializer.data


FUNCTIONS = {
    'list_teams': list_teams,
    'get_team_details': get_team_details,
    'create_team': create_team,
    'update_team': update_team,
}

SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'list_teams',
            'description': "List the manager's teams, optionally scoped to one auction or filtered by name search.",
            'parameters': {
                'type': 'object',
                'properties': {
                    'auction_id': {'type': 'string', 'description': 'Optional auction id to filter by.'},
                    'search': {'type': 'string', 'description': 'Optional name search.'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_team_details',
            'description': "Get a team's full profile: name, color, captain username, and which fixtures/auctions it's been part of.",
            'parameters': {
                'type': 'object',
                'properties': {'team_id': {'type': 'integer'}},
                'required': ['team_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'create_team',
            'description': (
                'Create a new team in the Teams DB. Only pass username/password if the manager explicitly '
                'stated both in their own message — never invent, guess, or reuse credentials from elsewhere. '
                'If given, this creates a real captain login the team can use to sign in.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'username': {'type': 'string', 'description': 'Only if the manager explicitly provided one.'},
                    'password': {'type': 'string', 'description': 'Only if the manager explicitly provided one.'},
                    'primary_color': {'type': 'string', 'description': 'Hex color, e.g. #1F3322.'},
                },
                'required': ['name'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'update_team',
            'description': "Edit a team's name or color. Does not change captain login credentials.",
            'parameters': {
                'type': 'object',
                'properties': {
                    'team_id': {'type': 'integer'},
                    'name': {'type': 'string'},
                    'primary_color': {'type': 'string'},
                },
                'required': ['team_id'],
            },
        },
    },
]
