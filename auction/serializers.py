from rest_framework import serializers
from .models import User, Team, Auction, AuctionTeam, Player, Bid, SoldResult


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'role']


class TeamSerializer(serializers.ModelSerializer):
    captain_username = serializers.CharField(read_only=True)

    class Meta:
        model = Team
        fields = ['id', 'name', 'captain_username', 'created_at']


class TeamCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)

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
    captain_username = serializers.CharField(source='team.captain_username', read_only=True)
    players_won = serializers.SerializerMethodField()

    class Meta:
        model = AuctionTeam
        fields = ['id', 'name', 'captain_username', 'balance', 'players_won']

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
