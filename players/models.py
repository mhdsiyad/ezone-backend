import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .validators import validate_photo_size

BASE_RATING = 62


POSITION_CHOICES = [
    ('GK', 'Goalkeeper'),
    ('CB', 'Centre Back'),
    ('RB', 'Right Back'),
    ('LB', 'Left Back'),
    ('DMF', 'Defensive Midfielder'),
    ('CMF', 'Central Midfielder'),
    ('AMF', 'Attacking Midfielder'),
    ('RWF', 'Right Wing Forward'),
    ('LWF', 'Left Wing Forward'),
    ('SS', 'Second Striker'),
    ('CF', 'Centre Forward'),
]


class PlayerProfile(models.Model):
    player_id = models.CharField(max_length=10, unique=True, blank=True, null=True, db_index=True)
    name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=20)
    efootball_id = models.CharField(max_length=100, unique=True)
    instagram_id = models.CharField(max_length=100, blank=True, null=True)
    position = models.CharField(max_length=10, choices=POSITION_CHOICES, blank=True, null=True)
    country = models.CharField(max_length=100, default='India', blank=True)
    photo = models.ImageField(upload_to='player_photos/', validators=[validate_photo_size], blank=True, null=True)
    rating = models.PositiveIntegerField(default=BASE_RATING)
    is_verified = models.BooleanField(default=False)
    contacted = models.BooleanField(default=False)
    note = models.TextField(blank=True, default='')
    applied_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-applied_at']

    def __str__(self):
        return f"{self.player_id or 'unverified'} - {self.name}"


class PlayerIDCounter(models.Model):
    """Singleton row used to generate sequential EZ#### player IDs at verification time."""
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"PlayerIDCounter(last_number={self.last_number})"


class PlayerBadge(models.Model):
    BADGE_CHOICES = [
        ('golden_ball', 'Golden Ball'),
        ('golden_glove', 'Golden Glove'),
    ]

    profile = models.ForeignKey(PlayerProfile, on_delete=models.CASCADE, related_name='badges')
    badge_type = models.CharField(max_length=20, choices=BADGE_CHOICES)
    competition = models.ForeignKey('auction.FixtureCompetition', on_delete=models.CASCADE, related_name='player_badges')
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('competition', 'badge_type')
        ordering = ['-awarded_at']

    def __str__(self):
        return f"{self.get_badge_type_display()} - {self.profile.name} ({self.competition_id})"


@receiver(post_delete, sender=PlayerProfile)
def delete_player_photo(sender, instance, **kwargs):
    """Delete the photo file when a player profile is deleted.

    Uses default_storage.delete() so it works for both local filesystem
    (development) and remote backends like R2/S3 (production). The old
    os.remove(instance.photo.path) would raise NotImplementedError when
    the storage backend is S3-based, because path is not supported remotely.
    """
    if not instance.photo:
        return
    name = instance.photo.name
    if not name:
        return
    try:
        if default_storage.exists(name):
            default_storage.delete(name)
    except Exception:  # noqa: BLE001 - best-effort cleanup, never crash a delete
        pass
