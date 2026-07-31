import math

from .models import FixtureLineup, FixtureMatch


def _fixture_table(competition, request=None, teams=None, matches_qs=None):
    teams = list(teams) if teams is not None else list(competition.teams.all())
    rows = {
        team.id: {
            'team_id': team.id,
            'team_name': team.name,
            'team_logo': request.build_absolute_uri(team.logo.url) if team.logo and request else (team.logo.url if team.logo else None),
            'team_color': team.primary_color,
            'played': 0,
            'won': 0,
            'drawn': 0,
            'lost': 0,
            'goals_for': 0,
            'goals_against': 0,
            'goal_difference': 0,
            'points': 0,
        }
        for team in teams
    }

    if matches_qs is None:
        matches_qs = FixtureMatch.objects.filter(competition=competition, status='completed')
    matches = matches_qs.select_related('home_team', 'away_team').prefetch_related('lineups')
    for match in matches:
        home = rows.get(match.home_team_id)
        away = rows.get(match.away_team_id)
        if not home or not away:
            continue

        home['played'] += 1
        away['played'] += 1

        lineups = list(match.lineups.all())
        if competition.match_type == 'team' and lineups:
            home_goals = sum(l.home_goals for l in lineups)
            away_goals = sum(l.away_goals for l in lineups)
        else:
            home_goals = match.home_score
            away_goals = match.away_score

        home['goals_for'] += home_goals
        home['goals_against'] += away_goals
        away['goals_for'] += away_goals
        away['goals_against'] += home_goals

        # Penalties are shot per player set, not per fixture. For a team tournament
        # that is already baked into the sets-won score, so a level score there is a
        # real draw (the manager adds a decider set to separate them). For single
        # matches the set's shootout is what breaks a level scoreline. Shootout goals
        # are never added to goals for/against.
        penalty_home = penalty_away = 0
        if match.home_score == match.away_score and competition.match_type != 'team':
            for l in lineups:
                if l.penalty_shootout:
                    penalty_home += l.home_penalty
                    penalty_away += l.away_penalty
        penalty_decided = penalty_home != penalty_away

        if match.home_score > match.away_score or (penalty_decided and penalty_home > penalty_away):
            home['won'] += 1
            away['lost'] += 1
            home['points'] += 3
        elif match.home_score < match.away_score or penalty_decided:
            away['won'] += 1
            home['lost'] += 1
            away['points'] += 3
        else:
            home['drawn'] += 1
            away['drawn'] += 1
            home['points'] += 1
            away['points'] += 1

    for row in rows.values():
        row['goal_difference'] = row['goals_for'] - row['goals_against']

    return sorted(
        rows.values(),
        key=lambda row: (
            row['points'],
            row['goal_difference'],
            row['goals_for'],
            row['team_name'].lower(),
        ),
        reverse=True,
    )


def _fixture_group_tables(competition, request=None):
    groups = competition.groups.prefetch_related('teams').order_by('order', 'id')
    result = []
    for group in groups:
        group_teams = list(group.teams.all())
        matches_qs = FixtureMatch.objects.filter(
            competition=competition, status='completed', group=group
        )
        result.append({
            'group_id': group.id,
            'group_name': group.name,
            'table': _fixture_table(competition, request, teams=group_teams, matches_qs=matches_qs),
        })
    return result


def _fixture_player_stats(competition, request=None):
    stats = {}
    lineups = FixtureLineup.objects.filter(
        match__competition=competition,
        match__status='completed',
    ).select_related(
        'home_player',
        'away_player',
        'home_roster_entry__profile',
        'away_roster_entry__profile',
        'match__home_team',
        'match__away_team',
    )

    def ensure(player, roster_entry, team):
        if not player and not roster_entry:
            return None
        stat_id = f"roster-{roster_entry.id}" if roster_entry else f"player-{player.id}"
        player_name = roster_entry.name if roster_entry else player.name
        player_id = roster_entry.player_id if roster_entry else player.id
        roster_entry_id = roster_entry.id if roster_entry else None
        profile_player_id = roster_entry.profile.player_id if roster_entry and roster_entry.profile_id else None
        if stat_id not in stats:
            stats[stat_id] = {
                'player_id': player_id,
                'roster_entry_id': roster_entry_id,
                'profile_player_id': profile_player_id,
                'player_name': player_name,
                'team_id': team.id,
                'team_name': team.name,
                'team_logo': request.build_absolute_uri(team.logo.url) if team.logo and request else (team.logo.url if team.logo else None),
                'team_color': team.primary_color,
                'goals': 0,
                'goals_against': 0,
                'matches': 0,
                # Track per-match goals conceded to correctly aggregate defence stats
                '_match_goals_against': {},  # match_id -> goals conceded in that match
            }
        return stats[stat_id]

    for lineup in lineups:
        home = ensure(lineup.home_player, lineup.home_roster_entry, lineup.match.home_team)
        away = ensure(lineup.away_player, lineup.away_roster_entry, lineup.match.away_team)
        match_id = lineup.match_id

        if home:
            home['goals'] += lineup.home_goals
            # Accumulate away goals (goals against home player) per match
            home['_match_goals_against'].setdefault(match_id, 0)
            home['_match_goals_against'][match_id] += lineup.away_goals

        if away:
            away['goals'] += lineup.away_goals
            # Accumulate home goals (goals against away player) per match
            away['_match_goals_against'].setdefault(match_id, 0)
            away['_match_goals_against'][match_id] += lineup.home_goals

    # Compute final match-level defence totals
    for stat in stats.values():
        match_goals = stat.pop('_match_goals_against', {})
        stat['matches'] = len(match_goals)
        stat['goals_against'] = sum(match_goals.values())

    # Count total distinct completed league match days for the 80% minimum rule
    completed_league_match_days = FixtureMatch.objects.filter(
        competition=competition,
        stage='league',
        status='completed',
    ).values_list('match_day', flat=True).distinct().count()

    # A player must have played in at least 80% of completed league match days
    # Use ceil so partial fractions always round UP (e.g. 80% of 5 days = 4.0 → needs 4 matches)
    min_matches_required = max(1, math.ceil(completed_league_match_days * 0.80))

    goal_stats = sorted(
        stats.values(),
        key=lambda row: (row['goals'], -row['goals_against'], row['player_name'].lower()),
        reverse=True,
    )
    # Defence: Include all players who played >= 1 match.
    # Sort order:
    # 1. Qualified first (0) vs unqualified (1) based on the 80% rule
    # 2. Total goals conceded (ascending)
    # 3. Matches played (descending)
    defence_stats = sorted(
        [row for row in stats.values() if row['matches'] > 0],
        key=lambda row: (
            0 if row['matches'] >= min_matches_required else 1,
            row['goals_against'],
            -row['matches'],
            row['player_name'].lower(),
        ),
    )
    stats_meta = {
        'defence_min_matches': min_matches_required,
        'defence_total_match_days': completed_league_match_days,
    }
    return goal_stats, defence_stats, stats_meta
