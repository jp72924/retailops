from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from core.models import Role
from api.permissions import IsAdminRole
from api.serializers.role import RoleSerializer


class RoleViewSet(ReadOnlyModelViewSet):
    """
    GET /api/v1/roles/       — list all roles
    GET /api/v1/roles/<id>/  — retrieve a single role

    Admin only. Roles are seeded reference data (Admin / Manager / Staff)
    and are not created, updated, or deleted via the API.
    """
    queryset           = Role.objects.order_by('name')
    serializer_class   = RoleSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
