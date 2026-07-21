from django.urls import path
from .views import PublicPlayerListView, PlayerApplyView, PublicPlayerDetailView

urlpatterns = [
    path('', PublicPlayerListView.as_view(), name='public-players-list'),
    path('apply/', PlayerApplyView.as_view(), name='player-apply'),
    path('<str:player_id>/', PublicPlayerDetailView.as_view(), name='public-player-detail'),
]
