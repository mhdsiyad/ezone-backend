"""Player Application tools. Approving/unverifying reuse the same reversible
players.services logic the human review flow uses. Rejecting does NOT delete
the application (the human UI's "reject" button does — a permanent hard
delete of the row and its photo) — it sets is_rejected instead, so it's a
real, reversible status rather than data loss.

Two different ids are in play here, and they must not be confused: the
internal `application_id` (PlayerProfile.id, used to manage an application —
approve/unverify/reject/get_player_application) versus the public `player_id`
EZ#### code (used by get_player_card, assigned only once approved). Both
find_best_players and list_player_applications return a `player_id` field —
that value is the EZ#### code, not an application_id.
"""
from django.db.models import Q
from django.utils import timezone

from players.models import PlayerProfile
from players.serializers import (
    ManagerPlayerDetailSerializer,
    ManagerPlayerListSerializer,
    PublicPlayerDetailSerializer,
)
from players.services import unverify_player as _unverify_player
from players.services import verify_player as _verify_player

from .base import ToolError


def find_best_players(user, position=None, search=None, limit=10, **kwargs):
    qs = PlayerProfile.objects.filter(is_verified=True)
    if position:
        qs = qs.filter(position=position)
    if search:
        qs = qs.filter(name__icontains=search)
    limit = max(1, min(int(limit or 10), 50))
    players = qs.order_by('-rating')[:limit]
    return [
        {
            'application_id': p.id,
            'player_id': p.player_id,
            'name': p.name,
            'position': p.position,
            'rating': p.rating,
            'country': p.country,
        }
        for p in players
    ]


def list_player_applications(user, is_verified=None, is_rejected=None, contacted=None, search=None, **kwargs):
    qs = PlayerProfile.objects.all().order_by('-applied_at')
    if is_verified is not None:
        qs = qs.filter(is_verified=bool(is_verified))
    if is_rejected is not None:
        qs = qs.filter(is_rejected=bool(is_rejected))
    if contacted is not None:
        qs = qs.filter(contacted=bool(contacted))
    if search:
        qs = qs.filter(
            Q(name__icontains=search) | Q(player_id__icontains=search)
            | Q(efootball_id__icontains=search) | Q(phone_number__icontains=search)
        )
    # 'id' here is the application_id other player tools take; 'player_id' is
    # the separate EZ#### code get_player_card takes — both are present in
    # ManagerPlayerListSerializer's output so the model can pick the right one.
    return ManagerPlayerListSerializer(qs[:50], many=True).data


def get_player_application(user, application_id, **kwargs):
    try:
        player = PlayerProfile.objects.get(id=application_id)
    except PlayerProfile.DoesNotExist:
        raise ToolError(f'No player application with id {application_id}.')
    return ManagerPlayerDetailSerializer(player).data


def approve_player(user, application_id, **kwargs):
    try:
        player = PlayerProfile.objects.get(id=application_id)
    except PlayerProfile.DoesNotExist:
        raise ToolError(f'No player application with id {application_id}.')
    _verify_player(player)
    return ManagerPlayerDetailSerializer(player).data


def unverify_player(user, application_id, **kwargs):
    try:
        player = PlayerProfile.objects.get(id=application_id)
    except PlayerProfile.DoesNotExist:
        raise ToolError(f'No player application with id {application_id}.')
    _unverify_player(player)
    return ManagerPlayerDetailSerializer(player).data


def get_player_card(user, player_code, **kwargs):
    """The rich public "Player Card" for an approved player — rating, badges,
    career goals, matches/wins/win-rate, and match/tournament history. This is
    a different, richer view than get_player_application: that one is the
    manager's review-queue record (contact info, notes, status); this is the
    player's own profile/stat card, and only exists once they're verified.
    """
    try:
        player = PlayerProfile.objects.get(player_id=player_code, is_verified=True)
    except PlayerProfile.DoesNotExist:
        raise ToolError(
            f"No verified player with EZONE id '{player_code}'. This only works for approved "
            f"players — use list_player_applications/get_player_application for applications "
            f"that aren't verified yet."
        )
    return PublicPlayerDetailSerializer(player).data


