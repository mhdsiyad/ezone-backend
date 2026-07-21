from django.core.management.base import BaseCommand

from auction.models import FixtureCompetition
from players.rating_engine import handle_competition_completed


class Command(BaseCommand):
    help = "Awards badges and recomputes ratings for every completed tournament. Safe to re-run."

    def handle(self, *args, **options):
        competitions = FixtureCompetition.objects.filter(status='completed')
        count = competitions.count()
        for competition in competitions:
            handle_competition_completed(competition)
        self.stdout.write(self.style.SUCCESS(f"Recomputed ratings/badges for {count} completed competitions."))
