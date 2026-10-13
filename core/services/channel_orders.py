"""
Orders that arrive through a sales channel.

The flow has two steps, because that is how a receipt payment works: the
customer is given an order number and an amount *before* they pay.

1. `create_channel_order` creates and confirms the order in one short
   transaction, which reserves the stock through the same path every other
   order uses. It freezes the currency, so the amount the customer was quoted
   in the secondary currency cannot move under them. No third-party call
   happens in here.
2. The customer pays and the store submits the receipt
   (core.services.channel_payments).

`client_reference`, the store's own order id, is the idempotency key. A store
that retries after a timeout gets the order it already made, not a second one
and a second stock reservation.

Lock order matters. The kiosk takes product locks and then draws an order
number from a counter row; this takes them in the same order, so neither can
deadlock the other.
"""
from collections import OrderedDict
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Prefetch
from django.utils import timezone

from core.models import (
    ChannelOrder,
    CurrencySnapshot,
    Payment,
    SalesOrder,
    SalesOrderItem,
)
from core.services.currency import current_context
from core.services.customer_links import link_customer
from core.services.inventory import lock_products
from core.services.listings import orderable_products
from core.services.orders import cancel_order
from core.services.payments import lock_order

MAX_LINE_QUANTITY = 1000

# What the store shows its customer. A pure function of the order and its
# payments, so every endpoint reports the same thing.
AWAITING_PAYMENT = 'awaiting_payment'
PAYMENT_UNDER_REVIEW = 'payment_under_review'
PROCESSING = 'processing'
COMPLETED = 'completed'
CANCELLED = 'cancelled'
REFUNDED = 'refunded'

REVIEW_VERIFIED = 'verified'
REVIEW_NEEDS_REVIEW = 'needs_review'
REVIEW_REJECTED = 'rejected'


class ChannelOrderError(Exception):
    code = 'channel_order_error'


class InvalidOrderItems(ChannelOrderError):
    def __init__(self, message, code='invalid_product'):
        self.code = code
        super().__init__(message)


class ClientReferenceConflict(ChannelOrderError):
    code = 'client_reference_conflict'

    def __init__(self):
        super().__init__('This client_reference was already used for a different order.')


def customer_status(order, payments, delivery_complete=False):
    """
    What state a customer should see for `order`, given its `payments`
    (every status, as already loaded).

    `delivery_complete` says the whole order was delivered digitally (see
    core.services.entitlements.delivery_complete). A paid order is not moved
    to Delivered by software, because a refund needs it Paid, so
    "completed" is derived: from Delivered, or from a finished delivery.
    """
    if order.status == SalesOrder.CANCELLED:
        return CANCELLED
    if order.status == SalesOrder.REFUNDED:
        return REFUNDED
    if order.status == SalesOrder.DELIVERED:
        return COMPLETED
    if order.status in (SalesOrder.PAID, SalesOrder.SHIPPED):
        return COMPLETED if delivery_complete else PROCESSING
    if any(payment.status == Payment.PENDING_REVIEW for payment in payments):
        return PAYMENT_UNDER_REVIEW
    return AWAITING_PAYMENT


def review_status(payment):
    """How a payment reads to the customer: verified, needs review, or rejected."""
    return {
        Payment.CONFIRMED: REVIEW_VERIFIED,
        Payment.PENDING_REVIEW: REVIEW_NEEDS_REVIEW,
        Payment.REJECTED: REVIEW_REJECTED,
    }[payment.status]


def channel_orders():
    """
    Channel orders with everything their representation reads, in four queries.

    The lines are fetched once with their product and the listing variant that
    sold it, so a line can name the listing and the variant label without a
    query per line.
    """
    lines = SalesOrderItem.objects.select_related(
        'product', 'product__listing_variant__listing',
    )
    return (
        ChannelOrder.objects
        .select_related('sales_order', 'currency_snapshot', 'customer_link')
        .prefetch_related(
            Prefetch('sales_order__items', queryset=lines),
            'sales_order__items__entitlements',
            'sales_order__payments',
        )
    )


def merge_items(items):
    """``[(sku, quantity), ...]`` -> ``{sku: quantity}``, summing repeated SKUs."""
    merged = OrderedDict()
    for sku, quantity in items:
        merged[sku] = merged.get(sku, 0) + quantity
    oversized = [sku for sku, quantity in merged.items() if quantity > MAX_LINE_QUANTITY]
    if oversized:
        raise InvalidOrderItems(
            f'At most {MAX_LINE_QUANTITY} of one product can be ordered: {", ".join(oversized)}.',
            code='invalid_quantity',
        )
    return merged


def _existing(channel, client_reference):
    return (
        channel_orders()
        .filter(channel=channel, client_reference=client_reference)
        .first()
    )


def _assert_same_request(existing, profile, merged):
    """The same reference must mean the same order, not a different cart."""
    same_customer = existing.customer_link.external_ref == profile.external_ref
    existing_items = {
        item.product.sku: item.quantity for item in existing.sales_order.items.all()
    }
    if not same_customer or existing_items != dict(merged):
        raise ClientReferenceConflict()


