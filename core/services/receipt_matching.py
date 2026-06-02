from decimal import Decimal, InvalidOperation
import re
import unicodedata

from django.utils.dateparse import parse_date, parse_datetime

from .vepay import (
    ORIGIN_ACCOUNT_PATH,
    ORIGIN_BANK_PATH,
    PAYMENT_AMOUNT_VALUE_PATH,
    PAYMENT_BANK_APP_PATH,
    PAYMENT_DATETIME_ISO_PATH,
    PAYMENT_REFERENCE_PATH,
    get_receipt_value,
)


MONEY_QUANT = Decimal('0.01')
PAYMENT_DATE_ISO_PATH = ('payment', 'date', 'iso')
PAYMENT_AMOUNT_CURRENCY_PATHS = (
    ('payment', 'amount', 'currency'),
    ('payment', 'currency'),
    ('currency',),
)
REQUIRED_FIELD_KEYS = ('amount_usd', 'reference', 'paid_on', 'origin_bank')

BANK_ALIASES = {
    'BDV': 'BDV',
    'BANCO DE VENEZUELA': 'BDV',
    'BCO DE VENEZUELA': 'BDV',
    'VENEZUELA': 'BDV',
    'PAGOMOVILBDV PERSONAS': 'BDV',
    'PAGOMOVIL BDV PERSONAS': 'BDV',
    'BANCAMIGA': 'BANCAMIGA',
    'BANESCO': 'BANESCO',
    'MERCANTIL': 'MERCANTIL',
    'BANCO MERCANTIL': 'MERCANTIL',
    'BBVA PROVINCIAL': 'BBVA PROVINCIAL',
    'BANCO PROVINCIAL': 'BBVA PROVINCIAL',
    'PROVINCIAL': 'BBVA PROVINCIAL',
}

BANK_CODE_PREFIXES = {
    '0102': 'BDV',
    '0134': 'Banesco',
    '0105': 'Mercantil',
    '0108': 'BBVA Provincial',
    '0172': 'Bancamiga',
}


def compare_receipt_fields(receipt_data, expected_fields, settings, field_keys=None):
    """
    Compare normalized VEPay receipt fields against expected kiosk form values.

    Only keys present in ``field_keys`` are compared. Missing OCR data is a
    mismatch because the system cannot prove the uploaded receipt matches.
    """
    field_keys = tuple(field_keys or expected_fields.keys())
    receipt_fields = extract_receipt_fields(receipt_data, settings)
    expected = normalize_expected_fields(expected_fields)

    field_matches = {}
    mismatches = {}
    for key in field_keys:
        expected_value = expected.get(key)
        receipt_value = receipt_fields.get(key)
        if key == 'origin_bank':
            expected_compare = normalize_bank(expected_value)
            receipt_compare = normalize_bank(receipt_value)
        elif key == 'reference':
            expected_compare = normalize_reference(expected_value)
            receipt_compare = normalize_reference(receipt_value)
        else:
            expected_compare = expected_value
            receipt_compare = receipt_value

        matched = bool(expected_compare) and bool(receipt_compare) and expected_compare == receipt_compare
        field_matches[key] = matched
        if not matched:
            mismatches[key] = {
                'expected': _display_value(expected_value),
                'actual': _display_value(receipt_value),
                'code': (
                    'missing_receipt_field'
                    if not receipt_compare else 'receipt_field_mismatch'
                ),
            }

    return {
        'matches': all(field_matches.values()) if field_matches else True,
        'field_matches': field_matches,
        'receipt_fields': _stringify_fields(receipt_fields),
        'expected_fields': _stringify_fields(expected),
        'mismatches': mismatches,
    }


