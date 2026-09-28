"""
Currency identity and exchange rate as they stood when money moved.

The live currency configuration lives in the SystemSettings singleton and can
change at any time: the symbol is edited, the decimals change, the BCV rate is
refreshed every business day. A payment must keep showing the currency and the
rate it was recorded with, so each Payment references an immutable
CurrencySnapshot row describing them.

Snapshots are interned: every payment recorded under the same configuration
points at the same row, found by `fingerprint`. The table therefore grows by
roughly one row per rate update that a payment actually used, not one per
payment.

Orders store nothing. An order is an obligation in the primary currency; its
secondary-currency value is derived by `order_amounts()` from its confirmed
payments (each at its own recorded rate) plus the outstanding balance at the
live rate. A fully paid order is therefore fully frozen, while an unpaid
balance keeps following the rate it will actually be settled at.

This module deliberately does not import core.models at module level:
core.models imports it.
"""
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

RATE_QUANTUM = Decimal('0.00000001')  # SystemSettings.secondary_exchange_rate has 8 decimals

CONVERSION_RULE_V1 = 'half_up_v1'

RATE_SOURCE_FETCHED = 'fetched'
RATE_SOURCE_MANUAL = 'manual'
RATE_SOURCE_UNKNOWN = 'unknown'
RATE_SOURCE_BACKFILLED = 'backfilled'

# Bump the leading tag if the canonical form ever changes. A different
# fingerprint for identical values only produces a duplicate snapshot row,
# never a wrong one, so versioning is about tidiness rather than safety.
FINGERPRINT_VERSION = 'v1'


