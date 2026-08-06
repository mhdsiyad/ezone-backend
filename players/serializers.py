from rest_framework import serializers
from .models import PlayerProfile


class PlayerApplySerializer(serializers.ModelSerializer):
    """Public application form — applicants can only ever set their own submitted fields."""

    class Meta:
        model = PlayerProfile
        fields = [
            'id', 'name', 'phone_number', 'efootball_id', 'instagram_id', 'position',
            'country', 'photo', 'is_verified', 'applied_at',
        ]
        read_only_fields = ['is_verified', 'applied_at']


class PublicPlayerListSerializer(serializers.ModelSerializer):
    rating = serializers.SerializerMethodField()

    class Meta:
        model = PlayerProfile
        fields = ['player_id', 'name', 'efootball_id', 'instagram_id', 'position', 'photo', 'rating']

    def get_rating(self, obj):
        # Computed live, same as the detail page — the cached DB column can lag
        # behind an ongoing tournament's results until the next recompute trigger.
        from .rating_engine import get_career_points, rating_from_points
        return rating_from_points(get_career_points(obj))


class PublicPlayerDetailSerializer(serializers.ModelSerializer):
    rating = serializers.SerializerMethodField()
    total_goals = serializers.SerializerMethodField()
    match_history = serializers.SerializerMethodField()
    recent_matches = serializers.SerializerMethodField()
    badges = serializers.SerializerMethodField()
    current_team = serializers.SerializerMethodField()
    matches_played = serializers.SerializerMethodField()
    wins = serializers.SerializerMethodField()
    win_rate = serializers.SerializerMethodField()
    rating_xp_into_level = serializers.SerializerMethodField()
    rating_xp_for_next_level = serializers.SerializerMethodField()
    career_xp = serializers.SerializerMethodField()

    class Meta:
        model = PlayerProfile
        fields = [
            'player_id', 'name', 'efootball_id', 'instagram_id', 'position', 'country', 'photo', 'rating',
            'total_goals', 'match_history', 'recent_matches', 'badges', 'current_team', 'matches_played', 'wins',
            'win_rate', 'rating_xp_into_level', 'rating_xp_for_next_level', 'career_xp',
        ]

    def _xp_breakdown(self, obj):
        if not hasattr(obj, '_xp_breakdown_cache'):
            from .rating_engine import get_tournament_xp_breakdown
            obj._xp_breakdown_cache = get_tournament_xp_breakdown(obj)
        return obj._xp_breakdown_cache

    def get_career_xp(self, obj):
        return sum(entry['total_xp'] for entry in self._xp_breakdown(obj))

    def get_rating(self, obj):
        # Computed live from current stats rather than the cached DB column, so an
        # in-progress tournament's results show up immediately without waiting for
        # a background recompute trigger to fire.
        from .rating_engine import rating_from_points
        return rating_from_points(self.get_career_xp(obj))

    def _rating_progress(self, obj):
        if not hasattr(obj, '_rating_progress_cache'):
            from .rating_engine import get_rating_progress_from_points
            obj._rating_progress_cache = get_rating_progress_from_points(self.get_career_xp(obj))
        return obj._rating_progress_cache

    def get_rating_xp_into_level(self, obj):
        return self._rating_progress(obj)['xp_into_level']

    def get_rating_xp_for_next_level(self, obj):
        return self._rating_progress(obj)['xp_for_next_level']

    def get_total_goals(self, obj):
        from .rating_engine import get_total_goals
        return get_total_goals(obj)

    def get_current_team(self, obj):
        from .rating_engine import get_current_team
        return get_current_team(obj)

    def _match_stats(self, obj):
        if not hasattr(obj, '_match_stats_cache'):
            from .rating_engine import get_match_stats
            obj._match_stats_cache = get_match_stats(obj)
        return obj._match_stats_cache

    def get_matches_played(self, obj):
        return self._match_stats(obj)['matches_played']

    def get_wins(self, obj):
        return self._match_stats(obj)['wins']

    def get_win_rate(self, obj):
        return self._match_stats(obj)['win_rate']

    def get_match_history(self, obj):
        return self._xp_breakdown(obj)

    def get_recent_matches(self, obj):
        from .rating_engine import get_recent_matches
        return get_recent_matches(obj, limit=20, offset=0)

    def get_badges(self, obj):
        from .rating_engine import XP_PER_BADGE
        return [
            {
                'badge_type': badge.badge_type,
                'badge_label': badge.get_badge_type_display(),
                'competition_title': badge.competition.title,
                'awarded_at': badge.awarded_at,
                'xp': XP_PER_BADGE,
            }
            for badge in obj.badges.select_related('competition').all()
        ]


class ManagerPlayerListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlayerProfile
        fields = [
            'id', 'player_id', 'name', 'phone_number', 'efootball_id', 'instagram_id', 'position', 'country', 'photo',
            'rating', 'is_verified', 'contacted', 'note', 'applied_at', 'verified_at', 'is_rejected', 'rejected_at',
        ]


class ManagerPlayerDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlayerProfile
        fields = [
            'id', 'player_id', 'name', 'phone_number', 'efootball_id', 'instagram_id', 'position', 'country', 'photo',
            'rating', 'is_verified', 'contacted', 'note', 'applied_at', 'verified_at', 'is_rejected', 'rejected_at',
        ]
        read_only_fields = [
            'player_id', 'name', 'phone_number', 'efootball_id', 'instagram_id', 'position', 'country', 'photo',
            'rating', 'is_verified', 'applied_at', 'verified_at', 'is_rejected', 'rejected_at',
        ]


class ManagerPlayerUpdateSerializer(serializers.ModelSerializer):
    """Restricts manager edits to onboarding-tracking fields only."""

    class Meta:
        model = PlayerProfile
        fields = ['contacted', 'note']


class PlayerVerifySerializer(serializers.Serializer):
    is_verified = serializers.BooleanField()
