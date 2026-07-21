from django.urls import path
from .views import ManagerPlayerListView, ManagerPlayerDetailView, ManagerPlayerVerifyView

urlpatterns = [
    path('players/', ManagerPlayerListView.as_view(), name='manager-players-list'),
    path('players/<int:pk>/', ManagerPlayerDetailView.as_view(), name='manager-player-detail'),
    path('players/<int:pk>/verify/', ManagerPlayerVerifyView.as_view(), name='manager-player-verify'),
]
