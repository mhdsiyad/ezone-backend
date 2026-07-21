from django.core.management.base import BaseCommand

from auction.models import FixtureCompetition
from players.rating_engine import handle_competition_completed, recompute_competition_profiles


class Command(BaseCommand):
    help = (
        "Awards badges and recomputes ratings for every completed tournament, and "
        "refreshes ratings for ongoing tournaments too. Safe to re-run."
    )

    def handle(self, *args, **options):
        completed = FixtureCompetition.objects.filter(status='completed')
        for competition in completed:
            handle_competition_completed(competition)

        ongoing = FixtureCompetition.objects.filter(status='ongoing')
        for competition in ongoing:
            recompute_competition_profiles(competition)

        self.stdout.write(self.style.SUCCESS(
            f"Recomputed ratings/badges for {completed.count()} completed and "
            f"{ongoing.count()} ongoing competitions."
        ))
