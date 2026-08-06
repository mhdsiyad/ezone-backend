"""Fixture/tournament tools. Knockout-stage *creation* is deliberately not
wired up here — FixtureKnockoutCreateView soft-deletes any existing matches
for that stage every time it's re-run, which would silently wipe recorded
results if the model called it a second time. propose_knockout_seeding is
the safe read-only half of that flow (computes a proposed bracket without
persisting anything); committing it stays a human-only action in the app's
own UI for now.
"""
import random

from django.db.models import Q

from auction.models import Auction, AuctionTeam, FixtureCompetition, FixtureMatch, Team
from auction.serializers import (
    FixtureCompetitionListSerializer,
    FixtureCompetitionStatusUpdateSerializer,
    FixtureMatchSerializer,
)
from auction.stats import _fixture_group_tables, _fixture_player_stats, _fixture_table
from auction.views import FixtureCreationError, _create_fixture_competition

from .base import ToolError

STAGE_BY_QUALIFIER_COUNT = {2: 'final', 4: 'semi', 8: 'quarter', 16: 'round_of_16', 32: 'round_of_32'}


def _get_owned_competition(user, competition_id):
    try:
        return FixtureCompetition.objects.get(id=competition_id, auction__manager=user)
    except FixtureCompetition.DoesNotExist:
        raise ToolError(f'No tournament with id {competition_id} found for this manager.')


def list_tournaments(user, auction_id=None, status=None, search=None, **kwargs):
    qs = FixtureCompetition.objects.filter(auction__manager=user)
    if auction_id:
        qs = qs.filter(auction_id=auction_id)
    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(Q(title__icontains=search))
    return FixtureCompetitionListSerializer(qs.order_by('-created_at')[:50], many=True).data


def get_tournament_status(user, competition_id, **kwargs):
    competition = _get_owned_competition(user, competition_id)

    data = FixtureCompetitionListSerializer(competition).data
    matches = competition.matches.select_related('home_team', 'away_team').order_by('match_day', 'order')
    data['teams'] = list(competition.teams.values_list('name', flat=True))
    data['upcoming_matches'] = FixtureMatchSerializer(matches.filter(status='upcoming')[:10], many=True).data
    data['recent_results'] = FixtureMatchSerializer(matches.filter(status='completed').order_by('-played_at')[:10], many=True).data

    if competition.format_type == 'group_stage' and competition.groups.exists():
        data['group_tables'] = _fixture_group_tables(competition)
    else:
        data['standings'] = _fixture_table(competition)

    return data


def get_fixture_player_stats(user, competition_id, **kwargs):
    competition = _get_owned_competition(user, competition_id)
    goal_stats, defence_stats, meta = _fixture_player_stats(competition)
    return {'goal_stats': goal_stats[:20], 'defence_stats': defence_stats[:20], 'meta': meta}


def list_matches(user, competition_id, match_day=None, status=None, stage=None, **kwargs):
    competition = _get_owned_competition(user, competition_id)

    qs = competition.matches.select_related('home_team', 'away_team').order_by('match_day', 'order')
    if match_day is not None:
        qs = qs.filter(match_day=match_day)
    if status:
        qs = qs.filter(status=status)
    if stage:
        qs = qs.filter(stage=stage)
    return FixtureMatchSerializer(qs[:100], many=True).data


def find_matches(user, team=None, opponent=None, status=None, **kwargs):
    """list_matches needs a competition_id you already know — this is for the
    opposite case: "find the match(es) between these teams" without knowing
    which tournament it's in. Searches by team name across every tournament
    this manager owns and reports which tournament each match belongs to.
    """
    if not team and not opponent:
        raise ToolError('Provide at least team (a team name to search for).')

    qs = FixtureMatch.objects.filter(competition__auction__manager=user).select_related(
        'home_team', 'away_team', 'competition',
    )
    if team and opponent:
        qs = qs.filter(
            (Q(home_team__name__icontains=team) & Q(away_team__name__icontains=opponent))
            | (Q(home_team__name__icontains=opponent) & Q(away_team__name__icontains=team))
        )
    elif team:
        qs = qs.filter(Q(home_team__name__icontains=team) | Q(away_team__name__icontains=team))
    if status:
        qs = qs.filter(status=status)

    matches = qs.order_by('-competition__created_at', 'match_day', 'order')[:50]
    return [
        {
            'match_id': m.id,
            'tournament_id': m.competition_id,
            'tournament_title': m.competition.title,
            'home_team': m.home_team.name,
            'away_team': m.away_team.name,
            'stage': m.stage,
            'match_day': m.match_day,
            'status': m.status,
            'home_score': m.home_score,
            'away_score': m.away_score,
            'played_at': m.played_at,
        }
        for m in matches
    ]


