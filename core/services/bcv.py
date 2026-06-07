"""
Secondary-currency exchange-rate auto update.

Fetches the current rate from a configurable JSON HTTP source (default:
DolarApi Venezuela's official BCV endpoint) and writes it into
``SystemSettings.secondary_exchange_rate``.

The source URL and the dotted JSON field path are admin-configurable, so any
endpoint that returns the rate as a JSON number can be used:

  default url   : https://ve.dolarapi.com/v1/dolares/oficial
  default field : promedio   ->  {"promedio": 36.42, ...}

A nested value is reachable with a dotted path, e.g. ``data.rate`` for
``{"data": {"rate": 36.42}}``.
"""

from decimal import Decimal, InvalidOperation

import requests
from django.utils import timezone

from core.models import SystemSettings


DEFAULT_TIMEOUT_SECONDS = 15


class BCVRateError(Exception):
    """Raised when the secondary-currency rate cannot be fetched or parsed."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self):
        return self.message


def _resolve_path(data, dotted_path):
    current = data
    for key in dotted_path.split('.'):
        key = key.strip()
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def fetch_rate(settings=None, timeout=None):
    """
    Fetch the rate from the configured source and return it as a Decimal.

    Does not write anything. Raises BCVRateError on any failure.
    """
    settings = settings or SystemSettings.get()
    url = (settings.secondary_rate_source_url or '').strip()
    field = (settings.secondary_rate_source_field or '').strip()

    if not url:
        raise BCVRateError('not_configured', 'No rate source URL is configured.')
    if not field:
        raise BCVRateError('not_configured', 'No rate source field is configured.')

    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS

    try:
        response = requests.get(url, timeout=timeout)
    except requests.Timeout as exc:
        raise BCVRateError('timeout', 'Rate source request timed out.') from exc
    except requests.RequestException as exc:
        raise BCVRateError('network_error', 'Could not reach the rate source.') from exc

    if response.status_code >= 400:
        raise BCVRateError(
            f'http_{response.status_code}',
            f'Rate source returned HTTP {response.status_code}.',
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise BCVRateError('invalid_response', 'Rate source returned non-JSON.') from exc

    raw_value = _resolve_path(data, field)
    if raw_value is None:
        raise BCVRateError(
            'field_not_found',
            f'Field "{field}" not found in the rate source response.',
        )

    try:
        rate = Decimal(str(raw_value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BCVRateError(
            'invalid_rate',
            f'Field "{field}" is not a valid number: {raw_value!r}.',
        ) from exc

    if rate <= 0:
        raise BCVRateError('invalid_rate', 'Fetched rate must be greater than zero.')

    return rate


def update_secondary_exchange_rate(settings=None, timeout=None):
    """
    Fetch the rate and persist it to SystemSettings.

    Returns the new Decimal rate. Raises BCVRateError on failure (and writes
    nothing in that case).
    """
    settings = settings or SystemSettings.get()
    rate = fetch_rate(settings, timeout=timeout)
    settings.secondary_exchange_rate = rate
    settings.secondary_rate_updated_at = timezone.now()
    settings.save(update_fields=['secondary_exchange_rate', 'secondary_rate_updated_at'])
    return rate
