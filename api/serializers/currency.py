"""
Currency payloads shared by the payment, order and kiosk endpoints.

Money in these payloads is always a string, matching how DRF renders every
DecimalField in this API. DRF's JSON encoder would otherwise turn a bare
Decimal into a float, and a bolívar amount can carry more significant digits
than a float holds exactly.

The *Serializer classes exist to document the shapes in the OpenAPI schema;
the payloads themselves are built by the functions below.
"""
from decimal import Decimal

from rest_framework import serializers

from core.services.currency import current_context, secondary_amount


def _money(value, places):
    if value is None:
        return None
    quantum = Decimal(10) ** -int(places)
    return format(Decimal(str(value)).quantize(quantum), 'f')


def _timestamp(value):
    return serializers.DateTimeField().to_representation(value) if value else None


def live_currency(serializer):
    """
    The live currency context, resolved once per request.

    Every serializer in a tree shares the root's context dict, so caching it
    there turns a list of 25 orders into one settings read instead of 25.
    """
    context = serializer.context
    if '_live_currency' not in context:
        context['_live_currency'] = current_context()
    return context['_live_currency']


# ── Payments ──────────────────────────────────────────────────────────────────

class RecordedSecondaryCurrencySerializer(serializers.Serializer):
    code = serializers.CharField()
    symbol = serializers.CharField()
    decimal_places = serializers.IntegerField()
    rate = serializers.CharField(help_text='Secondary units per 1 primary unit.')
    rate_as_of = serializers.DateTimeField(allow_null=True)
    rate_source = serializers.ChoiceField(
        choices=['fetched', 'manual', 'unknown', 'backfilled'],
        help_text=(
            '"backfilled" marks a payment recorded before rates were tracked; '
            'its rate is the one in effect when tracking began, so its '
            'secondary amount is approximate.'
        ),
    )


class RecordedCurrencySerializer(serializers.Serializer):
    code = serializers.CharField()
    symbol = serializers.CharField()
    decimal_places = serializers.IntegerField()
    secondary = RecordedSecondaryCurrencySerializer(allow_null=True)


def recorded_currency_payload(ctx):
    """The currency a payment was recorded in. `ctx` is a CurrencySnapshot or context."""
    secondary = None
    if ctx.secondary_currency_enabled:
        secondary = {
            'code': ctx.secondary_currency_code,
            'symbol': ctx.secondary_currency_symbol,
            'decimal_places': ctx.secondary_decimal_places,
            'rate': _money(ctx.secondary_exchange_rate, 8),
            'rate_as_of': _timestamp(ctx.rate_as_of),
            'rate_source': ctx.rate_source,
        }
    return {
        'code': ctx.currency_code,
        'symbol': ctx.currency_symbol,
        'decimal_places': ctx.decimal_places,
        'secondary': secondary,
    }


def secondary_amount_payload(amount, ctx):
    converted = secondary_amount(amount, ctx)
    return _money(converted, ctx.secondary_decimal_places) if converted is not None else None


# ── Orders ────────────────────────────────────────────────────────────────────

class OrderCurrencySerializer(serializers.Serializer):
    basis = serializers.ChoiceField(
        choices=['live', 'recorded'],
        help_text=(
            '"recorded": figures use the currency of the latest confirmed payment. '
            '"live": no payment confirmed yet, so the current settings apply.'
        ),
    )
    code = serializers.CharField()
    symbol = serializers.CharField()
    decimal_places = serializers.IntegerField()


class OrderSecondaryAmountsSerializer(serializers.Serializer):
    code = serializers.CharField()
    symbol = serializers.CharField()
    decimal_places = serializers.IntegerField()
    live_rate = serializers.CharField(allow_null=True)
    amount_paid = serializers.CharField(
        allow_null=True,
        help_text='Sum of confirmed payments, each at the rate it was recorded at. Never changes.',
    )
    amount_outstanding = serializers.CharField(
        allow_null=True,
        help_text='Outstanding balance at the live rate. Changes with the rate.',
    )
    total_amount = serializers.CharField(
        allow_null=True,
        help_text='amount_paid + amount_outstanding. Fixed once the order is fully paid.',
    )


def order_currency_payload(amounts):
    ctx = amounts.currency
    return {
        'basis': amounts.basis,
        'code': ctx.currency_code,
        'symbol': ctx.currency_symbol,
        'decimal_places': ctx.decimal_places,
    }


def order_secondary_payload(amounts):
    recorded, live = amounts.currency, amounts.live
    if not (recorded.secondary_currency_enabled or live.secondary_currency_enabled):
        return None
    identity = recorded if recorded.secondary_currency_enabled else live
    places = identity.secondary_decimal_places
    return {
        'code': identity.secondary_currency_code,
        'symbol': identity.secondary_currency_symbol,
        'decimal_places': places,
        'live_rate': (
            _money(live.secondary_exchange_rate, 8)
            if live.secondary_currency_enabled else None
        ),
        'amount_paid': _money(amounts.paid_secondary, places),
        'amount_outstanding': _money(amounts.outstanding_secondary, live.secondary_decimal_places),
        'total_amount': _money(amounts.total_secondary, places),
    }
