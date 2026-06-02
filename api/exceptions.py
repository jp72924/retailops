import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Standardises all API error responses to the envelope:

        {
            "error":   "Human-readable summary.",
            "code":    "machine_readable_code",
            "details": {"field": ["message"]}   # omitted when not applicable
        }

    Converts Django's Http404 and PermissionDenied into DRF equivalents so
    they are handled here rather than falling through to Django's HTML error
    pages.
    """
    # Translate Django exceptions into DRF equivalents.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    # Let DRF do its default processing first (sets exc.status_code etc.).
    response = drf_exception_handler(exc, context)

    if response is None:
        # Unhandled exception — log it, return a generic 500.
        logger.exception('Unhandled exception in API view', exc_info=exc)
        return Response(
            {'error': 'Internal server error.', 'code': 'server_error'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    payload = _build_payload(exc, response)
    response.data = payload
    return response


# ── Payload builders ──────────────────────────────────────────────────────────

def _build_payload(exc, response):
    """Return the standardised error dict for a given DRF exception."""
    if isinstance(exc, exceptions.ValidationError):
        return _validation_payload(exc)
    if isinstance(exc, exceptions.NotAuthenticated):
        return {'error': 'Authentication required.', 'code': 'not_authenticated'}
    if isinstance(exc, exceptions.AuthenticationFailed):
        return {'error': 'Invalid credentials.', 'code': 'authentication_failed'}
    if isinstance(exc, exceptions.PermissionDenied):
        return {'error': 'You do not have permission to perform this action.', 'code': 'permission_denied'}
    if isinstance(exc, exceptions.NotFound):
        return {'error': 'Not found.', 'code': 'not_found'}
    if isinstance(exc, exceptions.MethodNotAllowed):
        return {'error': f'Method "{exc.args[0]}" not allowed.', 'code': 'method_not_allowed'}
    if isinstance(exc, exceptions.Throttled):
        wait = f' Try again in {exc.wait:.0f} seconds.' if exc.wait else ''
        return {'error': f'Request was throttled.{wait}', 'code': 'throttled'}

    # Fallback: surface DRF's detail message with a generic code.
    detail = getattr(exc, 'detail', str(exc))
    if hasattr(detail, '__str__'):
        detail = str(detail)
    return {
        'error': detail,
        'code': getattr(exc, 'default_code', 'error'),
    }


def _validation_payload(exc):
    """
    Converts DRF's nested validation error structure into the standard envelope.

    Non-field errors are placed under the key 'non_field_errors' in `details`.
    """
    raw = exc.detail

    # Normalise: if top-level is a list (non-field errors), wrap it.
    if isinstance(raw, list):
        raw = {'non_field_errors': raw}

    # Flatten ErrorDetail objects to plain strings.
    details = {
        field: [str(msg) for msg in messages] if isinstance(messages, list) else [str(messages)]
        for field, messages in raw.items()
    }

    return {
        'error': 'Validation failed.',
        'code': 'validation_error',
        'details': details,
    }
