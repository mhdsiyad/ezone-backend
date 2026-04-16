from django.contrib import admin
from .models import User, Team, Auction, AuctionTeam, Player, Bid, SoldResult


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['username', 'role', 'is_active', 'date_joined']
    list_filter = ['role', 'is_active']
    search_fields = ['username', 'email']


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_by', 'created_at']
    search_fields = ['name']


class AuctionTeamInline(admin.TabularInline):
    model = AuctionTeam
    extra = 0


class PlayerInline(admin.TabularInline):
    model = Player
    extra = 0
    fields = ['name', 'level', 'base_price', 'sold', 'skipped', 'order']


@admin.register(Auction)
class AuctionAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'status', 'manager', 'created_at']
    list_filter = ['status']
    search_fields = ['title', 'id']
    inlines = [AuctionTeamInline, PlayerInline]
    readonly_fields = ['created_at', 'started_at', 'ended_at']


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ['name', 'level', 'base_price', 'auction', 'sold', 'skipped', 'order']
    list_filter = ['level', 'sold', 'skipped']
    search_fields = ['name']


@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display = ['player', 'team', 'amount', 'auction', 'timestamp']
    list_filter = ['auction']
    ordering = ['-timestamp']


@admin.register(SoldResult)
class SoldResultAdmin(admin.ModelAdmin):
    list_display = ['player', 'team', 'sold_price', 'auction', 'sold_at']
    list_filter = ['auction']
