"""
Give every existing payment a currency snapshot.

Payments recorded before snapshots existed never stored the rate they were
made at, and it cannot be recovered. They are assigned the configuration in
effect when this migration runs — the current currency identity and the
current exchange rate — marked `rate_source='backfilled'` so the figure is
shown as approximate rather than as a recorded rate.

No payment is deleted or altered beyond this one column. A fresh database, or
one with no payments, gets no snapshot row at all.
"""
import hashlib
import json
import unicodedata
from datetime import timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

# Mirrors CurrencyContext.build() and canonical() in core/services/currency.py
# at fingerprint version v1, inlined because migrations must not depend on
# application code that can change. core/tests/test_currency_snapshots.py
# asserts the two still agree.

RATE_QUANTUM = Decimal('0.00000001')
RATE_SOURCE_BACKFILLED = 'backfilled'
CONVERSION_RULE_V1 = 'half_up_v1'

# SystemSettings field defaults, for a database that has payments but no
# settings row.
SETTINGS_DEFAULTS = {
    'currency_code': 'USD',
    'currency_symbol': '$',
    'decimal_places': 2,
    'secondary_currency_enabled': False,
    'secondary_currency_code': '',
    'secondary_currency_symbol': '',
    'secondary_decimal_places': 2,
    'secondary_exchange_rate': Decimal('1'),
}


def backfilled_fields(settings_row):
    def read(name):
        if settings_row is None:
            return SETTINGS_DEFAULTS[name]
        return getattr(settings_row, name)

    return {
        'currency_code': (read('currency_code') or '').strip().upper(),
        'currency_symbol': unicodedata.normalize('NFC', read('currency_symbol') or ''),
        'decimal_places': int(read('decimal_places')),
        'secondary_currency_enabled': bool(read('secondary_currency_enabled')),
        'secondary_currency_code': (read('secondary_currency_code') or '').strip().upper(),
        'secondary_currency_symbol': unicodedata.normalize(
            'NFC', read('secondary_currency_symbol') or ''
        ),
        'secondary_decimal_places': int(read('secondary_decimal_places')),
        'secondary_exchange_rate': Decimal(str(read('secondary_exchange_rate'))).quantize(
            RATE_QUANTUM, rounding=ROUND_HALF_UP
        ),
        'rate_as_of': None,
        'rate_source': RATE_SOURCE_BACKFILLED,
        'conversion_rule': CONVERSION_RULE_V1,
    }


def fingerprint_v1(fields):
    rate_as_of = fields['rate_as_of']
    canonical = [
        'v1',
        fields['currency_code'],
        fields['currency_symbol'],
        fields['decimal_places'],
        fields['secondary_currency_enabled'],
        fields['secondary_currency_code'],
        fields['secondary_currency_symbol'],
        fields['secondary_decimal_places'],
        format(fields['secondary_exchange_rate'], 'f'),
        (
            rate_as_of.astimezone(dt_timezone.utc).isoformat(timespec='microseconds')
            if rate_as_of is not None else None
        ),
        fields['rate_source'],
        fields['conversion_rule'],
    ]
    payload = json.dumps(canonical, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def backfill(apps, schema_editor):
    Payment = apps.get_model('core', 'Payment')
    unassigned = Payment.objects.filter(currency_snapshot__isnull=True)
    if not unassigned.exists():
        return

    SystemSettings = apps.get_model('core', 'SystemSettings')
    CurrencySnapshot = apps.get_model('core', 'CurrencySnapshot')

    fields = backfilled_fields(SystemSettings.objects.filter(pk=1).first())
    snapshot, _ = CurrencySnapshot.objects.get_or_create(
        fingerprint=fingerprint_v1(fields),
        defaults=fields,
    )
    unassigned.update(currency_snapshot=snapshot)


def unbackfill(apps, schema_editor):
    Payment = apps.get_model('core', 'Payment')
    CurrencySnapshot = apps.get_model('core', 'CurrencySnapshot')

    backfilled = CurrencySnapshot.objects.filter(rate_source=RATE_SOURCE_BACKFILLED)
    Payment.objects.filter(currency_snapshot__in=backfilled).update(currency_snapshot=None)
    backfilled.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_currency_snapshot'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
