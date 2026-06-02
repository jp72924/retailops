from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from core.models import User
from api.permissions import IsAdminRole
from api.throttling import PasswordChangeRateThrottle
from api.serializers.user import (
    ChangePasswordSerializer,
    UserReadSerializer,
    UserWriteSerializer,
)


class UserViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    GenericViewSet,
):
    """
    User management endpoints. Admin-only except where noted.

    GET    /api/v1/users/              — list all users (Admin)
    POST   /api/v1/users/              — create user with role (Admin)
    GET    /api/v1/users/<id>/         — retrieve user (Admin, or the user themselves)
    PATCH  /api/v1/users/<id>/         — update profile fields (Admin)
    POST   /api/v1/users/<id>/change-password/  — set new password (Admin)
    POST   /api/v1/users/<id>/deactivate/       — set is_active=False (Admin)
    POST   /api/v1/users/<id>/reactivate/       — set is_active=True  (Admin)

    DELETE is intentionally absent — use deactivate instead.
    """
    queryset = User.objects.select_related('role').order_by('first_name', 'last_name')

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve'):
            return UserReadSerializer
        return UserWriteSerializer

    def get_permissions(self):
        # Any authenticated user can retrieve their own profile.
        # All other actions are Admin-only.
        if self.action == 'retrieve':
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsAdminRole()]

    def get_throttles(self):
        if self.action == 'change_password':
            return [PasswordChangeRateThrottle()]
        return super().get_throttles()

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # Allow self-retrieval; block non-admins from reading other users' data.
        if instance.pk != request.user.pk:
            if not (request.user.role and request.user.role.name == 'Admin'):
                raise PermissionDenied()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    # ── Actions ───────────────────────────────────────────────────────────────

    @action(detail=True, methods=['post'], url_path='change-password')
    def change_password(self, request, pk=None):
        """
        POST /api/v1/users/<id>/change-password/

        Body: {"new_password": "...", "confirm_password": "..."}
        Sets the target user's password. Admin only.
        """
        user = self.get_object()
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user.set_password(serializer.validated_data['new_password'])
        user.save()
        return Response({'detail': f'Password updated for {user.get_full_name()}.'})

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        """
        POST /api/v1/users/<id>/deactivate/

        Sets is_active=False. Admin only.
        Guards against self-deactivation.
        """
        user = self.get_object()
        if user.pk == request.user.pk:
            return Response(
                {'error': 'You cannot deactivate your own account.', 'code': 'conflict'},
                status=status.HTTP_409_CONFLICT,
            )
        user.is_active = False
        user.save()
        return Response({'detail': f'{user.get_full_name()} deactivated.'})

    @action(detail=True, methods=['post'])
    def reactivate(self, request, pk=None):
        """
        POST /api/v1/users/<id>/reactivate/

        Sets is_active=True. Admin only.
        """
        user = self.get_object()
        user.is_active = True
        user.save()
        return Response({'detail': f'{user.get_full_name()} reactivated.'})
