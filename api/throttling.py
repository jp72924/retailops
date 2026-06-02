from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """
    IP-based throttle applied only to POST /api/v1/auth/token/.

    Isolates the login endpoint into its own named scope so its rate can be
    tuned independently of any other anonymous endpoint.  The global
    AnonRateThrottle has been removed from DEFAULT_THROTTLE_CLASSES so this
    is the only anon throttle in effect.

    Rate: DEFAULT_THROTTLE_RATES['login'] (default 20/min).
    """
    scope = 'login'


class PasswordChangeRateThrottle(UserRateThrottle):
    """
    Per-user throttle for POST /api/v1/users/<id>/change-password/.

    Prevents an authenticated caller from rapidly cycling password attempts
    on the accounts they manage.

    Rate: DEFAULT_THROTTLE_RATES['password_change'] (default 10/min).
    """
    scope = 'password_change'


class OrderTransitionRateThrottle(UserRateThrottle):
    """
    Per-user throttle for the six order lifecycle-transition actions:
    submit, confirm, ship, deliver, cancel, refund.

    Prevents rapid-fire state changes that could cause inconsistent stock
    levels or duplicate inventory movements under concurrent API use.

    Rate: DEFAULT_THROTTLE_RATES['order_transition'] (default 60/min).
    """
    scope = 'order_transition'


class PasswordResetRateThrottle(AnonRateThrottle):
    """
    IP-based throttle for both password-reset endpoints:
      POST /api/v1/auth/password-reset/
      POST /api/v1/auth/password-reset/confirm/

    Prevents automated enumeration and token-guessing attacks.
    Deliberately strict because these are unauthenticated write operations.

    Rate: DEFAULT_THROTTLE_RATES['password_reset'] (default 5/min).
    """
    scope = 'password_reset'


class InventoryAdjustRateThrottle(UserRateThrottle):
    """
    Per-user throttle for POST /api/v1/inventory/adjust/.

    Manual adjustments are audited write operations; a lower ceiling
    discourages scripted misuse and keeps audit logs readable.

    Rate: DEFAULT_THROTTLE_RATES['inventory_adjust'] (default 30/min).
    """
    scope = 'inventory_adjust'


class OcrVerifyRateThrottle(SimpleRateThrottle):
    """
    Throttle for POST /api/v1/payments/receipts/verify/.

    Kiosk requests are keyed by station so one busy terminal does not consume
    another station's allowance. Back-office requests fall back to user ID.

    Rate: DEFAULT_THROTTLE_RATES['ocr_verify'] (default 12/min).
    """
    scope = 'ocr_verify'

    def get_cache_key(self, request, view):
        station = getattr(request, 'kiosk_station', None)
        if station is not None:
            ident = f'kiosk-{station.pk}'
        elif request.user is not None and request.user.is_authenticated:
            ident = f'user-{request.user.pk}'
        else:
            ident = self.get_ident(request)

        return self.cache_format % {
            'scope': self.scope,
            'ident': ident,
        }
