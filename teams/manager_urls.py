from django.urls import path
from .views import ManagerTeamListView, ManagerTeamDetailView, ManagerSeasonListView

urlpatterns = [
    path('team-onboarding/', ManagerTeamListView.as_view(), name='manager-team-onboarding-list'),
    path('team-onboarding/seasons/', ManagerSeasonListView.as_view(), name='manager-team-onboarding-seasons'),
    path('team-onboarding/<int:pk>/', ManagerTeamDetailView.as_view(), name='manager-team-onboarding-detail'),
]
