"""
Validating a payment receipt against an order before the payment is recorded.

This is the fraud-control path for receipt payments (mobile payment and bank
transfer): decode the image, read it through VEPay OCR, compare what the
receipt says with what the order expects, check the money went to one of the
store's own accounts, and refuse a receipt that was already spent.

It used to live inside the kiosk checkout. It is shared now because any channel
that accepts receipts must run the same checks; a second copy would drift.

How much the caller is trusted is decided by a ReceiptPolicy:

* A kiosk is a provisioned device in the store. KIOSK_POLICY keeps the
  behaviour the kiosk has always had, including accepting a transaction key
  and OCR data the kiosk app sends itself.
* An internet channel is not. CHANNEL_POLICY never takes the transaction key or
  OCR data from the request; only VEPay's own reading counts. And when OCR is
  unavailable it degrades to a payment pending staff review instead of
  failing, because a human reviews those anyway.

Errors carry a `code` and the HTTP status a view should answer with, so each
view keeps its own response shape.
"""
import base64
import binascii
import uuid
from dataclasses import dataclass
from time import perf_counter
from decimal import Decimal, InvalidOperation

from asgiref.sync import async_to_sync
from django.core.files.base import ContentFile
from django.utils.dateparse import parse_date, parse_datetime

from core.models import Payment, RecipientProfile, SystemSettings
from core.services.receipt_matching import (
    REQUIRED_FIELD_KEYS,
    compare_receipt_fields,
    match_recipient_profile,
)
from core.services.vepay import TRANSACTION_KEY_PATH, VEPayClient, VEPayError, get_receipt_value

RECEIPT_PAYMENT_METHODS = {Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER}
ALLOWED_RECEIPT_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/heic', 'image/heif'}
MONEY_QUANT = Decimal('0.01')

# HTTP statuses, as plain numbers so this module does not depend on DRF.
HTTP_400_BAD_REQUEST = 400
HTTP_409_CONFLICT = 409
HTTP_413_REQUEST_ENTITY_TOO_LARGE = 413
HTTP_415_UNSUPPORTED_MEDIA_TYPE = 415
HTTP_422_UNPROCESSABLE_ENTITY = 422
HTTP_503_SERVICE_UNAVAILABLE = 503


@dataclass(frozen=True)
class ReceiptPolicy:
    # Accept a transaction key and OCR data supplied in the request when OCR
    # did not run. Safe only for a provisioned device.
    trust_client_evidence: bool
    # Treat "OCR is off" and retryable VEPay failures as "pending review"
    # rather than an error.
    degrade_unavailable_ocr_to_review: bool
    # Demand an image for receipt methods even if SystemSettings does not.
    require_receipt_image: bool
    # Filename prefix VEPay sees; the stored path is rebuilt by upload_to.
    image_name_prefix: str


KIOSK_POLICY = ReceiptPolicy(
    trust_client_evidence=True,
    degrade_unavailable_ocr_to_review=False,
    require_receipt_image=False,
    image_name_prefix='kiosk-receipt',
)

CHANNEL_POLICY = ReceiptPolicy(
    trust_client_evidence=False,
    degrade_unavailable_ocr_to_review=True,
    require_receipt_image=True,
    image_name_prefix='channel-receipt',
)


class DuplicateTransactionError(Exception):
    def __init__(self, transaction_key):
        self.transaction_key = transaction_key
        super().__init__('Duplicate transaction')


class AmountMismatchError(Exception):
    def __init__(self, receipt_amount, order_total):
        self.receipt_amount = receipt_amount
        self.order_total = order_total
        super().__init__('Amount mismatch')


class InvalidReceiptImageError(Exception):
    def __init__(self, message, code, http_status):
        self.code = code
        self.http_status = http_status
        super().__init__(message)


class ReceiptValidationError(Exception):
    def __init__(self, message, code, http_status, details=None):
        self.code = code
        self.http_status = http_status
        self.details = details or {}
        super().__init__(message)


def assert_transaction_key_unused(transaction_key):
    if transaction_key and Payment.objects.filter(transaction_key=transaction_key).exists():
        raise DuplicateTransactionError(transaction_key)


def assert_receipt_total_unchanged(receipt_fields, order_total):
    """Reject an order whose total drifted away from the validated receipt."""
    validated_total = receipt_fields['validated_total']
    if validated_total is None:  # nothing was ever checked against a total
        return
    order_total = Decimal(str(order_total)).quantize(MONEY_QUANT)
    if validated_total != order_total:
        raise AmountMismatchError(validated_total, order_total)


