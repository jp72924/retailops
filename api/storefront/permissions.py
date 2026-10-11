from rest_framework.permissions import BasePermission

from core.models import ChannelKey


class IsSalesChannel(BasePermission):
    """Allow access only to requests authenticated with a channel key."""

    def has_permission(self, request, view):
        return isinstance(request.auth, ChannelKey)
