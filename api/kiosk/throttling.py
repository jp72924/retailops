from rest_framework.throttling import SimpleRateThrottle


class _KioskStationThrottle(SimpleRateThrottle):
    """Base throttle keyed by kiosk station ID (not user ID)."""

    def get_cache_key(self, request, view):
        station = getattr(request, 'kiosk_station', None)
        if station is None:
            return None  # Skip throttle if not a kiosk request.
        return self.cache_format % {
            'scope': self.scope,
            'ident': f'kiosk-{station.pk}',
        }


class KioskIdentifyThrottle(_KioskStationThrottle):
    scope = 'kiosk_identify'


class KioskScanThrottle(_KioskStationThrottle):
    scope = 'kiosk_scan'


class KioskCheckoutThrottle(_KioskStationThrottle):
    scope = 'kiosk_checkout'


class KioskPollThrottle(_KioskStationThrottle):
    scope = 'kiosk_poll'
