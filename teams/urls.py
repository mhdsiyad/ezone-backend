from django.urls import path
from .views import PublicTeamListView, TeamApplyView

urlpatterns = [
    path('', PublicTeamListView.as_view(), name='public-teams-list'),
    path('apply/', TeamApplyView.as_view(), name='team-apply'),
]
