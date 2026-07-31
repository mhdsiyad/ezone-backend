import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models


def generate_auction_id():
    return 'EZN-' + str(uuid.uuid4())[:6].upper()


class User(AbstractUser):
    ROLE_CHOICES = [('manager', 'Manager'), ('captain', 'Captain')]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='captain')

    def __str__(self):
        return f"{self.username} ({self.role})"


class Team(models.Model):
    name = models.CharField(max_length=100)
    logo = models.ImageField(upload_to='team_logos/', null=True, blank=True)
    primary_color = models.CharField(max_length=7, default='#1F3322')
    captain_username = models.CharField(max_length=150, blank=True, default='')
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='created_teams'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Auction(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('ended', 'Ended'),
    ]

    id = models.CharField(
        max_length=20, primary_key=True,
        default=generate_auction_id
    )
    title = models.CharField(max_length=200)
    manager = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='managed_auctions'
    )
    teams = models.ManyToManyField(Team, through='AuctionTeam', blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    base_balance = models.PositiveIntegerField(default=10000)
    max_players_per_team = models.PositiveIntegerField(default=15)
    time_limit = models.PositiveIntegerField(default=60)
    price_decrement = models.PositiveIntegerField(
        default=5,
        help_text='Amount subtracted from a player\'s base price each time they go unsold/skipped.'
    )
    price_lock_enabled = models.BooleanField(
        default=False,
        help_text='When on, a team cannot bid more than what leaves enough balance to still afford its remaining required roster slots.'
    )
    custom_bid_disabled = models.BooleanField(
        default=False,
        help_text='When on, captains can only use the quick-bid buttons — the free-type bid amount input is hidden.'
    )
    is_fixture_only = models.BooleanField(
        default=False,
        help_text='Auto-created container for teams entered directly at fixture-creation time (no live auction ever runs). Hidden from the manager\'s auctions dashboard.'
    )
    current_player = models.ForeignKey(
        'Player', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='current_in_auction'
    )
    current_timer = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title


class AuctionTeam(models.Model):
    """Through model carrying per-auction team state (balance, etc.)"""
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    balance = models.PositiveIntegerField()

    class Meta:
        unique_together = ('auction', 'team')

    def __str__(self):
        return f"{self.team.name} in {self.auction.title}"


class Player(models.Model):
    LEVEL_CHOICES = [
        ('bigtime', 'Bigtime'),
        ('epic', 'Epic'),
        ('showtime', 'Showtime'),
        ('highlight', 'Highlight'),
        ('base', 'Base'),
    ]

    auction = models.ForeignKey(
        Auction, on_delete=models.CASCADE, related_name='players'
    )
    name = models.CharField(max_length=100)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='base')
    base_price = models.PositiveIntegerField(default=50)
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    goals = models.PositiveIntegerField(default=0)
    sold = models.BooleanField(default=False)
    skipped = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.name} ({self.level})"


class Bid(models.Model):
    auction = models.ForeignKey(
        Auction, on_delete=models.CASCADE, related_name='bids'
    )
    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name='bids'
    )
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='bids')
    amount = models.PositiveIntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.team.name} bid ${self.amount} on {self.player.name}"


class SoldResult(models.Model):
    auction = models.ForeignKey(
        Auction, on_delete=models.CASCADE, related_name='results'
    )
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='won_players')
    sold_price = models.PositiveIntegerField()
    sold_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.player.name} → {self.team.name} @ ${self.sold_price}"


class FixtureSeason(models.Model):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class FixtureCompetition(models.Model):
    MATCH_TYPE_CHOICES = [
        ('single', 'Single Player Matches'),
        ('team', 'Team Tournament'),
    ]

    FORMAT_TYPE_CHOICES = [
        ('ezone_custom', 'League'),
        ('top4_semi',    'Top 4 Semi Finals'),
        ('top2_final',   'Top 2 Direct Final'),
        ('knockout',     'Knockout Bracket'),
        ('group_stage',  'Group Stage'),
    ]

    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('ongoing', 'Ongoing'),
        ('completed', 'Completed'),
    ]

    auction = models.ForeignKey(
        Auction, on_delete=models.CASCADE, related_name='fixture_competitions'
    )
    season = models.ForeignKey(
        FixtureSeason, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='competitions'
    )
    title = models.CharField(max_length=200)
    match_type = models.CharField(max_length=20, choices=MATCH_TYPE_CHOICES)
    format_type = models.CharField(max_length=100, blank=True, default='League')
    teams = models.ManyToManyField(Team, related_name='fixture_competitions', blank=True)
    matches_per_pair = models.PositiveIntegerField(default=1)
    match_days = models.PositiveIntegerField(default=1)
    semifinal_qualifiers = models.PositiveIntegerField(default=4)
    teams_per_group_advance = models.PositiveIntegerField(default=2)
    is_default = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='upcoming'
    )
    completion_percentage = models.PositiveIntegerField(default=0)
    winner = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='won_fixture_competitions'
    )
    runner_up = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='runner_up_fixture_competitions'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class FixtureGroup(models.Model):
    competition = models.ForeignKey(
        FixtureCompetition, on_delete=models.CASCADE, related_name='groups'
    )
    name = models.CharField(max_length=50)
    order = models.PositiveIntegerField(default=0)
    teams = models.ManyToManyField(Team, related_name='fixture_groups', blank=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.name} ({self.competition.title})"


