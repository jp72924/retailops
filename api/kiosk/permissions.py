from rest_framework.permissions import BasePermission

from core.models import KioskStation


class IsKioskStation(BasePermission):
    """Allow access only if the request was authenticated via KioskTokenAuthentication."""

    def has_permission(self, request, view):
        return (
            hasattr(request, 'kiosk_station')
            and isinstance(request.auth, KioskStation)
            and request.kiosk_station.is_active
        )
