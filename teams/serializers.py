from rest_framework import serializers
from .models import Team, Season

class TeamSerializer(serializers.ModelSerializer):
    season_name = serializers.CharField(source='season.name', read_only=True)

    class Meta:
        model = Team
        fields = ['id', 'team_name', 'leader_name', 'season', 'season_name', 'leader_contact_number', 'instagram_id', 'is_verified', 'created_at']
        read_only_fields = ['is_verified', 'created_at']