def reject_player_application(user, application_id, **kwargs):
    try:
        player = PlayerProfile.objects.get(id=application_id)
    except PlayerProfile.DoesNotExist:
        raise ToolError(f'No player application with id {application_id}.')
    player.is_rejected = True
    player.rejected_at = timezone.now()
    player.save(update_fields=['is_rejected', 'rejected_at'])
    return ManagerPlayerDetailSerializer(player).data


FUNCTIONS = {
    'find_best_players': find_best_players,
    'list_player_applications': list_player_applications,
    'get_player_application': get_player_application,
    'get_player_card': get_player_card,
    'approve_player': approve_player,
    'unverify_player': unverify_player,
    'reject_player_application': reject_player_application,
}

SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'find_best_players',
            'description': (
                'Find the highest-rated verified players, optionally filtered by position or name search. '
                'Returns both application_id (for get_player_application/approve/reject) and player_id '
                '(the EZ#### code, for get_player_card) for each player — they are not the same value.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'position': {
                        'type': 'string',
                        'enum': ['GK', 'CB', 'RB', 'LB', 'DMF', 'CMF', 'AMF', 'RWF', 'LWF', 'SS', 'CF'],
                        'description': 'Optional position filter.',
                    },
                    'search': {'type': 'string', 'description': 'Optional name search.'},
                    'limit': {'type': 'integer', 'description': 'Max results, default 10, max 50.'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'list_player_applications',
            'description': (
                'List player applications with full detail (contact info, notes, verification/rejection status). '
                'Use is_verified=false and is_rejected=false to see the pending review queue. Each result has '
                'both id (the application_id for get_player_application/approve/reject) and player_id (the '
                'EZ#### code for get_player_card, only set once approved) — they are not the same value.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'is_verified': {'type': 'boolean'},
                    'is_rejected': {'type': 'boolean'},
                    'contacted': {'type': 'boolean'},
                    'search': {'type': 'string', 'description': 'Matches name, player_id, efootball_id, or phone number.'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_player_application',
            'description': (
                "Get one player application's full detail (contact info, notes, status) by its application_id "
                "— not the player_id/EZ#### code. For an approved player's rating/badges/history, use "
                "get_player_card with their player_id instead."
            ),
            'parameters': {
                'type': 'object',
                'properties': {'application_id': {'type': 'integer', 'description': "The application's internal id (from list_player_applications' id field), not the EZ#### player_id."}},
                'required': ['application_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_player_card',
            'description': (
                "Get an approved player's full public Player Card: rating, badges, career goals, "
                "matches played/wins/win-rate, and match/tournament history. Only works for verified "
                "players — for a pending application use get_player_application instead."
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'player_code': {'type': 'string', 'description': "The player's EZ#### id, e.g. 'EZ0034' (their EZONE ID / player_id field) — NOT the application_id used by the other player tools."},
                },
                'required': ['player_code'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'approve_player',
            'description': (
                'Approve/verify a player application by its application_id — assigns an EZ#### player_id on '
                'first approval. Reversible via unverify_player.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {'application_id': {'type': 'integer'}},
                'required': ['application_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'unverify_player',
            'description': 'Undo a player approval by its application_id — clears is_verified without deleting anything.',
            'parameters': {
                'type': 'object',
                'properties': {'application_id': {'type': 'integer'}},
                'required': ['application_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'reject_player_application',
            'description': (
                'Reject a player application by its application_id — marks it rejected (is_rejected=true), '
                'does NOT delete it or their data. Restate that you are about to reject the named application '
                'and get a clear yes before calling this.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {'application_id': {'type': 'integer'}},
                'required': ['application_id'],
            },
        },
    },
]
