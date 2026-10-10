"""
What a payment does to its order, in one place.

An order used to become "paid" in three places, each with its own copy of the
rule: the payments API, the back-office payment form, and the kiosk (which
only ever sets paid_at). The back-office copy checked no order status, so a
payment could revive a cancelled order, and it re-stamped paid_at on every
extra payment, moving revenue between months. The API copy locked the payment
rows rather than the order, so two partial payments arriving together could
each miss the other and leave a fully paid order unpaid.

Every path that records or approves a payment now goes through here. Each one
locks the order row first, always in that order, so concurrent payments on
one order are serialised and the later one sees the earlier one.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.services.receipt_validation import DuplicateTransactionError


class OrderNotPayableError(Exception):
    """The order is in a status that cannot take this payment."""
    code = 'order_not_payable'

    def __init__(self, order, allowed):
        self.order = order
        self.allowed = allowed
        super().__init__(
            f'Order {order.order_number} is {order.get_status_display()}; '
            f'this payment can only be applied to an order that is '
            f'{_status_list(allowed)}.'
        )


class PaymentReviewError(Exception):
    """A review was asked of a payment that cannot take it."""

    def __init__(self, message, code):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PaymentApplication:
    total_paid: Decimal
    became_paid: bool


def _status_list(statuses):
    from core.models import SalesOrder
    labels = dict(SalesOrder.STATUS_CHOICES)
    return ' or '.join(str(labels[status]) for status in sorted(statuses))


def recordable_statuses():
    """Order statuses a new payment may be recorded against."""
    from core.models import SalesOrder
    return frozenset({SalesOrder.CONFIRMED, SalesOrder.PAID})


def approvable_statuses():
    """
    Order statuses a pending payment may be approved for.

    Wider than recordable_statuses(): a kiosk order is DELIVERED the moment the
    customer leaves, whether or not its receipt still awaits review, and an
    order can be shipped before a late payment is cleared.
    """
    from core.models import SalesOrder
    return frozenset({
        SalesOrder.CONFIRMED, SalesOrder.PAID, SalesOrder.SHIPPED, SalesOrder.DELIVERED,
    })


def lock_order(order_id):
    """Lock and return the order row. Must run inside a transaction."""
    from core.models import SalesOrder
    return SalesOrder.objects.select_for_update().get(pk=order_id)


def lock_payable_order(order_id, allowed):
    """Lock the order and refuse it unless its status is in `allowed`."""
    order = lock_order(order_id)
    if order.status not in allowed:
        raise OrderNotPayableError(order, allowed)
    return order


def apply_confirmed_payments(order, *, at=None):
    """
    Bring the order's paid state in line with its confirmed payments.

    * CONFIRMED and covered: becomes PAID, with paid_at.
    * SHIPPED or DELIVERED, covered, without paid_at: gets paid_at only. This
      is how an approved kiosk payment is recognised as revenue without moving
      an order that has already left the store.
    * PAID: unchanged. A further payment never re-stamps paid_at.

    `order` is updated in place to match the stored row.
    """
    from core.models import Payment, SalesOrder

    with transaction.atomic():
        locked = lock_order(order.pk)
        total_paid = (
            Payment.objects
            .filter(sales_order_id=locked.pk, status=Payment.CONFIRMED)
            .aggregate(total=Sum('amount'))['total']
        ) or Decimal('0.00')

        became_paid = False
        if total_paid >= locked.total_amount:
            stamp = at or timezone.now()
            if locked.status == SalesOrder.CONFIRMED:
                locked.status = SalesOrder.PAID
                locked.paid_at = stamp
                locked.save(update_fields=['status', 'paid_at', 'updated_at'])
                became_paid = True
            elif (
                locked.status in (SalesOrder.SHIPPED, SalesOrder.DELIVERED)
                and locked.paid_at is None
            ):
                locked.paid_at = stamp
                locked.save(update_fields=['paid_at', 'updated_at'])

        order.status = locked.status
        order.paid_at = locked.paid_at
        return PaymentApplication(total_paid=total_paid, became_paid=became_paid)


def approve_payment(payment, *, user, notes='', transaction_key=''):
    """
    Confirm a payment that was pending review, then apply it to its order.

    `transaction_key` lets the reviewer record the bank's transaction key they
    checked by hand; it is refused if another payment already holds it, which
    is what stops the same receipt from being approved twice.
    """
    from core.models import Payment

    key = (transaction_key or '').strip()
    with transaction.atomic():
        # Order first, then payment: the same order every payment path locks in.
        order = lock_order(payment.sales_order_id)
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.PENDING_REVIEW:
            raise PaymentReviewError(
                f'Payment {locked.payment_number} is {locked.get_status_display()}; '
                f'only a payment pending review can be approved.',
                'payment_not_pending',
            )
        if order.status not in approvable_statuses():
            raise OrderNotPayableError(order, approvable_statuses())

        update_fields = ['status', 'reviewed_by', 'reviewed_at', 'review_notes']
        if key:
            if locked.transaction_key and locked.transaction_key != key:
                raise PaymentReviewError(
                    'This payment already has a different transaction key.',
                    'transaction_key_mismatch',
                )
            if Payment.objects.filter(transaction_key=key).exclude(pk=locked.pk).exists():
                raise DuplicateTransactionError(key)
            locked.transaction_key = key
            update_fields.append('transaction_key')

        locked.status = Payment.CONFIRMED
        locked.reviewed_by = user
        locked.reviewed_at = timezone.now()
        locked.review_notes = (notes or '').strip()
        try:
            with transaction.atomic():
                locked.save(update_fields=update_fields)
        except IntegrityError:
            # Another approval claimed the same key between the check and the write.
            raise DuplicateTransactionError(key)

        application = apply_confirmed_payments(order)
    return locked, application


def reject_payment(payment, *, user, notes):
    """
    Mark a pending payment as rejected. The order is left as it is.

    A reason is required: a rejected receipt is the record someone will look
    at when the customer disputes it.
    """
    from core.models import Payment

    notes = (notes or '').strip()
    if not notes:
        raise PaymentReviewError('A reason is required to reject a payment.', 'review_notes_required')

    with transaction.atomic():
        lock_order(payment.sales_order_id)
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.PENDING_REVIEW:
            raise PaymentReviewError(
                f'Payment {locked.payment_number} is {locked.get_status_display()}; '
                f'only a payment pending review can be rejected.',
                'payment_not_pending',
            )
        locked.status = Payment.REJECTED
        locked.reviewed_by = user
        locked.reviewed_at = timezone.now()
        locked.review_notes = notes
        locked.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])
    return locked
