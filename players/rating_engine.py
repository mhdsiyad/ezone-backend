from django.db.models import Q, Sum

from .models import BASE_RATING, PlayerBadge, PlayerProfile

# XP economy — how each stat feeds into a player's rating. The rating IS the
# level; there is no separate "level" concept. Tunable here without a migration.
XP_PER_GOAL = 50
XP_PER_WIN = 100
XP_PER_BADGE = 250  # Golden Ball / Golden Glove

# Tournament final-standing bonus, ranked 1st (top of table) downward.
# Positions past the list taper to a flat floor.
TABLE_POSITION_XP = [250, 200, 180, 160, 150, 140, 130, 120, 110, 100, 90, 80, 70, 60]
TABLE_POSITION_XP_FLOOR = 50


def get_table_position_xp(position):
    """`position` is 1-indexed (1 = top of table)."""
    idx = position - 1
    if idx < len(TABLE_POSITION_XP):
        return TABLE_POSITION_XP[idx]
    return TABLE_POSITION_XP_FLOOR


def get_total_goals(profile):
    from auction.models import FixtureLineup

    home_sum = FixtureLineup.objects.filter(
        match__status='completed',
        home_roster_entry__profile=profile,
    ).aggregate(total=Sum('home_goals'))['total'] or 0
    away_sum = FixtureLineup.objects.filter(
        match__status='completed',
        away_roster_entry__profile=profile,
    ).aggregate(total=Sum('away_goals'))['total'] or 0
    return home_sum + away_sum


def get_badge_count(profile):
    return PlayerBadge.objects.filter(profile=profile).count()


def get_current_team(profile):
    from auction.models import FixtureRosterEntry

    entry = FixtureRosterEntry.objects.filter(profile=profile).select_related('team').order_by('-created_at').first()
    return entry.team.name if entry else None


def get_match_stats(profile):
    from auction.models import FixtureLineup

    lineups = FixtureLineup.objects.filter(
        match__status='completed',
    ).filter(
        Q(home_roster_entry__profile=profile) | Q(away_roster_entry__profile=profile)
    ).select_related('match', 'home_roster_entry', 'away_roster_entry')

    matches_seen = {}
    for lineup in lineups:
        match = lineup.match
        if match.id in matches_seen:
            continue
        is_home = lineup.home_roster_entry_id is not None and lineup.home_roster_entry.profile_id == profile.id
        won = (
            (is_home and match.home_score > match.away_score)
            or (not is_home and match.away_score > match.home_score)
        )
        matches_seen[match.id] = won

    matches_played = len(matches_seen)
    wins = sum(1 for won in matches_seen.values() if won)
    win_rate = round((wins / matches_played) * 100) if matches_played else 0
    return {'matches_played': matches_played, 'wins': wins, 'win_rate': win_rate}


def _roster_entry_stats(entry):
    """Goals scored and matches won by a single roster entry (one tournament stint)."""
    from auction.models import FixtureLineup

    lineups = FixtureLineup.objects.filter(
        match__status='completed',
    ).filter(
        Q(home_roster_entry=entry) | Q(away_roster_entry=entry)
    ).select_related('match')

    goals = 0
    matches_seen = {}
    for lineup in lineups:
        match = lineup.match
        is_home = lineup.home_roster_entry_id == entry.id
        goals += lineup.home_goals if is_home else lineup.away_goals
        if match.id in matches_seen:
            continue
        won = (is_home and match.home_score > match.away_score) or (not is_home and match.away_score > match.home_score)
        matches_seen[match.id] = won

    wins = sum(1 for won in matches_seen.values() if won)
    return goals, wins


def _team_table_position(competition, team_id):
    """1-indexed standing of `team_id` in `competition`'s table (group-aware). None if not found."""
    from auction.stats import _fixture_table, _fixture_group_tables

    if competition.groups.exists():
        for group in _fixture_group_tables(competition):
            for idx, row in enumerate(group['table']):
                if row['team_id'] == team_id:
                    return idx + 1
        return None

    for idx, row in enumerate(_fixture_table(competition)):
        if row['team_id'] == team_id:
            return idx + 1
    return None


