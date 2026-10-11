from rest_framework.throttling import SimpleRateThrottle


class _ChannelThrottle(SimpleRateThrottle):
    """Base throttle keyed by sales channel, not by user or address."""

    def get_cache_key(self, request, view):
        channel = getattr(request, 'channel', None)
        if channel is None:
            return None  # Not a channel request.
        return self.cache_format % {
            'scope': self.scope,
            'ident': f'channel-{channel.pk}',
        }


class StorefrontReadThrottle(_ChannelThrottle):
    scope = 'storefront_read'
