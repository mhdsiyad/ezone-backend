from django.contrib import admin
from django.utils import timezone

from .models import (
    User,
    Team,
    Auction,
    AuctionTeam,
    Player,
    Bid,
    SoldResult,
    FixtureSeason,
    FixtureCompetition,
    FixtureRosterEntry,
    FixtureMatch,
    FixtureLineup,
    CustomTournament,
)


class SoftDeleteAdmin(admin.ModelAdmin):
    """Admin for soft-deletable models.

    Uses `all_objects` so deleted records remain visible here — this page is the
    only place they can be seen or brought back.
    """

    actions = ['restore_selected', 'soft_delete_selected']

    def get_queryset(self, request):
        return self.model.all_objects.get_queryset()

    @admin.display(boolean=True, description='Deleted', ordering='is_deleted')
    def deleted_flag(self, obj):
        return obj.is_deleted

    @admin.action(description='Restore selected (undo delete)')
    def restore_selected(self, request, queryset):
        n = queryset.update(is_deleted=False, deleted_at=None)
        self.message_user(request, f'{n} record(s) restored.')

    @admin.action(description='Soft delete selected (hide from app)')
    def soft_delete_selected(self, request, queryset):
        n = queryset.update(is_deleted=True, deleted_at=timezone.now())
        self.message_user(request, f'{n} record(s) hidden from the app.')


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['username', 'role', 'is_active', 'date_joined']
    list_filter = ['role', 'is_active']
    search_fields = ['username', 'email']


@admin.register(Team)
class TeamAdmin(SoftDeleteAdmin):
    list_display = ['name', 'created_by', 'created_at', 'deleted_flag']
    list_filter = ['is_deleted']
    search_fields = ['name', 'captain_username']


class AuctionTeamInline(admin.TabularInline):
    model = AuctionTeam
    extra = 0


class PlayerInline(admin.TabularInline):
    model = Player
    extra = 0
    fields = ['name', 'level', 'base_price', 'sold', 'skipped', 'order']


@admin.register(Auction)
class AuctionAdmin(SoftDeleteAdmin):
    list_display = ['id', 'title', 'status', 'manager', 'is_public', 'price_lock_enabled', 'custom_bid_disabled', 'created_at', 'deleted_flag']
    list_filter = ['is_deleted', 'status', 'is_public', 'price_lock_enabled', 'custom_bid_disabled']
    search_fields = ['title', 'id']
    inlines = [AuctionTeamInline, PlayerInline]
    readonly_fields = ['created_at', 'started_at', 'ended_at']


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ['name', 'level', 'base_price', 'auction', 'sold', 'skipped', 'order']
    list_filter = ['level', 'sold', 'skipped']
    search_fields = ['name', 'position', 'auction__title', 'auction__id']


@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display = ['player', 'team', 'amount', 'auction', 'timestamp']
    list_filter = ['auction']
    search_fields = ['player__name', 'team__name', 'auction__title', 'auction__id']
    ordering = ['-timestamp']


@admin.register(SoldResult)
class SoldResultAdmin(admin.ModelAdmin):
    list_display = ['player', 'team', 'sold_price', 'auction', 'sold_at']
    list_filter = ['auction']
    search_fields = ['player__name', 'team__name', 'auction__title', 'auction__id']


class FixtureMatchInline(admin.TabularInline):
    model = FixtureMatch
    extra = 0

    def get_queryset(self, request):
        return FixtureMatch.all_objects.get_queryset()


@admin.register(FixtureCompetition)
class FixtureCompetitionAdmin(SoftDeleteAdmin):
    list_display = ['title', 'season', 'auction', 'match_type', 'matches_per_pair', 'match_days', 'deleted_flag']
    list_filter = ['is_deleted', 'match_type', 'auction']
    search_fields = ['title', 'auction__title', 'auction__id']
    filter_horizontal = ['teams']
    inlines = [FixtureMatchInline]


@admin.register(FixtureSeason)
class FixtureSeasonAdmin(SoftDeleteAdmin):
    list_display = ['name', 'is_active', 'created_at', 'deleted_flag']
    list_filter = ['is_deleted', 'is_active']
    search_fields = ['name']


@admin.register(FixtureRosterEntry)
class FixtureRosterEntryAdmin(SoftDeleteAdmin):
    list_display = ['name', 'team', 'competition', 'is_custom', 'is_active', 'deleted_flag']
    list_filter = ['is_deleted', 'competition', 'team', 'is_custom', 'is_active']
    search_fields = ['name', 'team__name', 'competition__title']


class FixtureLineupInline(admin.TabularInline):
    model = FixtureLineup
    extra = 0


@admin.register(FixtureMatch)
class FixtureMatchAdmin(SoftDeleteAdmin):
    list_display = ['competition', 'home_team', 'away_team', 'stage', 'match_day', 'status', 'home_score', 'away_score', 'deleted_flag']
    list_filter = ['is_deleted', 'competition', 'stage', 'status']
    search_fields = ['home_team__name', 'away_team__name', 'competition__title']
    inlines = [FixtureLineupInline]


@admin.register(CustomTournament)
class CustomTournamentAdmin(SoftDeleteAdmin):
    list_display = ['title', 'created_at', 'deleted_flag']
    list_filter = ['is_deleted']
    search_fields = ['title']
