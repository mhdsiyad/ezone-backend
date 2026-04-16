from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    # Auth
    path('auth/login/', views.LoginView.as_view(), name='login'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Teams (manager only)
    path('teams/', views.TeamListCreateView.as_view(), name='team-list-create'),
    path('teams/<int:pk>/', views.TeamDetailView.as_view(), name='team-detail'),

    # Auctions
    path('auctions/', views.AuctionListCreateView.as_view(), name='auction-list-create'),
    path('auctions/<str:auction_id>/', views.AuctionDetailView.as_view(), name='auction-detail'),

    # Players
    path('auctions/<str:auction_id>/players/', views.PlayerListView.as_view(), name='player-list'),
    path('auctions/<str:auction_id>/players/import/', views.PlayerImportView.as_view(), name='player-import'),

    # Auction Control
    path('auctions/<str:auction_id>/control/', views.AuctionControlView.as_view(), name='auction-control'),

    # Bids
    path('auctions/<str:auction_id>/bids/', views.BidListCreateView.as_view(), name='bid-list-create'),

    # Results
    path('auctions/<str:auction_id>/results/', views.ResultListView.as_view(), name='results'),
]