def normalize_rate(value) -> Decimal:
    return Decimal(str(value)).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _normalize_timestamp(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value.astimezone(dt_timezone.utc)


@dataclass(frozen=True)
class CurrencyContext:
    """
    The currency configuration a monetary figure is expressed in.

    Attribute names mirror SystemSettings, so code that only reads currency
    attributes accepts a SystemSettings row, a CurrencyContext, or a
    CurrencySnapshot interchangeably.
    """
    currency_code: str
    currency_symbol: str
    decimal_places: int
    secondary_currency_enabled: bool
    secondary_currency_code: str
    secondary_currency_symbol: str
    secondary_decimal_places: int
    secondary_exchange_rate: Decimal
    rate_as_of: Optional[datetime]
    rate_source: str
    conversion_rule: str = CONVERSION_RULE_V1

    @classmethod
    def build(cls, **fields) -> 'CurrencyContext':
        """
        Construct a normalized context.

        Normalizing before storing and before hashing is what makes interning
        work: an in-memory Decimal('36.5') and a stored 36.50000000 must hash
        identically. Symbols are NFC-normalized but deliberately not stripped,
        because the display concatenates symbol and amount and a trailing space
        can be intentional.
        """
        return cls(
            currency_code=(fields['currency_code'] or '').strip().upper(),
            currency_symbol=unicodedata.normalize('NFC', fields['currency_symbol'] or ''),
            decimal_places=int(fields['decimal_places']),
            secondary_currency_enabled=bool(fields['secondary_currency_enabled']),
            secondary_currency_code=(fields['secondary_currency_code'] or '').strip().upper(),
            secondary_currency_symbol=unicodedata.normalize(
                'NFC', fields['secondary_currency_symbol'] or ''
            ),
            secondary_decimal_places=int(fields['secondary_decimal_places']),
            secondary_exchange_rate=normalize_rate(fields['secondary_exchange_rate']),
            rate_as_of=_normalize_timestamp(fields.get('rate_as_of')),
            rate_source=fields.get('rate_source') or RATE_SOURCE_UNKNOWN,
            conversion_rule=fields.get('conversion_rule') or CONVERSION_RULE_V1,
        )

    @classmethod
    def from_settings(cls, settings) -> 'CurrencyContext':
        return cls.build(
            currency_code=settings.currency_code,
            currency_symbol=settings.currency_symbol,
            decimal_places=settings.decimal_places,
            secondary_currency_enabled=settings.secondary_currency_enabled,
            secondary_currency_code=settings.secondary_currency_code,
            secondary_currency_symbol=settings.secondary_currency_symbol,
            secondary_decimal_places=settings.secondary_decimal_places,
            secondary_exchange_rate=settings.secondary_exchange_rate,
            rate_as_of=settings.secondary_rate_updated_at,
            rate_source=getattr(settings, 'secondary_rate_source', None) or RATE_SOURCE_UNKNOWN,
        )

    def canonical(self) -> list:
        # A JSON array rather than a delimiter-joined string, so no symbol can
        # smuggle in a separator and collide with a different configuration.
        return [
            FINGERPRINT_VERSION,
            self.currency_code,
            self.currency_symbol,
            self.decimal_places,
            self.secondary_currency_enabled,
            self.secondary_currency_code,
            self.secondary_currency_symbol,
            self.secondary_decimal_places,
            format(self.secondary_exchange_rate, 'f'),
            (
                self.rate_as_of.isoformat(timespec='microseconds')
                if self.rate_as_of is not None else None
            ),
            self.rate_source,
            self.conversion_rule,
        ]

    def fingerprint(self) -> str:
        payload = json.dumps(self.canonical(), ensure_ascii=False, separators=(',', ':'))
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def model_fields(self) -> dict:
        """Field values for creating a CurrencySnapshot row."""
        return {
            'currency_code': self.currency_code,
            'currency_symbol': self.currency_symbol,
            'decimal_places': self.decimal_places,
            'secondary_currency_enabled': self.secondary_currency_enabled,
            'secondary_currency_code': self.secondary_currency_code,
            'secondary_currency_symbol': self.secondary_currency_symbol,
            'secondary_decimal_places': self.secondary_decimal_places,
            'secondary_exchange_rate': self.secondary_exchange_rate,
            'rate_as_of': self.rate_as_of,
            'rate_source': self.rate_source,
            'conversion_rule': self.conversion_rule,
        }


def current_context() -> CurrencyContext:
    """
    The live currency configuration, read from SystemSettings.

    A pure read: unlike SystemSettings.get(), it never inserts the row. An
    unsaved SystemSettings() carries exactly the defaults get_or_create would
    have written, so the result is identical, and rendering a page or
    recording a payment never turns into a write on a fresh database.
    """
    from core.models import SystemSettings
    settings = SystemSettings.objects.filter(pk=1).first() or SystemSettings()
    return CurrencyContext.from_settings(settings)


def secondary_amount(amount, ctx) -> Optional[Decimal]:
    """
    Convert a primary-currency amount into the secondary currency of `ctx`.

    Returns None when `ctx` has no secondary currency enabled. `ctx` may be a
    CurrencyContext, a CurrencySnapshot, or the SystemSettings row.
    """
    if ctx is None or not ctx.secondary_currency_enabled:
        return None
    rate = ctx.secondary_exchange_rate
    if rate is None or rate <= 0:
        return None
    rule = getattr(ctx, 'conversion_rule', CONVERSION_RULE_V1)
    if rule != CONVERSION_RULE_V1:
        raise ValueError(f'Unknown conversion rule: {rule!r}')
    quantum = Decimal(10) ** -int(ctx.secondary_decimal_places)
    return (Decimal(str(amount)) * Decimal(str(rate))).quantize(quantum, rounding=ROUND_HALF_UP)


# ── Orders ────────────────────────────────────────────────────────────────────

BASIS_LIVE = 'live'
BASIS_RECORDED = 'recorded'


@dataclass(frozen=True)
class OrderAmounts:
    """
    An order's money in both currencies.

    `currency` is the configuration the order's primary figures and its paid
    portion are expressed in: the latest confirmed payment's snapshot once any
    payment is confirmed, else the live configuration. Outstanding figures are
    always live, because an unpaid balance is settled at the rate of the day
    it is paid.

    Each *_secondary value is None when it cannot be stated honestly: no
    secondary currency in effect, a payment recorded while the secondary
    currency was disabled, or payments recorded in different secondary
    currencies (for example across a redenomination).
    """
    basis: str
    currency: CurrencyContext
    live: CurrencyContext
    total: Decimal
    paid: Decimal
    outstanding: Decimal
    paid_secondary: Optional[Decimal]
    outstanding_secondary: Optional[Decimal]
    total_secondary: Optional[Decimal]


def _snapshot_context(snapshot) -> CurrencyContext:
    return snapshot.as_context() if hasattr(snapshot, 'as_context') else snapshot


def order_amounts(order, live: Optional[CurrencyContext] = None) -> OrderAmounts:
    """
    Derive an order's primary and secondary figures.

    Reads `order.payments.all()`, so callers rendering many orders should
    prefetch `payments` with `select_related('currency_snapshot')` — the
    prefetch cache is used when present. Only confirmed payments count as
    paid, matching SalesOrder.amount_paid.
    """
    from core.models import Payment

    live = live or current_context()
    confirmed = [p for p in order.payments.all() if p.status == Payment.CONFIRMED]

    total = Decimal(str(order.total_amount or 0))
    paid = sum((p.amount for p in confirmed), Decimal('0.00'))
    outstanding = total - paid

    if confirmed:
        latest = max(confirmed, key=lambda p: (p.created_at, p.pk))
        currency = _snapshot_context(latest.currency_snapshot)
        basis = BASIS_RECORDED

        paid_secondary = Decimal('0')
        secondary_codes = set()
        for payment in confirmed:
            converted = secondary_amount(payment.amount, payment.currency_snapshot)
            if converted is None:
                paid_secondary = None
                break
            secondary_codes.add(payment.currency_snapshot.secondary_currency_code)
            paid_secondary += converted
        if paid_secondary is not None and len(secondary_codes) > 1:
            paid_secondary = None
    else:
        currency = live
        basis = BASIS_LIVE
        secondary_codes = {live.secondary_currency_code}
        paid_secondary = (
            Decimal('0') if live.secondary_currency_enabled else None
        )

    outstanding_secondary = secondary_amount(outstanding, live)

    if outstanding == 0:
        # Fully settled: the total is exactly what was paid, whatever the live
        # configuration says today.
        total_secondary = paid_secondary
    elif (
        paid_secondary is not None
        and outstanding_secondary is not None
        and secondary_codes <= {live.secondary_currency_code}
    ):
        total_secondary = paid_secondary + outstanding_secondary
    else:
        total_secondary = None

    return OrderAmounts(
        basis=basis,
        currency=currency,
        live=live,
        total=total,
        paid=paid,
        outstanding=outstanding,
        paid_secondary=paid_secondary,
        outstanding_secondary=outstanding_secondary,
        total_secondary=total_secondary,
    )


ORDER_AMOUNTS_ATTR = '_order_amounts'


def attach_order_amounts(orders, live: Optional[CurrencyContext] = None) -> list:
    """
    Compute `order_amounts()` for many orders with a single settings read.

    Stores each result on the order as `_order_amounts`, which the
    `order_money` template filter and the order API serializer both reuse.
    Returns a list, so pass it back to the template in place of the queryset
    — iterating the original queryset again would query again.
    """
    live = live or current_context()
    orders = list(orders)
    for order in orders:
        setattr(order, ORDER_AMOUNTS_ATTR, order_amounts(order, live))
    return orders
