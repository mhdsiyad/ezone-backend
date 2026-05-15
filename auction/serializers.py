from rest_framework import serializers
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
)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'role']


class TeamSerializer(serializers.ModelSerializer):
    captain_username = serializers.CharField(read_only=True)

    class Meta:
        model = Team
        fields = ['id', 'name', 'logo', 'primary_color', 'captain_username', 'created_at']


class TeamCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)
    logo = serializers.ImageField(required=False, allow_null=True)
    primary_color = serializers.CharField(max_length=7, required=False)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('Username already taken.')
        return value


class PlayerSerializer(serializers.ModelSerializer):
    stats = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = ['id', 'name', 'level', 'base_price', 'stats', 'sold', 'skipped', 'order']

    def get_stats(self, obj):
        return {'win': obj.wins, 'lose': obj.losses, 'goals': obj.goals}


class BidSerializer(serializers.ModelSerializer):
    team_id = serializers.IntegerField(source='team.id', read_only=True)
    team_name = serializers.CharField(source='team.name', read_only=True)
    timestamp = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Bid
        fields = ['id', 'team_id', 'team_name', 'amount', 'timestamp']


class AuctionTeamSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source='team.id')
    name = serializers.CharField(source='team.name')
    logo = serializers.ImageField(source='team.logo', read_only=True)
    primary_color = serializers.CharField(source='team.primary_color', read_only=True)
    captain_username = serializers.CharField(source='team.captain_username', read_only=True)
    players_won = serializers.SerializerMethodField()

    class Meta:
        model = AuctionTeam
        fields = ['id', 'name', 'logo', 'primary_color', 'captain_username', 'balance', 'players_won']

    def get_players_won(self, obj):
        results = SoldResult.objects.filter(
            auction=obj.auction, team=obj.team
        ).select_related('player')
        return [
            {
                'id': r.player.id,
                'name': r.player.name,
                'level': r.player.level,
                'sold_price': r.sold_price,
                'sold_at': r.sold_at.isoformat() if r.sold_at else None,
            }
            for r in results
        ]


class SoldResultSerializer(serializers.ModelSerializer):
    player = PlayerSerializer(read_only=True)
    team_id = serializers.IntegerField(source='team.id', read_only=True)
    team_name = serializers.CharField(source='team.name', read_only=True)

    class Meta:
        model = SoldResult
        fields = ['id', 'player', 'team_id', 'team_name', 'sold_price', 'sold_at']


class FixtureTeamPlayerSerializer(serializers.ModelSerializer):
    sold_price = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = ['id', 'name', 'level', 'sold_price']

    def get_sold_price(self, obj):
        result = getattr(obj, '_fixture_sold_result', None)
        return result.sold_price if result else None


class FixtureSeasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = FixtureSeason
        fields = ['id', 'name', 'is_active', 'created_at']


class FixtureRosterEntrySerializer(serializers.ModelSerializer):
    team_name = serializers.CharField(source='team.name', read_only=True)
    player_level = serializers.CharField(source='player.level', read_only=True)

    class Meta:
        model = FixtureRosterEntry
        fields = [
            'id', 'team', 'team_name', 'player', 'player_level',
            'name', 'is_custom', 'is_active', 'created_at'
        ]


class FixtureLineupSerializer(serializers.ModelSerializer):
    home_player_name = serializers.CharField(source='home_player.name', read_only=True)
    away_player_name = serializers.CharField(source='away_player.name', read_only=True)
    home_roster_entry_name = serializers.CharField(source='home_roster_entry.name', read_only=True)
    away_roster_entry_name = serializers.CharField(source='away_roster_entry.name', read_only=True)

    class Meta:
        model = FixtureLineup
        fields = [
            'id', 'home_player', 'home_player_name', 'home_roster_entry',
            'home_roster_entry_name', 'away_player', 'away_player_name',
            'away_roster_entry', 'away_roster_entry_name', 'home_goals',
            'away_goals', 'order'
        ]


class FixtureMatchSerializer(serializers.ModelSerializer):
    home_team_name = serializers.CharField(source='home_team.name', read_only=True)
    away_team_name = serializers.CharField(source='away_team.name', read_only=True)
    home_team_logo = serializers.ImageField(source='home_team.logo', read_only=True)
    away_team_logo = serializers.ImageField(source='away_team.logo', read_only=True)
    lineups = FixtureLineupSerializer(many=True, read_only=True)

    class Meta:
        model = FixtureMatch
        fields = [
            'id', 'home_team', 'home_team_name', 'home_team_logo',
            'away_team', 'away_team_name', 'away_team_logo',
            'stage', 'match_day', 'order', 'status',
            'home_score', 'away_score', 'played_at', 'lineups'
        ]


