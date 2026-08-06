"""Auction tools: create/find/detail only — no live control or bidding.
Auction control (start/pause/next_player/end_auction) and bidding are
websocket-driven, mutate real team balances, and have no clean undo once a
countdown fires — deliberately excluded from free-text tool-calling.
"""
from django.db.models import Q

from auction.models import Auction, AuctionTeam, Team
from auction.serializers import AuctionCreateSerializer, AuctionDetailSerializer, AuctionListSerializer

from .base import ToolError


def list_auctions(user, search=None, **kwargs):
    qs = Auction.objects.filter(manager=user)
    if search:
        qs = qs.filter(Q(title__icontains=search))
    auctions = qs.order_by('-created_at')[:50]
    return [
        {
            'id': a.id,
            'title': a.title,
            'status': a.status,
            'is_fixture_only': a.is_fixture_only,
            'total_teams': a.teams.count(),
            'created_at': a.created_at.isoformat(),
        }
        for a in auctions
    ]


def create_auction(
    user, title, team_ids, auction_type='ezone', base_balance=10000,
    max_players_per_team=15, time_limit=60, price_decrement=5,
    price_lock_enabled=False, custom_bid_disabled=False,
    quick_bid_increments=None, **kwargs
):
    if not title or not isinstance(title, str):
        raise ToolError('title is required.')
    if not team_ids or not isinstance(team_ids, list):
        raise ToolError('team_ids must be a non-empty list of team ids — use list_teams to find them.')

    existing = Auction.objects.filter(manager=user, title__iexact=title).first()
    if existing:
        raise ToolError(
            f"An auction called '{title}' already exists (id {existing.id}, status "
            f"{existing.status}). Use get_auction_details on that id instead of creating a duplicate."
        )

    payload = {
        'title': title,
        'auction_type': auction_type,
        'base_balance': base_balance,
        'max_players_per_team': max_players_per_team,
        'time_limit': time_limit,
        'price_decrement': price_decrement,
        'price_lock_enabled': price_lock_enabled,
        'custom_bid_disabled': custom_bid_disabled,
        'team_ids': team_ids,
    }
    if quick_bid_increments is not None:
        payload['quick_bid_increments'] = quick_bid_increments

    serializer = AuctionCreateSerializer(data=payload)
    if not serializer.is_valid():
        raise ToolError(f'Invalid auction data: {serializer.errors}')

    data = serializer.validated_data
    ids = data.pop('team_ids')
    data.pop('team_overrides', None)

    teams = Team.objects.filter(id__in=ids, created_by=user)
    if teams.count() != len(ids):
        raise ToolError('One or more team_ids are invalid or not owned by this manager — use list_teams to find valid ids.')

    auction = Auction.objects.create(manager=user, **data)
    for team in teams:
        AuctionTeam.objects.create(auction=auction, team=team, balance=auction.base_balance)

    return AuctionListSerializer(auction).data


def get_auction_details(user, auction_id, **kwargs):
    # AuctionDetailView's GET is deliberately unscoped (spectator screen), so
    # this queries directly with an explicit manager filter rather than
    # reusing that view.
    try:
        auction = Auction.objects.get(id=auction_id, manager=user)
    except Auction.DoesNotExist:
        raise ToolError(f'No auction with id {auction_id} found for this manager.')
    return AuctionDetailSerializer(auction).data


FUNCTIONS = {
    'list_auctions': list_auctions,
    'create_auction': create_auction,
    'get_auction_details': get_auction_details,
}

SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'list_auctions',
            'description': "List the manager's auctions (id, title, status, team count), optionally filtered by title search.",
            'parameters': {
                'type': 'object',
                'properties': {
                    'search': {'type': 'string', 'description': 'Optional title search.'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'create_auction',
            'description': (
                'Create a new auction with a list of existing team ids (use list_teams to find them). '
                'This only creates the auction config and attaches teams — it does not start bidding.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'team_ids': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'At least 1 existing team id.'},
                    'auction_type': {'type': 'string', 'description': "Default 'ezone'."},
                    'base_balance': {'type': 'integer', 'description': 'Starting budget per team. Default 10000.'},
                    'max_players_per_team': {'type': 'integer', 'description': 'Default 15.'},
                    'time_limit': {'type': 'integer', 'description': 'Seconds per bid round. Default 60.'},
                    'price_decrement': {'type': 'integer', 'description': 'Default 5.'},
                    'price_lock_enabled': {'type': 'boolean'},
                    'custom_bid_disabled': {'type': 'boolean'},
                    'quick_bid_increments': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'Default [5, 10, 25].'},
                },
                'required': ['title', 'team_ids'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_auction_details',
            'description': 'Get full detail on one auction: config, teams with balances, current player/bid, recent bids.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'auction_id': {'type': 'string', 'description': 'The auction id (e.g. EZN-XXXXXX).'},
                },
                'required': ['auction_id'],
            },
        },
    },
]