def validate_receipt(receipt_data, payment_method, order_total, *, settings, currency,
                     policy=KIOSK_POLICY, ocr_report=None):
    """
    Validate receipt metadata against an order total and map it onto Payment
    fields.

    Call it before opening the write transaction: the OCR step is a
    third-party HTTP call (one retry against a configurable timeout) and must
    not run while database locks are held. The returned `validated_total`
    records the total the receipt was accepted against, so the write block can
    reject the order if that total moved.

    `settings` supplies the OCR and image rules; `currency` supplies the
    exchange rate a secondary-currency receipt is converted at, and is the
    context the caller records on the payment.

    `ocr_report`, if given, is a dict filled in when a VEPay call is actually
    made: `status` ('ok' or the VEPay error code), `latency_ms` and
    `bytes_sent`. It stays empty when OCR did not run, and it is filled even
    when this function goes on to raise, so a caller can record what the call
    cost.
    """
    if not isinstance(receipt_data, dict):
        receipt_data = {}

    order_total = Decimal(str(order_total)).quantize(MONEY_QUANT)
    is_receipt_method = payment_method in RECEIPT_PAYMENT_METHODS
    image_present = receipt_image_present(receipt_data)
    image_required = (
        settings.receipt_image_required_for_receipt_methods or policy.require_receipt_image
    )
    if is_receipt_method and image_required and not image_present:
        raise InvalidReceiptImageError(
            'Receipt image is required for this payment method.',
            'receipt_image_required',
            HTTP_400_BAD_REQUEST,
        )

    image_data = (
        decode_receipt_image_data(receipt_data, settings, name_prefix=policy.image_name_prefix)
        if image_present else None
    )
    receipt_match_required = (
        is_receipt_method and
        (image_required or image_data is not None)
    )
    vepay_data = None
    transaction_key = (
        (receipt_data.get('transaction_key') or '').strip()
        if policy.trust_client_evidence else ''
    )
    if receipt_match_required:
        if image_data is None:
            raise InvalidReceiptImageError(
                'Receipt image is required for this payment method.',
                'receipt_image_required',
                HTTP_400_BAD_REQUEST,
            )
        vepay_data = _read_receipt(image_data, payment_method, settings, policy, ocr_report)

        if vepay_data is not None:
            comparison = compare_receipt_fields(
                vepay_data,
                expected_receipt_fields(receipt_data, order_total),
                currency,
                REQUIRED_FIELD_KEYS,
            )
            if not comparison['matches']:
                raise ReceiptValidationError(
                    'Receipt fields do not match the submitted payment details.',
                    'receipt_field_mismatch',
                    HTTP_422_UNPROCESSABLE_ENTITY,
                    details={
                        'field_matches': comparison['field_matches'],
                        'receipt_fields': comparison['receipt_fields'],
                        'expected_fields': comparison['expected_fields'],
                        'mismatches': comparison['mismatches'],
                    },
                )

            if settings.recipient_validation_enabled:
                profiles = RecipientProfile.objects.filter(
                    is_active=True, payment_method=payment_method,
                )
                recipient_match = match_recipient_profile(vepay_data, payment_method, profiles)
                if not recipient_match['matched']:
                    raise ReceiptValidationError(
                        'Receipt recipient details do not match any known-good account.',
                        'recipient_mismatch',
                        HTTP_422_UNPROCESSABLE_ENTITY,
                        details={
                            'receipt_fields': recipient_match['receipt_fields'],
                            'checked_profiles_count': recipient_match['checked_profiles_count'],
                        },
                    )

            transaction_key = (
                get_receipt_value(vepay_data, TRANSACTION_KEY_PATH, '') or transaction_key
            ).strip()

    # Fails fast on a receipt already spent, before the order exists; the write
    # block repeats the check to settle a race between two live checkouts.
    assert_transaction_key_unused(transaction_key)

    # The OCR comparison above checks the receipt amount against order_total,
    # so a match there validates the total too.
    validated_total = order_total if vepay_data is not None else None
    receipt_amount = receipt_data.get('amount_usd')
    if receipt_amount not in (None, ''):
        try:
            receipt_total = Decimal(str(receipt_amount)).quantize(MONEY_QUANT)
        except (InvalidOperation, ValueError, TypeError):
            raise AmountMismatchError(receipt_amount, order_total)
        if receipt_total != order_total:
            raise AmountMismatchError(receipt_total, order_total)
        validated_total = order_total

    client_ocr_data = (
        receipt_data.get('ocr_receipt_data') if policy.trust_client_evidence else None
    )
    ocr_receipt_data = vepay_data or client_ocr_data or None
    status_value = Payment.CONFIRMED
    if is_receipt_method and not transaction_key:
        status_value = Payment.PENDING_REVIEW

    return {
        'status': status_value,
        'transaction_key': transaction_key,
        'ocr_receipt_data': ocr_receipt_data,
        'validated_total': validated_total,
        'image': receipt_content_file(image_data),
    }


