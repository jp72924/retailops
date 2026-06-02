from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template
from django.utils.html import format_html

from core.models import SystemSettings

register = template.Library()


def _safe_settings():
    try:
        return SystemSettings.get()
    except Exception:
        return None


def _format_amount(value, places):
    """Format a numeric/Decimal value with thousands separators and N decimals."""
    try:
        numeric = Decimal(str(value)) if value not in (None, '') else Decimal('0')
    except (InvalidOperation, ValueError):
        return str(value)
    fmt = f'{{:,.{places}f}}'
    return fmt.format(numeric)


@register.filter
def currency(value):
    """Format a value using the primary currency and, when enabled, append a
    secondary-currency conversion in a muted <span> next to it.

    Returns plain text (backward compatible) when no secondary currency is
    enabled, or an XSS-safe HTML fragment when enabled. Symbols are admin-
    editable so format_html is used to escape every placeholder.
    """
    settings = _safe_settings()
    symbol = settings.currency_symbol if settings else '$'
    places = settings.decimal_places if settings else 2

    primary_str = _format_amount(value, places)

    if not settings or not settings.secondary_currency_enabled:
        return f'{symbol}{primary_str}'

    try:
        numeric = Decimal(str(value)) if value not in (None, '') else Decimal('0')
    except (InvalidOperation, ValueError):
        return f'{symbol}{primary_str}'

    sec_places = settings.secondary_decimal_places
    quantum = Decimal(10) ** -sec_places
    sec_amount = (numeric * settings.secondary_exchange_rate).quantize(
        quantum, rounding=ROUND_HALF_UP
    )
    sec_str = _format_amount(sec_amount, sec_places)

    return format_html(
        '{}{}<span class="currency-secondary"> \u2248 {}{}</span>',
        symbol, primary_str, settings.secondary_currency_symbol, sec_str,
    )


@register.filter
def currency_plain(value):
    """Always primary-only plain text — for CSV, email, or any non-HTML context."""
    settings = _safe_settings()
    symbol = settings.currency_symbol if settings else '$'
    places = settings.decimal_places if settings else 2
    return f'{symbol}{_format_amount(value, places)}'


@register.filter
def currency_secondary(value):
    """Return the secondary-currency approximation as plain text (no wrapper).

    Empty string when secondary currency is disabled. Used in contexts where
    the primary and secondary pieces live in separate DOM nodes (e.g. JS
    recalc cells where |currency's combined HTML would get overwritten).
    """
    settings = _safe_settings()
    if not settings or not settings.secondary_currency_enabled:
        return ''
    try:
        numeric = Decimal(str(value)) if value not in (None, '') else Decimal('0')
    except (InvalidOperation, ValueError):
        return ''
    sec_places = settings.secondary_decimal_places
    quantum = Decimal(10) ** -sec_places
    sec_amount = (numeric * settings.secondary_exchange_rate).quantize(
        quantum, rounding=ROUND_HALF_UP
    )
    return f' \u2248 {settings.secondary_currency_symbol}{_format_amount(sec_amount, sec_places)}'
