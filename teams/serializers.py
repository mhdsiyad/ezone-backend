from rest_framework import serializers
from .models import Team

class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ['id', 'team_name', 'leader_name', 'leader_contact_number', 'instagram_id', 'is_verified', 'created_at']
        read_only_fields = ['is_verified', 'created_at']