def _read_receipt(image_data, payment_method, settings, policy, ocr_report=None):
    """
    Read the receipt through VEPay, or decide OCR cannot run.

    Returns the VEPay reading, or None when the policy degrades an unavailable
    OCR to manual review. Raises otherwise.
    """
    if not settings.ocr_enabled:
        if policy.degrade_unavailable_ocr_to_review:
            return None
        raise ReceiptValidationError(
            'Receipt OCR validation is disabled. Ask an associate for assistance.',
            'ocr_disabled',
            HTTP_409_CONFLICT,
        )
    enabled_methods = settings.ocr_enabled_methods or []
    if payment_method not in enabled_methods:
        if policy.degrade_unavailable_ocr_to_review:
            return None
        raise ReceiptValidationError(
            'OCR receipt verification is not enabled for this payment method.',
            'ocr_method_disabled',
            HTTP_422_UNPROCESSABLE_ENTITY,
        )

    started = perf_counter()
    try:
        reading = async_to_sync(VEPayClient().parse_receipt)(
            image_data['raw'],
            image_data['name'],
            image_data['content_type'],
        )
        _report_ocr(ocr_report, 'ok', image_data, started)
        return reading
    except VEPayError as exc:
        _report_ocr(ocr_report, exc.code, image_data, started)
        # A retryable failure is VEPay being unavailable, not the receipt
        # being bad, so it can wait for a person. A non-retryable one means
        # the image itself could not be read and the customer should retake it.
        if exc.is_retryable and policy.degrade_unavailable_ocr_to_review:
            return None
        raise ReceiptValidationError(
            exc.message,
            exc.code,
            (
                HTTP_503_SERVICE_UNAVAILABLE
                if exc.is_retryable else HTTP_422_UNPROCESSABLE_ENTITY
            ),
            details={'retryable': exc.is_retryable},
        )


def _report_ocr(report, status, image_data, started):
    if report is not None:
        report.update(
            status=status,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            bytes_sent=len(image_data['raw']),
        )


def expected_receipt_fields(receipt_data, order_total):
    return {
        'amount_usd': order_total,
        'reference': receipt_data.get('reference'),
        'paid_on': receipt_data.get('paid_on') or receipt_data.get('paid_at'),
        'origin_bank': receipt_data.get('origin_bank'),
    }


def receipt_image_present(receipt_data):
    data_url = (receipt_data.get('receipt_image_base64') or '').strip()
    if not data_url:
        return False
    if data_url.startswith('data:') and ',' in data_url:
        return bool(data_url.split(',', 1)[1].strip())
    return True


def decode_receipt_image_data(receipt_data, settings=None, *, name_prefix='kiosk-receipt'):
    data_url = receipt_data.get('receipt_image_base64') or ''
    if not data_url:
        return None

    content_type = (receipt_data.get('receipt_image_content_type') or '').lower()
    payload = data_url
    if data_url.startswith('data:') and ',' in data_url:
        header, payload = data_url.split(',', 1)
        content_type = header[5:].split(';', 1)[0].lower()
    payload = payload.strip()

    if content_type not in ALLOWED_RECEIPT_IMAGE_TYPES:
        raise InvalidReceiptImageError(
            'Receipt image must be a JPEG, PNG, HEIC, or HEIF file.',
            'unsupported_receipt_type',
            HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidReceiptImageError(
            'Receipt image could not be decoded.',
            'invalid_receipt_image',
            HTTP_400_BAD_REQUEST,
        )
    if not raw:
        raise InvalidReceiptImageError(
            'Receipt image could not be decoded.',
            'invalid_receipt_image',
            HTTP_400_BAD_REQUEST,
        )

    settings = settings or SystemSettings.get()
    max_bytes = settings.ocr_max_file_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise InvalidReceiptImageError(
            f'Receipt image must be {settings.ocr_max_file_mb} MB or smaller.',
            'receipt_too_large',
            HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    ext = {
        'image/jpeg': 'jpg',
        'image/png': 'png',
        'image/heic': 'heic',
        'image/heif': 'heif',
    }.get(content_type, 'jpg')
    # Decoding happens before the order exists, so the name carries no order
    # number — nor does it need to: the stored path is rebuilt from the order
    # by Payment.receipt_image's upload_to (core.models.receipt_image_upload_path),
    # and this name only supplies the extension plus the filename VEPay sees.
    name = f'{name_prefix}-{uuid.uuid4().hex}.{ext}'
    return {
        'name': name,
        'raw': raw,
        'content_type': content_type,
    }


def receipt_content_file(image_data):
    if image_data is None:
        return None
    return image_data['name'], ContentFile(image_data['raw'])


def receipt_paid_date(receipt_data):
    raw = (receipt_data.get('paid_on') or receipt_data.get('paid_at') or '').strip()
    if not raw:
        return ''

    value = parse_date(raw)
    if value:
        return value.isoformat()

    value = parse_datetime(raw)
    if value:
        return value.date().isoformat()

    if len(raw) >= 10:
        value = parse_date(raw[:10])
        if value:
            return value.isoformat()

    return ''
