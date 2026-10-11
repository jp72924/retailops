import hashlib

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core.models import ChannelKey


class ChannelKeyAuthentication(BaseAuthentication):
    """
    Authenticates a sales channel with ``Authorization: ChannelKey <secret>``.

    The channel's secret lives on the storefront's server and never in a
    browser, so there is no public key and no CORS to configure. Only the
    SHA-256 of each secret and an 8-character prefix are stored; the prefix
    narrows the lookup to an index and the hash decides.

    Unlike the kiosk's authentication this writes nothing: it is one SELECT per
    request, with no heartbeat to update. A key authenticates only while it is
    not revoked, its channel is active, and the channel's service user is
    active.

    On success the view receives:
        request.user     = the channel's service user
        request.auth     = the ChannelKey that was presented
        request.channel  = the SalesChannel
    """

    keyword = 'ChannelKey'

    def authenticate(self, request):
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if not header.startswith(f'{self.keyword} '):
            return None  # Not our scheme; the kiosk and staff APIs use their own.

        raw_key = header[len(self.keyword) + 1:].strip()
        if len(raw_key) < 8:
            raise AuthenticationFailed('Invalid channel key.')

        try:
            key = (
                ChannelKey.objects
                .select_related('channel', 'channel__service_user', 'channel__service_user__role')
                .get(
                    prefix=raw_key[:8],
                    key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                    revoked_at__isnull=True,
                    channel__is_active=True,
                    channel__service_user__is_active=True,
                )
            )
        except ChannelKey.DoesNotExist:
            raise AuthenticationFailed('Invalid channel key.')

        request.channel = key.channel
        return (key.channel.service_user, key)

    def authenticate_header(self, request):
        return self.keyword


class ChannelKeyScheme(OpenApiAuthenticationExtension):
    target_class = 'api.storefront.authentication.ChannelKeyAuthentication'
    name = 'ChannelKey'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            'description': (
                'Send `Authorization: ChannelKey <secret>`. The secret is issued once, '
                'when the channel is provisioned or a key is added, and belongs on the '
                'storefront\'s server only.'
            ),
        }
