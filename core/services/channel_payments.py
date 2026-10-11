"""
Recording a receipt payment for a channel order.

The receipt is validated against the order's *frozen* quote, never against
today's rate. A customer who was told to pay Bs 1,058.50 and did so must not
be refused because the BCV rate moved in between, so the receipt is compared
through the currency snapshot taken when the order was created, and the payment
is recorded with that same snapshot.

Like the kiosk, the slow part runs first and holds no lock: the VEPay call is a
third-party request that can take a minute. The write block then locks the
order, re-checks everything the validation assumed, and records the payment
through the single path that decides what a payment does to its order.

A channel is not a provisioned device, so the policy is CHANNEL_POLICY: only
VEPay's own reading of the image counts, never a transaction key or OCR data
the request claims, and an unavailable OCR leaves the payment for a person to
review instead of failing the customer.
"""
from django.db import transaction
from django.utils import timezone

from core.models import OcrCallLog, Payment, SalesOrder, SystemSettings
from core.services.channel_orders import ChannelOrderError
from core.services.payments import (
    OrderNotPayableError,
    apply_confirmed_payments,
    lock_payable_order,
)
from core.services.receipt_validation import (
    CHANNEL_POLICY,
    assert_receipt_total_unchanged,
    assert_transaction_key_unused,
    receipt_paid_date,
    validate_receipt,
)
from core.services.vepay import RECIPIENT_ACCOUNT_PATH, RECIPIENT_BANK_PATH, get_receipt_value

# The only fields of the request that reach validation. The amount is never
# among them: what is owed is the order's, not the caller's.
RECEIPT_FIELDS = (
    'reference', 'paid_on', 'origin_bank', 'origin_phone',
    'receipt_image_base64', 'receipt_image_content_type',
)


class PaymentUnderReview(ChannelOrderError):
    """A payment for this order is already waiting for a person."""
    code = 'payment_under_review'

    def __init__(self, order):
        super().__init__(
            f'Order {order.order_number} already has a payment under review; '
            f'wait for it to be settled before sending another.'
        )


def _assert_no_pending_review(order):
    if order.payments.filter(status=Payment.PENDING_REVIEW).exists():
        raise PaymentUnderReview(order)


def register_channel_payment(channel_order, *, payment_method, receipt):
    """
    Validate a receipt and record the payment for a channel order.

    `channel_order` is loaded by the caller and must belong to the channel the
    request authenticated as. Returns the Payment: confirmed (and the order
    paid) when VEPay read the receipt and it matched, pending review when OCR
    could not decide.

    Raises OrderNotPayableError, PaymentUnderReview, and the receipt errors
    (InvalidReceiptImageError, ReceiptValidationError, AmountMismatchError,
    DuplicateTransactionError). A refusal records no payment.
    """
    channel = channel_order.channel
    receipt = {key: receipt[key] for key in RECEIPT_FIELDS if key in receipt}

    # Refuse early what is already known, before an OCR call that costs money.
    # The caller's copy of the order may be stale, so read it again.
    order = SalesOrder.objects.get(pk=channel_order.sales_order_id)
    if order.status != SalesOrder.CONFIRMED:
        raise OrderNotPayableError(order, {SalesOrder.CONFIRMED})
    _assert_no_pending_review(order)

    outstanding = order.amount_outstanding
    settings = SystemSettings.get()
    ocr_report = {}
    try:
        fields = validate_receipt(
            receipt,
            payment_method,
            outstanding,
            settings=settings,
            currency=channel_order.currency_snapshot.as_context(),
            policy=CHANNEL_POLICY,
            ocr_report=ocr_report,
        )
    finally:
        if ocr_report:
            OcrCallLog.objects.create(channel=channel, sales_order=order, **ocr_report)

    now = timezone.now()
    with transaction.atomic():
        locked = lock_payable_order(order.pk, allowed={SalesOrder.CONFIRMED})
        _assert_no_pending_review(locked)
        outstanding_now = locked.amount_outstanding
        # The receipt was accepted against `outstanding`; if it moved while OCR
        # ran, the receipt no longer proves payment of what is owed.
        assert_receipt_total_unchanged(fields, outstanding_now)
        # Settles a race between two live submissions of the same receipt.
        assert_transaction_key_unused(fields['transaction_key'])

        payment = Payment(
            sales_order=locked,
            amount=outstanding_now,
            payment_method=payment_method,
            status=fields['status'],
            reference_number=str(receipt.get('reference') or '').strip(),
            recorded_by=channel.service_user,
            notes=_notes(channel_order, receipt, fields['status']),
            transaction_key=fields['transaction_key'],
            origin_phone=str(receipt.get('origin_phone') or '').strip(),
            origin_bank=str(receipt.get('origin_bank') or '').strip(),
            recipient_bank=str(
                get_receipt_value(fields['ocr_receipt_data'], RECIPIENT_BANK_PATH, '') or ''
            ).strip(),
            recipient_account=str(
                get_receipt_value(fields['ocr_receipt_data'], RECIPIENT_ACCOUNT_PATH, '') or ''
            ).strip(),
            ocr_receipt_data=fields['ocr_receipt_data'],
            verified_at=now if fields['ocr_receipt_data'] and fields['transaction_key'] else None,
            # The quote the customer was given, not today's rate.
            currency_snapshot=channel_order.currency_snapshot,
        )
        if fields['image'] is not None:
            name, content = fields['image']
            payment.receipt_image.save(name, content, save=False)
        payment.save()

        apply_confirmed_payments(locked)
    return payment


def _notes(channel_order, receipt, status):
    notes = [
        f'Online payment - {channel_order.channel.name}',
        f'Client reference: {channel_order.client_reference}',
    ]
    if status == Payment.PENDING_REVIEW:
        notes.append('Receipt pending manual review.')
    paid_on = receipt_paid_date(receipt)
    if paid_on:
        notes.append(f'Paid date: {paid_on}')
    return '\n'.join(notes)