def propose_knockout_seeding(user, competition_id, **kwargs):
    competition = _get_owned_competition(user, competition_id)

    groups = list(competition.groups.all().order_by('order', 'id'))
    if not groups:
        raise ToolError('This tournament has no groups — knockout seeding only applies to group_stage tournaments.')

    advance = competition.teams_per_group_advance
    group_tables = _fixture_group_tables(competition)

    qualifiers = []
    for entry in group_tables:
        for row in entry['table'][:advance]:
            qualifiers.append({
                'id': row['team_id'], 'name': row['team_name'],
                'group_id': entry['group_id'], 'group_name': entry['group_name'],
            })

    stage = STAGE_BY_QUALIFIER_COUNT.get(len(qualifiers))
    if not stage:
        raise ToolError(
            f'{len(qualifiers)} qualifying teams do not form a valid bracket size — '
            f'groups × teams-per-group-advance must total 2, 4, 8, 16, or 32.'
        )

    remaining = list(qualifiers)
    random.shuffle(remaining)
    pairs = []
    while remaining:
        a = remaining.pop(0)
        candidates = [i for i, b in enumerate(remaining) if b['group_id'] != a['group_id']]
        idx = random.choice(candidates) if candidates else 0
        b = remaining.pop(idx)
        pairs.append({'home': a, 'away': b})

    return {
        'stage': stage,
        'pairs': pairs,
        'note': (
            'This is a proposed bracket only — nothing has been created. Creating the actual '
            'knockout stage isn\'t available through chat yet; the manager needs to submit this '
            'in the Fixtures UI to commit it.'
        ),
    }


def update_tournament_settings(
    user, competition_id, title=None, status=None,
    winner_team_id=None, runner_up_team_id=None, is_default=None, **kwargs
):
    competition = _get_owned_competition(user, competition_id)

    if title is not None:
        if not str(title).strip():
            raise ToolError('title cannot be empty.')
        competition.title = str(title).strip()

    payload = {}
    if status is not None:
        if status not in dict(FixtureCompetition.STATUS_CHOICES):
            raise ToolError(f'status must be one of {list(dict(FixtureCompetition.STATUS_CHOICES))}.')
        payload['status'] = status
    if winner_team_id is not None:
        if not competition.teams.filter(id=winner_team_id).exists():
            raise ToolError(f'Team {winner_team_id} is not part of this tournament.')
        payload['winner'] = winner_team_id
    if runner_up_team_id is not None:
        if not competition.teams.filter(id=runner_up_team_id).exists():
            raise ToolError(f'Team {runner_up_team_id} is not part of this tournament.')
        payload['runner_up'] = runner_up_team_id
    if is_default is not None:
        payload['is_default'] = bool(is_default)

    if not payload and title is None:
        raise ToolError('Provide at least one of: title, status, winner_team_id, runner_up_team_id, is_default.')

    if payload:
        serializer = FixtureCompetitionStatusUpdateSerializer(competition, data=payload, partial=True)
        if not serializer.is_valid():
            raise ToolError(f'Invalid tournament settings: {serializer.errors}')
        if serializer.validated_data.get('is_default'):
            FixtureCompetition.objects.exclude(id=competition.id).update(is_default=False)
        serializer.save()
        if competition.status == 'completed':
            from players.rating_engine import handle_competition_completed
            handle_competition_completed(competition)
    else:
        competition.save(update_fields=['title'])

    return FixtureCompetitionListSerializer(competition).data


