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
