from django.db.models import Q
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from auction.permissions import IsManagerPermission
from .models import Team, Season
from .serializers import TeamSerializer, SeasonSerializer, TeamOnboardingSerializer, TeamOnboardingUpdateSerializer

class PublicTeamListView(generics.ListAPIView):
    serializer_class = TeamSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return Team.objects.filter(is_verified=True).select_related('season').order_by('-created_at')

class TeamApplyView(generics.CreateAPIView):
    serializer_class = TeamSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        active_season = Season.objects.filter(is_active=True).first()
        if active_season and not serializer.validated_data.get('season'):
            serializer.save(season=active_season)
        else:
            serializer.save()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            {"message": "Thank you for your interest. Application submitted successfully.", "data": serializer.data},
            status=status.HTTP_201_CREATED,
            headers=headers
        )


class ManagerTeamPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class ManagerTeamListView(generics.ListAPIView):
    """Paginated list of every registered team (verified or not) for the manager onboarding page."""
    serializer_class = TeamOnboardingSerializer
    permission_classes = [IsManagerPermission]
    pagination_class = ManagerTeamPagination

    def get_queryset(self):
        qs = Team.objects.select_related('season').order_by('-created_at')
        params = self.request.query_params

        season = params.get('season')
        if season:
            qs = qs.filter(season_id=season)

        payment_status = params.get('payment_status')
        if payment_status:
            qs = qs.filter(payment_status=payment_status)

        contacted = params.get('contacted')
        if contacted in ('true', 'false'):
            qs = qs.filter(contacted=(contacted == 'true'))

        search = params.get('search')
        if search:
            qs = qs.filter(
                Q(team_name__icontains=search)
                | Q(leader_name__icontains=search)
                | Q(leader_contact_number__icontains=search)
            )

        return qs


class ManagerTeamDetailView(generics.RetrieveUpdateAPIView):
    """View a single registered team, or update its onboarding-tracking fields."""
    queryset = Team.objects.select_related('season').all()
    permission_classes = [IsManagerPermission]

    def get_serializer_class(self):
        if self.request.method in ('PATCH', 'PUT'):
            return TeamOnboardingUpdateSerializer
        return TeamOnboardingSerializer

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        # Return the full record (including read-only fields) after a successful update
        instance = self.get_object()
        return Response(TeamOnboardingSerializer(instance).data)


class ManagerSeasonListView(generics.ListAPIView):
    """Season options for the onboarding page's season selector."""
    serializer_class = SeasonSerializer
    permission_classes = [IsManagerPermission]
    queryset = Season.objects.all().order_by('-created_at')
