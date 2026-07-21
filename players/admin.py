from django.contrib import admin
from .models import PlayerProfile, PlayerBadge
from .services import verify_player, unverify_player


@admin.register(PlayerProfile)
class PlayerProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'player_id', 'efootball_id', 'rating', 'is_verified', 'contacted', 'applied_at')
    list_filter = ('is_verified', 'contacted', 'applied_at')
    search_fields = ('name', 'player_id', 'efootball_id', 'phone_number')
    actions = ['verify_players', 'unverify_players']

    def verify_players(self, request, queryset):
        for player in queryset:
            verify_player(player)
    verify_players.short_description = "Verify selected players (assigns EZ#### id)"

    def unverify_players(self, request, queryset):
        for player in queryset:
            unverify_player(player)
    unverify_players.short_description = "Unverify selected players"


@admin.register(PlayerBadge)
class PlayerBadgeAdmin(admin.ModelAdmin):
    list_display = ('profile', 'badge_type', 'competition', 'awarded_at')
    list_filter = ('badge_type', 'awarded_at')
    search_fields = ('profile__name', 'profile__player_id')
