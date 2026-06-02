import hashlib

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core.models import KioskStation


class KioskTokenAuthentication(BaseAuthentication):
    """
    Authenticates requests using the ``Authorization: KioskKey <key>`` header.

    Each kiosk station is provisioned with a unique API key.  Only the SHA-256
    hash and an 8-character prefix are stored server-side.  The lookup uses the
    prefix for a fast indexed query, then verifies the full hash.

    On success the view receives:
        request.user  = station.service_user   (satisfies all existing FK constraints)
        request.auth  = KioskStation instance
    The station is also attached as ``request.kiosk_station`` for convenience.
    """

    keyword = 'KioskKey'

    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith(f'{self.keyword} '):
            return None  # Not our scheme — let other authenticators try.

        raw_key = auth_header[len(self.keyword) + 1:]
        if len(raw_key) < 8:
            raise AuthenticationFailed('Invalid kiosk API key.')

        prefix = raw_key[:8]
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        try:
            station = (
                KioskStation.objects
                .select_related('service_user', 'service_user__role')
                .get(api_key_prefix=prefix, api_key_hash=key_hash, is_active=True)
            )
        except KioskStation.DoesNotExist:
            raise AuthenticationFailed('Invalid kiosk API key.')

        # Update heartbeat (fire-and-forget — single SQL UPDATE, no model reload).
        KioskStation.objects.filter(pk=station.pk).update(last_heartbeat=timezone.now())

        # Attach station for downstream views and permissions.
        request.kiosk_station = station
        return (station.service_user, station)

    def authenticate_header(self, request):
        return self.keyword
