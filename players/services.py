from django.db import transaction
from django.utils import timezone

from .models import PlayerIDCounter


def verify_player(player):
    """Marks a PlayerProfile verified, assigning a sequential EZ#### player_id on first verification."""
    with transaction.atomic():
        if not player.player_id:
            counter, _ = PlayerIDCounter.objects.select_for_update().get_or_create(pk=1)
            counter.last_number += 1
            counter.save(update_fields=['last_number'])
            player.player_id = f"EZ{counter.last_number:04d}"
        player.is_verified = True
        player.verified_at = timezone.now()
        player.save(update_fields=['player_id', 'is_verified', 'verified_at'])
    return player


def unverify_player(player):
    player.is_verified = False
    player.verified_at = None
    player.save(update_fields=['is_verified', 'verified_at'])
    return player
