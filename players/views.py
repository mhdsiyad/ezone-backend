from django.db.models import Q
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from auction.permissions import IsManagerPermission

from .models import PlayerProfile
from .serializers import (
    PlayerApplySerializer,
    PublicPlayerListSerializer,
    PublicPlayerDetailSerializer,
    ManagerPlayerListSerializer,
    ManagerPlayerDetailSerializer,
    ManagerPlayerUpdateSerializer,
    PlayerVerifySerializer,
)
from .services import verify_player, unverify_player


class PlayerApplyView(generics.CreateAPIView):
    serializer_class = PlayerApplySerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            {"message": "Thank you for applying. We'll review your profile and get in touch.", "data": serializer.data},
            status=status.HTTP_201_CREATED,
            headers=headers,
        )


SORT_FIELDS = {
    'rating': 'rating',
    '-rating': '-rating',
    'name': 'name',
    '-name': '-name',
}


class PublicPlayerListView(generics.ListAPIView):
    serializer_class = PublicPlayerListSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = PlayerProfile.objects.filter(is_verified=True)
        params = self.request.query_params

        search = params.get('search')
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(player_id__icontains=search)
                | Q(efootball_id__icontains=search)
            )

        sort = SORT_FIELDS.get(params.get('sort'), '-rating')
        return qs.order_by(sort)


class PublicPlayerDetailView(generics.RetrieveAPIView):
    serializer_class = PublicPlayerDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = 'player_id'
    queryset = PlayerProfile.objects.filter(is_verified=True)


class ManagerPlayerPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class ManagerPlayerListView(generics.ListAPIView):
    serializer_class = ManagerPlayerListSerializer
    permission_classes = [IsManagerPermission]
    pagination_class = ManagerPlayerPagination

    def get_queryset(self):
        qs = PlayerProfile.objects.all().order_by('-applied_at')
        params = self.request.query_params

        is_verified = params.get('is_verified')
        if is_verified in ('true', 'false'):
            qs = qs.filter(is_verified=(is_verified == 'true'))

        contacted = params.get('contacted')
        if contacted in ('true', 'false'):
            qs = qs.filter(contacted=(contacted == 'true'))

        search = params.get('search')
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(player_id__icontains=search)
                | Q(efootball_id__icontains=search)
                | Q(phone_number__icontains=search)
            )

        return qs


class ManagerPlayerDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = PlayerProfile.objects.all()
    permission_classes = [IsManagerPermission]

    def get_serializer_class(self):
        if self.request.method in ('PATCH', 'PUT'):
            return ManagerPlayerUpdateSerializer
        return ManagerPlayerDetailSerializer

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        instance = self.get_object()
        return Response(ManagerPlayerDetailSerializer(instance).data)


class ManagerPlayerVerifyView(generics.UpdateAPIView):
    queryset = PlayerProfile.objects.all()
    serializer_class = PlayerVerifySerializer
    permission_classes = [IsManagerPermission]

    def update(self, request, *args, **kwargs):
        player = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if serializer.validated_data['is_verified']:
            verify_player(player)
        else:
            unverify_player(player)

        return Response(ManagerPlayerDetailSerializer(player).data)