def get_tournament_xp_breakdown(profile):
    """Per-tournament XP breakdown for every completed competition the profile played in."""
    from auction.models import FixtureRosterEntry

    entries = FixtureRosterEntry.objects.filter(
        profile=profile, competition__status='completed'
    ).select_related('competition', 'team')

    badge_xp_by_competition = {}
    for badge in PlayerBadge.objects.filter(profile=profile):
        badge_xp_by_competition[badge.competition_id] = badge_xp_by_competition.get(badge.competition_id, 0) + XP_PER_BADGE

    breakdown = []
    seen_competitions = set()
    for entry in entries:
        if entry.competition_id in seen_competitions:
            continue
        seen_competitions.add(entry.competition_id)

        goals, wins = _roster_entry_stats(entry)
        position = _team_table_position(entry.competition, entry.team_id)
        placement_xp = get_table_position_xp(position) if position else 0
        badge_xp = badge_xp_by_competition.get(entry.competition_id, 0)

        goal_xp = goals * XP_PER_GOAL
        win_xp = wins * XP_PER_WIN
        total_xp = goal_xp + win_xp + placement_xp + badge_xp

        breakdown.append({
            'competition_id': entry.competition_id,
            'title': entry.competition.title,
            'team_name': entry.team.name,
            'goals': goals,
            'goal_xp': goal_xp,
            'wins': wins,
            'win_xp': win_xp,
            'table_position': position,
            'placement_xp': placement_xp,
            'badge_xp': badge_xp,
            'total_xp': total_xp,
        })
    return breakdown


def get_career_points(profile):
    return sum(entry['total_xp'] for entry in get_tournament_xp_breakdown(profile))


def xp_to_next_level(level):
    """XP required to advance from `level` to `level + 1`.

    Levels climb slowly and get progressively more expensive: 62->63 costs
    100 XP, growing along a quadratic curve to ~1200 XP by level 105->106.
    Rounded to the nearest 10 to keep thresholds tidy.
    """
    n = level - BASE_RATING
    if n < 0:
        return 0
    raw = 100 + 9.6296 * n + 0.371013 * n * n
    return int(round(raw / 10)) * 10


def _level_and_progress(points):
    level = BASE_RATING
    remaining = points
    while remaining >= xp_to_next_level(level):
        remaining -= xp_to_next_level(level)
        level += 1
    return level, remaining


def rating_from_points(points):
    level, _ = _level_and_progress(points)
    return level


def get_rating_progress_from_points(points):
    level, xp_into_level = _level_and_progress(points)
    return {
        'xp_into_level': xp_into_level,
        'xp_for_next_level': xp_to_next_level(level),
    }


def get_rating_progress(profile):
    """XP progress toward the next rating point, for a small in-UI progress bar."""
    return get_rating_progress_from_points(get_career_points(profile))


def recompute_rating(profile):
    rating = rating_from_points(get_career_points(profile))
    profile.rating = rating
    profile.save(update_fields=['rating'])
    return rating


def _award_badge(competition, badge_type, stat_rows):
    from auction.models import FixtureRosterEntry

    winner_profile = None
    for row in stat_rows:
        roster_entry_id = row.get('roster_entry_id')
        if not roster_entry_id:
            continue
        entry = FixtureRosterEntry.objects.filter(id=roster_entry_id).select_related('profile').first()
        if entry and entry.profile_id:
            winner_profile = entry.profile
            break

    if winner_profile:
        PlayerBadge.objects.update_or_create(
            competition=competition,
            badge_type=badge_type,
            defaults={'profile': winner_profile},
        )
    else:
        PlayerBadge.objects.filter(competition=competition, badge_type=badge_type).delete()


def handle_competition_completed(competition):
    """Awards Golden Ball / Golden Glove badges and recomputes rating for every linked
    participant. Safe to call repeatedly (idempotent) — no-ops unless the competition
    is actually completed."""
    if competition.status != 'completed':
        return

    from auction.models import FixtureRosterEntry
    from auction.stats import _fixture_player_stats

    goal_stats, defence_stats, _meta = _fixture_player_stats(competition)

    _award_badge(competition, 'golden_ball', goal_stats)
    _award_badge(competition, 'golden_glove', defence_stats)

    linked_profile_ids = FixtureRosterEntry.objects.filter(
        competition=competition, profile__isnull=False
    ).values_list('profile_id', flat=True).distinct()

    for profile in PlayerProfile.objects.filter(id__in=list(linked_profile_ids)):
        recompute_rating(profile)