def create_channel_order(channel, *, client_reference, profile, items, fulfillment, contact):
    """
    Create and confirm an order for a channel's customer.

    Returns ``(channel_order, created)``; ``created`` is False when the
    `client_reference` already named an order, which is returned unchanged.

    Raises InvalidOrderItems, ClientReferenceConflict, CustomerEmailConflict and
    InsufficientStockError. Each leaves nothing behind: no order, no stock
    reserved, no customer.
    """
    merged = merge_items(items)

    existing = _existing(channel, client_reference)
    if existing is not None:
        _assert_same_request(existing, profile, merged)
        return existing, False

    products = orderable_products(merged, channel)
    unavailable = [sku for sku in merged if sku not in products]
    if unavailable:
        raise InvalidOrderItems(f'Not available for sale: {", ".join(unavailable)}.')

    # Interned in autocommit, before the transaction: its unique-index entry is
    # then never held under locks, and a failed order leaves at worst a row the
    # next order reuses.
    snapshot = CurrencySnapshot.objects.intern(current_context())
    user = channel.service_user
    expires_at = timezone.now() + timedelta(minutes=channel.order_expiry_minutes)

    try:
        with transaction.atomic():
            # Product locks first, in primary-key order, exactly as the kiosk
            # does before it draws an order number. Products that do not track
            # inventory are never locked.
            lock_products(product.pk for product in products.values() if product.track_inventory)

            link = link_customer(channel, profile)

            order = SalesOrder(
                customer=link.customer,
                status=SalesOrder.PENDING,
                created_by=user,
                notes=f'Online order - {channel.name} - {client_reference}',
            )
            order.save()  # draws the order number

            lines = [
                SalesOrderItem(
                    sales_order=order,
                    product=products[sku],
                    quantity=quantity,
                    unit_price=products[sku].unit_price,
                    line_total=products[sku].unit_price * quantity,
                )
                for sku, quantity in merged.items()
            ]
            SalesOrderItem.objects.bulk_create(lines)
            order.subtotal = order.total_amount = sum(line.line_total for line in lines)
            order.save(update_fields=['subtotal', 'total_amount', 'updated_at'])

            # The one stock rule every surface shares; raises InsufficientStockError.
            order.confirm(user)

            channel_order = ChannelOrder.objects.create(
                sales_order=order,
                channel=channel,
                customer_link=link,
                client_reference=client_reference,
                currency_snapshot=snapshot,
                expires_at=expires_at,
                fulfillment=fulfillment,
                contact=contact,
            )
    except IntegrityError:
        # Two requests with the same reference raced; the loser rolled back
        # whole, stock reservation included. Hand it the winner's order.
        existing = _existing(channel, client_reference)
        if existing is None:
            raise
        _assert_same_request(existing, profile, merged)
        return existing, False

    return channel_orders().get(pk=channel_order.pk), True


class OrderNotCancellable(ChannelOrderError):
    """A paid order, or one with any payment, is closed by a person, not by the store."""
    code = 'order_not_cancellable'

    def __init__(self, order):
        self.order = order
        super().__init__('This order can no longer be cancelled.')


def cancel_channel_order(channel_order):
    """
    Cancel an unpaid order the store no longer wants, giving its stock back.

    A store that lets its customer change the cart after the order exists
    would otherwise strand that stock until the order expires. Only an order
    with no payment at all can be cancelled, for the reason an expiring order
    is never one with a payment: a person may be about to approve it. The
    order is locked and re-checked, so a payment that lands meanwhile wins.
    Cancelling an already cancelled order changes nothing, so a retry is safe;
    the reference stays used, as it does for an expired order.

    Raises OrderNotCancellable.
    """
    with transaction.atomic():
        order = lock_order(channel_order.sales_order_id)
        if order.status == SalesOrder.CANCELLED:
            return
        if order.status != SalesOrder.CONFIRMED or order.payments.exists():
            raise OrderNotCancellable(order)
        order.notes = f'{order.notes}\nCancelled by the channel.'.strip()
        order.save(update_fields=['notes', 'updated_at'])
        cancel_order(order, channel_order.channel.service_user)


class ExpiryResult:
    def __init__(self, expired=0, skipped=0):
        self.expired = expired
        self.skipped = skipped


def lapsed_channel_orders(now):
    """Channel orders past their expiry that are still unpaid and have no payment at all."""
    has_payment = Payment.objects.filter(sales_order=OuterRef('sales_order_id'))
    return (
        ChannelOrder.objects
        .filter(expires_at__lte=now, sales_order__status=SalesOrder.CONFIRMED)
        .filter(~Exists(has_payment))
        .order_by('expires_at')
    )


def expire_channel_orders(*, now=None, limit=500):
    """
    Cancel the channel orders that lapsed unpaid, giving their stock back.

    An order with any payment, including one waiting for review, is left
    alone: a person may be about to approve it. The order is locked and
    re-checked before it is cancelled, so a payment that lands while this runs
    wins. Safe to run on a schedule and to run twice: a cancelled order is no
    longer a candidate.
    """
    now = now or timezone.now()
    due = list(lapsed_channel_orders(now).values_list('pk', flat=True)[:limit])

    result = ExpiryResult()
    for pk in due:
        with transaction.atomic():
            channel_order = ChannelOrder.objects.select_related('channel__service_user').get(pk=pk)
            order = lock_order(channel_order.sales_order_id)
            if order.status != SalesOrder.CONFIRMED or order.payments.exists():
                result.skipped += 1
                continue
            order.notes = f'{order.notes}\nExpired unpaid on {now:%Y-%m-%d %H:%M} UTC.'.strip()
            order.save(update_fields=['notes', 'updated_at'])
            cancel_order(order, channel_order.channel.service_user, reason='expired')
            result.expired += 1
    return result
