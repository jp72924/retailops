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


class StorefrontOrderThrottle(_ChannelThrottle):
    scope = 'storefront_order'


class StorefrontEntitlementThrottle(_ChannelThrottle):
    """Supplying and revealing delivered keys: sensitive, and never a bulk operation."""
    scope = 'storefront_entitlement'


class StorefrontPaymentThrottle(_ChannelThrottle):
    """Caps what a channel can spend on receipt OCR, which is a paid third-party call."""
    scope = 'storefront_payment'
