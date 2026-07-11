from rest_framework import serializers
from .models import Team, Season

class SeasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Season
        fields = ['id', 'name', 'is_active']

class TeamSerializer(serializers.ModelSerializer):
    season_name = serializers.CharField(source='season.name', read_only=True)

    class Meta:
        model = Team
        fields = ['id', 'team_name', 'leader_name', 'season', 'season_name', 'leader_contact_number', 'instagram_id', 'is_verified', 'created_at']
        read_only_fields = ['is_verified', 'created_at']

class TeamOnboardingSerializer(serializers.ModelSerializer):
    """Full team record for the manager onboarding list/detail views."""
    season_name = serializers.CharField(source='season.name', read_only=True)

    class Meta:
        model = Team
        fields = [
            'id', 'team_name', 'leader_name', 'leader_contact_number', 'instagram_id',
            'is_verified', 'season', 'season_name', 'payment_status', 'contacted', 'note',
            'created_at',
        ]
        read_only_fields = ['team_name', 'leader_name', 'leader_contact_number', 'instagram_id', 'is_verified', 'created_at']

class TeamOnboardingUpdateSerializer(serializers.ModelSerializer):
    """Restricts manager edits to the onboarding-tracking fields."""

    class Meta:
        model = Team
        fields = ['season', 'payment_status', 'contacted', 'note']