class FixtureCompetitionListSerializer(serializers.ModelSerializer):
    teams_count = serializers.SerializerMethodField()
    matches_count = serializers.SerializerMethodField()
    season_name = serializers.CharField(source='season.name', read_only=True)
    auction_title = serializers.CharField(source='auction.title', read_only=True)

    class Meta:
        model = FixtureCompetition
        fields = [
            'id', 'title', 'season', 'season_name', 'auction', 'auction_title',
            'match_type', 'matches_per_pair', 'match_days',
            'semifinal_qualifiers', 'teams_count', 'matches_count', 'created_at'
        ]

    def get_teams_count(self, obj):
        return obj.teams.count()

    def get_matches_count(self, obj):
        return obj.matches.count()


class FixtureCompetitionDetailSerializer(serializers.ModelSerializer):
    teams = serializers.SerializerMethodField()
    season_name = serializers.CharField(source='season.name', read_only=True)
    auction_title = serializers.CharField(source='auction.title', read_only=True)
    roster_entries = FixtureRosterEntrySerializer(many=True, read_only=True)
    matches = FixtureMatchSerializer(many=True, read_only=True)
    table = serializers.SerializerMethodField()
    goal_stats = serializers.SerializerMethodField()
    defence_stats = serializers.SerializerMethodField()

    class Meta:
        model = FixtureCompetition
        fields = [
            'id', 'title', 'season', 'season_name', 'auction', 'auction_title',
            'match_type', 'matches_per_pair', 'match_days',
            'semifinal_qualifiers', 'teams', 'roster_entries', 'matches',
            'table', 'goal_stats', 'defence_stats', 'created_at'
        ]

    def get_table(self, obj):
        return self.context.get('table', [])

    def get_teams(self, obj):
        auction_teams = AuctionTeam.objects.filter(
            auction=obj.auction,
            team__in=obj.teams.all()
        ).select_related('team')
        return AuctionTeamSerializer(auction_teams, many=True).data

    def get_goal_stats(self, obj):
        return self.context.get('goal_stats', [])

    def get_defence_stats(self, obj):
        return self.context.get('defence_stats', [])


class FixtureCompetitionCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    season = serializers.IntegerField(required=False, allow_null=True)
    match_type = serializers.ChoiceField(choices=FixtureCompetition.MATCH_TYPE_CHOICES)
    team_ids = serializers.ListField(child=serializers.IntegerField(), min_length=2)
    matches_per_pair = serializers.IntegerField(min_value=1, max_value=10, default=1)
    match_days = serializers.IntegerField(min_value=1, max_value=60, default=1)
    semifinal_qualifiers = serializers.IntegerField(min_value=2, max_value=16, default=4)


class AuctionListSerializer(serializers.ModelSerializer):
    total_teams = serializers.SerializerMethodField()

    class Meta:
        model = Auction
        fields = ['id', 'title', 'status', 'total_teams', 'time_limit',
                  'base_balance', 'max_players_per_team', 'created_at']

    def get_total_teams(self, obj):
        return obj.teams.count()


class AuctionDetailSerializer(serializers.ModelSerializer):
    teams = serializers.SerializerMethodField()
    current_player = PlayerSerializer(read_only=True)
    highest_bid = serializers.SerializerMethodField()
    recent_bids = serializers.SerializerMethodField()
    total_players = serializers.SerializerMethodField()
    sold_players = serializers.SerializerMethodField()

    class Meta:
        model = Auction
        fields = [
            'id', 'title', 'status', 'time_limit', 'base_balance',
            'max_players_per_team', 'current_timer', 'current_player',
            'teams', 'highest_bid', 'recent_bids',
            'total_players', 'sold_players', 'created_at'
        ]

    def get_teams(self, obj):
        auction_teams = AuctionTeam.objects.filter(auction=obj).select_related('team')
        return AuctionTeamSerializer(auction_teams, many=True).data

    def get_highest_bid(self, obj):
        if not obj.current_player:
            return None
        bid = Bid.objects.filter(
            auction=obj, player=obj.current_player
        ).order_by('-amount').first()
        if bid:
            return BidSerializer(bid).data
        return None

    def get_recent_bids(self, obj):
        bids = Bid.objects.filter(auction=obj).select_related('team')[:20]
        return BidSerializer(bids, many=True).data

    def get_total_players(self, obj):
        return obj.players.count()

    def get_sold_players(self, obj):
        return obj.players.filter(sold=True).count()


class AuctionCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    base_balance = serializers.IntegerField(default=10000)
    max_players_per_team = serializers.IntegerField(default=15)
    time_limit = serializers.IntegerField(default=60)
    team_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
