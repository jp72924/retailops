from rest_framework import serializers

from core.models import Role


class RoleSerializer(serializers.ModelSerializer):
    """
    Read-only representation of a Role.
    Roles are seeded data (Admin / Manager / Staff) — they are not
    created or modified via the API.
    """

    class Meta:
        model  = Role
        fields = ['id', 'name', 'description', 'created_at', 'updated_at']
        read_only_fields = fields
