from django.db.models import Q
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

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

# Mirrors ezone-hub/src/lib/playerTiers.ts — keep the two in sync.
TIER_RATING_RANGES = {
    'base': (62, 75),
    'highlight': (76, 89),
    'showtime': (90, 98),
    'epic': (99, 105),
    'bigtime': (106, None),
}


class PublicPlayerPagination(PageNumberPagination):
    """Page-based pagination with a richer metadata block than DRF's default
    {count, next, previous} — the frontend renders page numbers and an
    'X-Y of N' range directly from this."""
    page_size = 15
    page_size_query_param = 'page_size'
    max_page_size = 50

    def get_paginated_response(self, data):
        paginator = self.page.paginator
        return Response({
            'results': data,
            'pagination': {
                'total_items': paginator.count,
                'total_pages': paginator.num_pages,
                'per_page': paginator.per_page,
                'current_page': self.page.number,
                'from_item': self.page.start_index(),
                'to_item': self.page.end_index(),
                'has_next': self.page.has_next(),
                'has_previous': self.page.has_previous(),
            },
        })


class PublicPlayerListView(generics.ListAPIView):
    serializer_class = PublicPlayerListSerializer
    permission_classes = [AllowAny]
    pagination_class = PublicPlayerPagination

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

        tier_range = TIER_RATING_RANGES.get(params.get('tier'))
        if tier_range:
            min_rating, max_rating = tier_range
            qs = qs.filter(rating__gte=min_rating)
            if max_rating is not None:
                qs = qs.filter(rating__lte=max_rating)

        sort = SORT_FIELDS.get(params.get('sort'), '-rating')
        return qs.order_by(sort)


class PublicPlayerDetailView(generics.RetrieveAPIView):
    serializer_class = PublicPlayerDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = 'player_id'
    queryset = PlayerProfile.objects.filter(is_verified=True)


class PublicPlayerMatchesView(APIView):
    """Paginated match history for the 'load more' control on the profile page —
    the detail endpoint only ships the first page inline."""
    permission_classes = [AllowAny]

    def get(self, request, player_id):
        try:
            profile = PlayerProfile.objects.get(player_id=player_id, is_verified=True)
        except PlayerProfile.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .rating_engine import get_recent_matches

        try:
            limit = min(50, max(1, int(request.query_params.get('limit', 20))))
            offset = max(0, int(request.query_params.get('offset', 0)))
        except ValueError:
            return Response({'error': 'limit and offset must be integers.'}, status=400)

        return Response(get_recent_matches(profile, limit=limit, offset=offset))


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

        is_rejected = params.get('is_rejected')
        if is_rejected in ('true', 'false'):
            qs = qs.filter(is_rejected=(is_rejected == 'true'))

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
