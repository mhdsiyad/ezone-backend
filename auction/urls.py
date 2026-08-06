from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    # Auth
    path('auth/login/', views.LoginView.as_view(), name='login'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Teams (manager only)
    path('teams/', views.TeamListCreateView.as_view(), name='team-list-create'),
    path('teams/registry/', views.TeamRegistryView.as_view(), name='team-registry'),
    path('teams/<int:pk>/', views.TeamDetailView.as_view(), name='team-detail'),

    # Auctions
    path('auctions/', views.AuctionListCreateView.as_view(), name='auction-list-create'),
    path('auctions/<str:auction_id>/', views.AuctionDetailView.as_view(), name='auction-detail'),

    # Players
    path('auctions/<str:auction_id>/players/', views.PlayerListView.as_view(), name='player-list'),
    path('auctions/<str:auction_id>/players/import/', views.PlayerImportView.as_view(), name='player-import'),
    path('auctions/<str:auction_id>/players/<int:player_id>/', views.PlayerPriceUpdateView.as_view(), name='player-price-update'),

    # Auction Control
    path('auctions/<str:auction_id>/control/', views.AuctionControlView.as_view(), name='auction-control'),

    # Bids
    path('auctions/<str:auction_id>/bids/', views.BidListCreateView.as_view(), name='bid-list-create'),

    # Results
    path('auctions/<str:auction_id>/results/', views.ResultListView.as_view(), name='results'),

    # Public fixtures for official website
    path('public/fixtures/', views.PublicFixtureCompetitionListView.as_view(), name='public-fixture-list'),
    path('public/custom-tournaments/<int:pk>/', views.PublicCustomTournamentDetailView.as_view(), name='public-custom-tournament-detail'),
    
    path('manager/custom-tournaments/', views.CustomTournamentListCreateView.as_view(), name='custom-tournament-list-create'),
    path('manager/custom-tournaments/<int:pk>/', views.CustomTournamentDetailView.as_view(), name='custom-tournament-detail'),

    path('public/auctions/', views.PublicAuctionListView.as_view(), name='public-auction-list'),
    path('public/fixtures/latest/', views.PublicLatestFixtureCompetitionView.as_view(), name='public-fixture-latest'),
    path('public/fixtures/<int:fixture_id>/', views.PublicFixtureCompetitionDetailView.as_view(), name='public-fixture-detail'),

    # Fixtures
    path('fixture-seasons/', views.FixtureSeasonListCreateView.as_view(), name='fixture-season-list-create'),
    path('fixtures/', views.AllFixtureCompetitionsView.as_view(), name='all-fixtures-list'),
    path('fixtures/quick-create/', views.FixtureQuickCreateView.as_view(), name='fixture-quick-create'),
    path('auctions/<str:auction_id>/fixtures/', views.FixtureCompetitionListCreateView.as_view(), name='fixture-list-create'),
    path('auctions/<str:auction_id>/fixtures/<int:fixture_id>/', views.FixtureCompetitionDetailView.as_view(), name='fixture-detail'),
    path('auctions/<str:auction_id>/fixtures/<int:fixture_id>/status/', views.FixtureCompetitionStatusUpdateView.as_view(), name='fixture-status-update'),
    path('auctions/<str:auction_id>/fixtures/<int:fixture_id>/roster/', views.FixtureRosterEntryListCreateView.as_view(), name='fixture-roster-list-create'),
    path('auctions/<str:auction_id>/fixtures/<int:fixture_id>/roster/<int:entry_id>/', views.FixtureRosterEntryDetailView.as_view(), name='fixture-roster-detail'),
    path('auctions/<str:auction_id>/fixtures/<int:fixture_id>/matches/<int:match_id>/', views.FixtureMatchUpdateView.as_view(), name='fixture-match-update'),
    path('auctions/<str:auction_id>/fixtures/<int:fixture_id>/knockout/', views.FixtureKnockoutCreateView.as_view(), name='fixture-knockout-create'),
    path('auctions/<str:auction_id>/fixtures/<int:fixture_id>/knockout/seed/', views.FixtureKnockoutSeedProposalView.as_view(), name='fixture-knockout-seed'),
    path('auctions/<str:auction_id>/fixtures/<int:fixture_id>/stage/<str:stage>/', views.FixtureStageDeleteView.as_view(), name='fixture-stage-delete'),
    path('auctions/<str:auction_id>/fixtures/<int:fixture_id>/groups/<int:group_id>/reassign/', views.FixtureGroupReassignView.as_view(), name='fixture-group-reassign'),
]