def extract_receipt_fields(receipt_data, settings):
    amount = receipt_amount_usd(receipt_data, settings)
    reference = get_receipt_value(receipt_data, PAYMENT_REFERENCE_PATH, '') or ''
    paid_on = receipt_paid_on(receipt_data)
    origin_bank = receipt_issuing_bank(receipt_data)

    return {
        'amount_usd': _money(amount) if amount is not None else None,
        'reference': str(reference).strip(),
        'paid_on': paid_on,
        'origin_bank': str(origin_bank).strip(),
    }


def receipt_issuing_bank(receipt_data):
    account_bank = bank_from_account(get_receipt_value(receipt_data, ORIGIN_ACCOUNT_PATH, ''))
    if account_bank:
        return account_bank

    bank_app = get_receipt_value(receipt_data, PAYMENT_BANK_APP_PATH, '') or ''
    if bank_app:
        return str(bank_app).strip()

    return str(get_receipt_value(receipt_data, ORIGIN_BANK_PATH, '') or '').strip()


def bank_from_account(account):
    text = str(account or '').strip()
    match = re.match(r'^\D*(\d{4})', text)
    if not match:
        return ''
    return BANK_CODE_PREFIXES.get(match.group(1), '')


def normalize_expected_fields(fields):
    return {
        'amount_usd': _money(fields.get('amount_usd')) if fields.get('amount_usd') not in (None, '') else None,
        'reference': str(fields.get('reference') or '').strip(),
        'paid_on': normalize_date(fields.get('paid_on')),
        'origin_bank': str(fields.get('origin_bank') or '').strip(),
    }


def receipt_amount_usd(receipt_data, settings):
    raw_value = get_receipt_value(receipt_data, PAYMENT_AMOUNT_VALUE_PATH)
    if raw_value in (None, ''):
        return None
    try:
        amount = Decimal(str(raw_value))
    except (InvalidOperation, ValueError, TypeError):
        return None

    currency = receipt_currency(receipt_data)
    primary_code = (settings.currency_code or '').upper()
    secondary_code = (settings.secondary_currency_code or '').upper()
    if (
        settings.secondary_currency_enabled
        and settings.secondary_exchange_rate
        and settings.secondary_exchange_rate > 0
    ):
        if not currency or currency == secondary_code:
            return amount / Decimal(str(settings.secondary_exchange_rate))
        if currency == primary_code:
            return amount

    return amount


def receipt_currency(receipt_data):
    for path in PAYMENT_AMOUNT_CURRENCY_PATHS:
        value = get_receipt_value(receipt_data, path)
        if value:
            return str(value).strip().upper()
    return ''


def receipt_paid_on(receipt_data):
    raw = (
        get_receipt_value(receipt_data, PAYMENT_DATETIME_ISO_PATH)
        or get_receipt_value(receipt_data, PAYMENT_DATE_ISO_PATH)
        or ''
    )
    return normalize_date(raw)


def normalize_date(value):
    if value in (None, ''):
        return ''
    raw = value.isoformat() if hasattr(value, 'isoformat') else str(value).strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}', raw):
        return raw[:10]

    parsed_date = parse_date(raw)
    if parsed_date:
        return parsed_date.isoformat()

    parsed_datetime = parse_datetime(raw)
    if parsed_datetime:
        return parsed_datetime.date().isoformat()

    return ''


def normalize_reference(value):
    return re.sub(r'[^0-9A-Za-z]+', '', str(value or '')).upper()


def normalize_bank(value):
    raw = _ascii_upper(value)
    if not raw:
        return ''
    compact = re.sub(r'[^0-9A-Z]+', ' ', raw).strip()
    return BANK_ALIASES.get(compact, compact)


def _ascii_upper(value):
    text = str(value or '').strip()
    if not text:
        return ''
    normalized = unicodedata.normalize('NFKD', text)
    return ''.join(ch for ch in normalized if not unicodedata.combining(ch)).upper()


def _money(value):
    try:
        return Decimal(str(value)).quantize(MONEY_QUANT)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _display_value(value):
    if value is None:
        return ''
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _stringify_fields(fields):
    return {key: _display_value(value) for key, value in fields.items()}
