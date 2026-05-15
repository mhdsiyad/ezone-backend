from django.contrib import admin
from .models import Team, Season

@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('team_name', 'leader_name', 'season', 'leader_contact_number', 'is_verified', 'created_at')
    list_filter = ('season', 'is_verified', 'created_at')
    search_fields = ('team_name', 'leader_name', 'leader_contact_number')
    actions = ['verify_teams', 'unverify_teams']

    def verify_teams(self, request, queryset):
        queryset.update(is_verified=True)
    verify_teams.short_description = "Mark selected teams as verified"

    def unverify_teams(self, request, queryset):
        queryset.update(is_verified=False)
    unverify_teams.short_description = "Unmark selected teams as verified"