class CustomTournament(models.Model):
    title = models.CharField(max_length=200)
    format_type = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=FixtureCompetition.STATUS_CHOICES, default='upcoming'
    )
    completion_percentage = models.PositiveIntegerField(default=0)
    winner_name = models.CharField(max_length=200, blank=True, null=True)
    runner_up_name = models.CharField(max_length=200, blank=True, null=True)
    manager = models.ForeignKey(User, on_delete=models.CASCADE, related_name='custom_tournaments')
    created_at = models.DateTimeField(auto_now_add=True)

    winner_banner_image = models.ImageField(upload_to='custom_tournaments/banners/', null=True, blank=True)
    runner_up_logo = models.ImageField(upload_to='custom_tournaments/logos/', null=True, blank=True)
    winner_description_1 = models.TextField(blank=True, null=True)
    winner_description_2 = models.TextField(blank=True, null=True)
    winner_quote = models.TextField(blank=True, null=True)
    champions_squad = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def __str__(self):
        return f"{self.title} ({self.auction_id})"



class FixtureRosterEntry(models.Model):
    competition = models.ForeignKey(
        FixtureCompetition, on_delete=models.CASCADE, related_name='roster_entries'
    )
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='fixture_roster_entries')
    player = models.ForeignKey(
        Player, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='fixture_roster_entries'
    )
    profile = models.ForeignKey(
        'players.PlayerProfile', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='roster_entries'
    )
    name = models.CharField(max_length=100)
    is_custom = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['team__name', 'name']
        unique_together = ('competition', 'team', 'name')

    def __str__(self):
        return f"{self.name} - {self.team.name}"


class FixtureMatch(models.Model):
    STAGE_CHOICES = [
        ('league', 'League'),
        ('round_of_32', 'Round of 32'),
        ('round_of_16', 'Round of 16'),
        ('quarter', 'Quarter Final'),
        ('semi', 'Semi Final'),
        ('final', 'Final'),
    ]
    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('completed', 'Completed'),
    ]

    competition = models.ForeignKey(
        FixtureCompetition, on_delete=models.CASCADE, related_name='matches'
    )
    group = models.ForeignKey(
        FixtureGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name='matches'
    )
    home_team = models.ForeignKey(
        Team, on_delete=models.CASCADE, related_name='home_fixture_matches'
    )
    away_team = models.ForeignKey(
        Team, on_delete=models.CASCADE, related_name='away_fixture_matches'
    )
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='league')
    match_day = models.PositiveIntegerField(default=1)
    order = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming')
    home_score = models.PositiveIntegerField(default=0)
    away_score = models.PositiveIntegerField(default=0)
    played_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['match_day', 'order', 'id']

    def __str__(self):
        return f"{self.home_team.name} vs {self.away_team.name}"


class FixtureLineup(models.Model):
    match = models.ForeignKey(
        FixtureMatch, on_delete=models.CASCADE, related_name='lineups'
    )
    home_player = models.ForeignKey(
        Player, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='home_fixture_lineups'
    )
    away_player = models.ForeignKey(
        Player, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='away_fixture_lineups'
    )
    home_roster_entry = models.ForeignKey(
        FixtureRosterEntry, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='home_fixture_lineups'
    )
    away_roster_entry = models.ForeignKey(
        FixtureRosterEntry, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='away_fixture_lineups'
    )
    home_goals = models.PositiveIntegerField(default=0)
    away_goals = models.PositiveIntegerField(default=0)
    penalty_shootout = models.BooleanField(
        default=False,
        help_text='This player set finished level and was decided by a penalty shootout.'
    )
    home_penalty = models.PositiveIntegerField(default=0)
    away_penalty = models.PositiveIntegerField(default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    @property
    def winner_side(self):
        """'home', 'away', or None — the shootout settles a level set."""
        if self.home_goals != self.away_goals:
            return 'home' if self.home_goals > self.away_goals else 'away'
        if self.penalty_shootout and self.home_penalty != self.away_penalty:
            return 'home' if self.home_penalty > self.away_penalty else 'away'
        return None

    def __str__(self):
        home = self.home_player.name if self.home_player else 'TBD'
        away = self.away_player.name if self.away_player else 'TBD'
        return f"{home} vs {away}"
