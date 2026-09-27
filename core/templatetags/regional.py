"""
Money formatting for templates.

Two families, and which one a figure uses is the whole point:

* `currency`, `currency_plain`, `currency_secondary` render at the LIVE
  configuration. Use them for things priced today: products, outstanding
  balances, and orders still being edited.

* `money`, `money_primary`, `order_money` render at a RECORDED
  configuration. Use them for money that has already moved: payments, and the
  paid portion of an order. They take the configuration explicitly and have no
  live fallback, so a forgotten argument shows a visible marker instead of
  quietly putting today's rate on a historical figure.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template
from django.utils.html import format_html

from core.models import SystemSettings
from core.services.currency import (
    ORDER_AMOUNTS_ATTR, OrderAmounts, order_amounts, secondary_amount,
)

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
        '<span class="currency-pair">{}{}<span class="currency-secondary">\u2248 {}{}</span></span>',
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
    return f'\u2248 {settings.secondary_currency_symbol}{_format_amount(sec_amount, sec_places)}'


# \u2500\u2500 Recorded configuration \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def _pair(amount, ctx, sec_amount, sec_ctx):
    """Primary figure in `ctx`, plus the secondary one in `sec_ctx` when known."""
    primary_str = _format_amount(amount, ctx.decimal_places)
    if sec_amount is None:
        return f'{ctx.currency_symbol}{primary_str}'
    return format_html(
        '<span class="currency-pair">{}{}<span class="currency-secondary">\u2248 {}{}</span></span>',
        ctx.currency_symbol,
        primary_str,
        sec_ctx.secondary_currency_symbol,
        _format_amount(sec_amount, sec_ctx.secondary_decimal_places),
    )


def _missing(value):
    return format_html(
        '{}<span class="currency-missing" title="Currency not supplied to the template">?</span>',
        _format_amount(value, 2),
    )


@register.filter
def money(value, recorded):
    """
    An amount in the currency and at the rate it was recorded with.

        {{ payment.amount|money:payment.currency_snapshot }}
    """
    if not recorded:
        return _missing(value)
    return _pair(value, recorded, secondary_amount(value, recorded), recorded)


@register.filter
def money_primary(value, recorded):
    """An amount in a recorded primary currency, with no secondary figure."""
    if not recorded:
        return _missing(value)
    return f'{recorded.currency_symbol}{_format_amount(value, recorded.decimal_places)}'


@register.filter
def order_money(order, part):
    """
    An order's total or paid amount in both currencies.

        {{ order|order_money:"total" }}    {{ order|order_money:"paid" }}

    Accepts an order or a precomputed OrderAmounts. List views should run the
    orders through `attach_order_amounts()`, which reads the live settings
    once for the whole page; otherwise each row reads them itself. The
    outstanding balance is not offered here on purpose: it is settled at
    today's rate, so it belongs to the live `currency` filter.
    """
    if not order:
        return ''
    if isinstance(order, OrderAmounts):
        amounts = order
    else:
        amounts = getattr(order, ORDER_AMOUNTS_ATTR, None) or order_amounts(order)
    if part == 'paid':
        primary, secondary = amounts.paid, amounts.paid_secondary
    elif part == 'total':
        primary, secondary = amounts.total, amounts.total_secondary
    else:
        raise template.TemplateSyntaxError(
            f'order_money expects "total" or "paid", got {part!r}'
        )
    recorded = amounts.currency
    sec_ctx = recorded if recorded.secondary_currency_enabled else amounts.live
    return _pair(primary, recorded, secondary, sec_ctx)