def create_tournament(
    user, title, teams, match_type='team', format_type='ezone_custom',
    matches_per_pair=1, match_days=1, teams_per_group_advance=2,
    semifinal_qualifiers=4, group_count=None, **kwargs
):
    if not title or not isinstance(title, str):
        raise ToolError('title is required.')
    if not teams or not isinstance(teams, list) or len(teams) < 2:
        raise ToolError('teams must be a list of at least 2 team names.')
    if match_type not in dict(FixtureCompetition.MATCH_TYPE_CHOICES):
        raise ToolError(f'match_type must be one of {list(dict(FixtureCompetition.MATCH_TYPE_CHOICES))}.')
    if format_type not in dict(FixtureCompetition.FORMAT_TYPE_CHOICES):
        raise ToolError(f'format_type must be one of {list(dict(FixtureCompetition.FORMAT_TYPE_CHOICES))}.')

    names = [str(t).strip() for t in teams if str(t).strip()]
    if len(names) != len(set(n.lower() for n in names)):
        raise ToolError('Team names must be unique.')

    # A tournament with this title already exists for this manager — creating
    # another one silently would duplicate the auction, the teams, and the
    # whole match schedule. This most often happens when the model is asked a
    # vague follow-up ("create matches", "add the fixture") for a tournament
    # that already has its schedule, and re-calls this tool instead of
    # looking the existing one up — raising here gives it a chance to recover
    # (call get_tournament_status/list_matches) instead of creating junk data.
    existing = FixtureCompetition.objects.filter(
        auction__manager=user, auction__is_deleted=False, title__iexact=title,
    ).first()
    if existing:
        raise ToolError(
            f"A tournament called '{title}' already exists (id {existing.id}, status "
            f"{existing.status}). Call get_tournament_status or list_matches on that id instead "
            f"of creating a duplicate — only create a new tournament if the manager explicitly "
            f"confirms they want a second one with this name."
        )

    auction = Auction.objects.create(manager=user, title=title, is_fixture_only=True)

    team_objs = []
    for name in names:
        team = Team.objects.create(name=name, created_by=user)
        AuctionTeam.objects.create(auction=auction, team=team, balance=auction.base_balance)
        team_objs.append(team)

    data = {
        'title': title,
        'match_type': match_type,
        'format_type': format_type,
        'matches_per_pair': matches_per_pair,
        'match_days': match_days,
        'semifinal_qualifiers': semifinal_qualifiers,
        'teams_per_group_advance': teams_per_group_advance,
        'group_count': group_count,
    }
    try:
        competition = _create_fixture_competition(auction, team_objs, data)
    except FixtureCreationError as e:
        # Rolls back the auction created moments ago for this call — nothing
        # else has referenced it yet.
        auction.delete()
        raise ToolError(str(e))

    return FixtureCompetitionListSerializer(competition).data


def publish_match_result(user, match_id, home_score, away_score, status='completed', **kwargs):
    try:
        match = FixtureMatch.objects.select_related('competition').get(
            id=match_id, competition__auction__manager=user,
        )
    except FixtureMatch.DoesNotExist:
        raise ToolError(f'No match with id {match_id} found for this manager.')

    if status not in {'upcoming', 'completed'}:
        raise ToolError("status must be 'upcoming' or 'completed'.")

    from django.utils import timezone

    match.home_score = max(0, int(home_score or 0))
    match.away_score = max(0, int(away_score or 0))
    match.status = status
    if match.status == 'completed' and not match.played_at:
        match.played_at = timezone.now()
    if match.status == 'upcoming':
        match.played_at = None
    match.save(update_fields=['home_score', 'away_score', 'status', 'played_at'])

    if match.competition.status == 'completed':
        from players.rating_engine import handle_competition_completed
        handle_competition_completed(match.competition)
    else:
        from players.rating_engine import recompute_competition_profiles
        recompute_competition_profiles(match.competition)

    return FixtureMatchSerializer(match).data


FUNCTIONS = {
    'list_tournaments': list_tournaments,
    'get_tournament_status': get_tournament_status,
    'get_fixture_player_stats': get_fixture_player_stats,
    'list_matches': list_matches,
    'find_matches': find_matches,
    'propose_knockout_seeding': propose_knockout_seeding,
    'update_tournament_settings': update_tournament_settings,
    'create_tournament': create_tournament,
    'publish_match_result': publish_match_result,
}

SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'list_tournaments',
            'description': 'List tournaments (fixture competitions) with their status and progress. Use this to check tournament status.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'auction_id': {'type': 'string', 'description': 'Optional auction id to filter by.'},
                    'status': {'type': 'string', 'enum': ['upcoming', 'ongoing', 'completed'], 'description': 'Optional status filter.'},
                    'search': {'type': 'string', 'description': 'Optional title search.'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_tournament_status',
            'description': 'Get full detail on one tournament: status, teams, upcoming matches, recent results, and the standings/group table.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'competition_id': {'type': 'integer', 'description': 'The tournament (fixture competition) id.'},
                },
                'required': ['competition_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_fixture_player_stats',
            'description': 'Get top scorers and best defensive players for a tournament.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'competition_id': {'type': 'integer'},
                },
                'required': ['competition_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'list_matches',
            'description': (
                'List matches within a tournament, optionally filtered by match day, status, or bracket stage. '
                'Use this to find the match_id needed to publish a result, or to read the knockout bracket '
                '(stage=quarter/semi/final/round_of_16/round_of_32).'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'competition_id': {'type': 'integer', 'description': 'The tournament id.'},
                    'match_day': {'type': 'integer', 'description': 'Optional match day number to filter by.'},
                    'status': {'type': 'string', 'enum': ['upcoming', 'completed'], 'description': 'Optional status filter.'},
                    'stage': {
                        'type': 'string',
                        'enum': ['league', 'round_of_32', 'round_of_16', 'quarter', 'semi', 'final'],
                        'description': 'Optional bracket stage filter.',
                    },
                },
                'required': ['competition_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'find_matches',
            'description': (
                "Find match(es) by team name(s) across every one of this manager's tournaments, without "
                "needing to know which tournament it's in first — reports the tournament name/id for each "
                "match found. Use this for \"X vs Y\" style requests instead of guessing at list_tournaments "
                "with the matchup as a title search."
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'team': {'type': 'string', 'description': 'A team name to search for (partial match ok).'},
                    'opponent': {'type': 'string', 'description': 'Optional second team name, to find matches specifically between team and opponent.'},
                    'status': {'type': 'string', 'enum': ['upcoming', 'completed']},
                },
                'required': ['team'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'propose_knockout_seeding',
            'description': (
                'Compute a proposed randomized cross-group knockout bracket from current group standings '
                '(group_stage tournaments only). Read-only — does not create anything. Show the manager the '
                'proposed pairs; committing them still requires the Fixtures UI.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {'competition_id': {'type': 'integer'}},
                'required': ['competition_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'update_tournament_settings',
            'description': (
                'Update a tournament\'s title, status, winner/runner-up, or default flag. '
                'Setting is_default=true un-defaults every other tournament for this manager. '
                'Setting status="completed" finalizes player ratings for this tournament — restate '
                'these effects and get confirmation before calling.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'competition_id': {'type': 'integer'},
                    'title': {'type': 'string'},
                    'status': {'type': 'string', 'enum': ['upcoming', 'ongoing', 'completed']},
                    'winner_team_id': {'type': 'integer', 'description': 'Must be a team already in this tournament.'},
                    'runner_up_team_id': {'type': 'integer', 'description': 'Must be a team already in this tournament.'},
                    'is_default': {'type': 'boolean'},
                },
                'required': ['competition_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'create_tournament',
            'description': 'Create a new tournament with a list of team names. Generates the schedule automatically based on format_type.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'teams': {
                        'type': 'array', 'items': {'type': 'string'},
                        'description': 'At least 2 unique team names.',
                    },
                    'match_type': {'type': 'string', 'enum': ['single', 'team'], 'description': "Default 'team'."},
                    'format_type': {
                        'type': 'string',
                        'enum': ['ezone_custom', 'top4_semi', 'top2_final', 'knockout', 'group_stage'],
                        'description': "League format. Default 'ezone_custom' (round-robin league).",
                    },
                    'matches_per_pair': {'type': 'integer', 'description': 'Default 1.'},
                    'match_days': {'type': 'integer', 'description': 'Default 1.'},
                    'group_count': {'type': 'integer', 'description': 'Only used when format_type is group_stage.'},
                },
                'required': ['title', 'teams'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'publish_match_result',
            'description': 'Publish (or correct) the score for a specific match day fixture. Use list_matches first to find the match_id.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'match_id': {'type': 'integer'},
                    'home_score': {'type': 'integer'},
                    'away_score': {'type': 'integer'},
                    'status': {'type': 'string', 'enum': ['upcoming', 'completed'], 'description': "Default 'completed'."},
                },
                'required': ['match_id', 'home_score', 'away_score'],
            },
        },
    },
]
