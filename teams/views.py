from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from .models import Team
from .serializers import TeamSerializer

class PublicTeamListView(generics.ListAPIView):
    serializer_class = TeamSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return Team.objects.filter(is_verified=True).order_by('-created_at')

class TeamApplyView(generics.CreateAPIView):
    serializer_class = TeamSerializer
    permission_classes = [AllowAny]

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
