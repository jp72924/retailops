import os

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.utils import timezone, translation


class KioskCORSMiddleware:
    """
    Adds CORS headers for the RetailOps Kiosk frontend.

    Allows cross-origin requests from the origins listed in the
    KIOSK_CORS_ORIGINS environment variable (comma-separated).
    In DEBUG mode, localhost origins are permitted by default.

    Only applies to /api/v1/ paths — all other paths are unchanged.

    This is intentionally minimal. In production, use a reverse proxy
    (nginx / Caddy) to handle CORS rather than this middleware.
    """

    _KIOSK_PATHS = ('/api/v1/',)

    def __init__(self, get_response):
        self.get_response = get_response
        # Build allowed origin set from environment + debug defaults
        env_origins = os.environ.get('KIOSK_CORS_ORIGINS', '')
        self._allowed = {o.strip() for o in env_origins.split(',') if o.strip()}

    def __call__(self, request):
        origin = request.META.get('HTTP_ORIGIN', '')

        # Only intercept API paths
        is_api = any(request.path.startswith(p) for p in self._KIOSK_PATHS)

        # In DEBUG, allow any localhost / 127.0.0.1 origin automatically
        from django.conf import settings as _s
        if _s.DEBUG and origin:
            import urllib.parse
            parsed = urllib.parse.urlparse(origin)
            if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
                self._allowed.add(origin)

        # Preflight
        if is_api and request.method == 'OPTIONS' and origin in self._allowed:
            response = self._preflight_response(origin, request)
            return response

        response = self.get_response(request)

        if is_api and origin in self._allowed:
            self._add_cors_headers(response, origin)

        return response

    def _preflight_response(self, origin, request):
        from django.http import HttpResponse
        response = HttpResponse()
        response.status_code = 204
        self._add_cors_headers(response, origin)
        req_headers = request.META.get('HTTP_ACCESS_CONTROL_REQUEST_HEADERS', '')
        if req_headers:
            response['Access-Control-Allow-Headers'] = req_headers
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        response['Access-Control-Max-Age'] = '86400'
        return response

    @staticmethod
    def _add_cors_headers(response, origin):
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Credentials'] = 'true'
        response['Vary'] = 'Origin'


class RegionalMiddleware:
    """
    Activates per-user timezone and language on every authenticated request.
    Timezone is deactivated after the response so it doesn't bleed across requests.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            tz_name = getattr(request.user, 'timezone', 'UTC') or 'UTC'
            lang = getattr(request.user, 'language', 'en') or 'en'
            try:
                timezone.activate(ZoneInfo(tz_name))
            except (ZoneInfoNotFoundError, KeyError):
                timezone.activate(ZoneInfo('UTC'))
            translation.activate(lang)
        else:
            timezone.deactivate()

        response = self.get_response(request)
        timezone.deactivate()
        return response
