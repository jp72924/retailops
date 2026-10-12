"""
Telling a store what changed in its orders.

A store that wants to know when a payment is approved, an order is cancelled or a licence
is issued can ask on a timer, or be told. This is the telling, and it is built so that it
cannot lie by omission and cannot be fooled by a late commit:

* **Events are differences.** An order's `published_state` (customer status, the review
  status of each payment, the status of each entitlement) is what the store was last told.
  `publish_order_changes` compares it with what is true now and records one event for
  each difference. It is called at the end of every service that changes an order, so it
  does not matter how many things changed, or in which order: the store hears the net
  result, once. Calling it twice in a row, or when nothing changed, records nothing.
* **Events are written in the transaction that makes the change.** A rollback leaves no
  event behind, and a committed change always has its event.
* **Sequence numbers follow commit order.** Each channel has a counter row, locked until
  the transaction commits, so event 41 can never commit after event 42: a store that reads
  the feed with `?after=41` cannot skip one.
* **An event carries nothing private.** No customer, no amount, no key, no OCR reading: it
  names the order and what to read again. The store re-reads the order, which is the only
  source of truth, and acts on that.

Orders that do not come from a channel are skipped at the first query.
"""
from django.db import transaction
from django.utils import timezone

from core.models import (
    ChannelEvent,
    ChannelOrder,
    ChannelWebhook,
    Entitlement,
    SalesOrder,
    SequenceCounter,
    WebhookDelivery,
)
from core.services.channel_orders import (
    AWAITING_PAYMENT,
    CANCELLED,
    COMPLETED,
    PAYMENT_UNDER_REVIEW,
    PROCESSING,
    REFUNDED,
    customer_status,
    review_status,
)
from core.services.entitlements import delivery_complete

# What the store was last told about an order that has never had an event.
BASELINE = {'customer_status': AWAITING_PAYMENT, 'payments': {}, 'entitlements': {}}

# Why an order's customer status changed, for the store's own use (a mail, a log line).
REASONS = (
    'paid', 'payment_under_review', 'payment_rejected', 'shipped', 'delivered',
    'cancelled', 'expired', 'refunded',
)


def sequence_prefix(channel_id):
    return f'chev-{channel_id}'


def current_state(order):
    """What is true about `order` now, in the shape stored as `published_state`."""
    payments = list(order.payments.all())
    entitlements = Entitlement.objects.filter(order_item__sales_order=order).order_by('pk')
    return {
        'customer_status': customer_status(order, payments, delivery_complete(order)),
        'payments': {payment.payment_number: review_status(payment) for payment in payments},
        'entitlements': {str(entitlement.pk): entitlement.status for entitlement in entitlements},
    }


def _reason(order, status, hint):
    if hint:
        return hint
    if status == CANCELLED:
        return 'cancelled'
    if status == REFUNDED:
        return 'refunded'
    if status == PAYMENT_UNDER_REVIEW:
        return 'payment_under_review'
    if status == COMPLETED:
        return 'delivered'
    if status == PROCESSING:
        return 'shipped' if order.status == SalesOrder.SHIPPED else 'paid'
    # Awaiting payment again: the receipt that was under review was rejected.
    return 'payment_rejected'


def publish_order_changes(order_id, *, reason=None):
    """
    Record an event for everything about the order that differs from what the store was told.

    Must run inside the transaction that made the change. `reason`, when the caller knows it
    better than the status does (an order that *expired* is cancelled to the status), is
    used for the order's event. Returns the events created: none for an order that is not a
    channel's, or when nothing changed.
    """
    with transaction.atomic():
        channel_order = (
            ChannelOrder.objects.select_for_update().filter(sales_order_id=order_id).first()
        )
        if channel_order is None:
            return []
        order = SalesOrder.objects.get(pk=order_id)
        published = {**BASELINE, **(channel_order.published_state or {})}
        now_state = current_state(order)
        base = {'order_number': order.order_number}
        pending = []

        for number, review in now_state['payments'].items():
            if published['payments'].get(number) != review:
                pending.append((ChannelEvent.PAYMENT_UPDATED, {**base, 'payment_number': number, 'review_status': review}))

        if now_state['customer_status'] != published['customer_status']:
            pending.append((ChannelEvent.ORDER_UPDATED, {
                **base,
                'client_reference': channel_order.client_reference,
                'customer_status': now_state['customer_status'],
                'reason': _reason(order, now_state['customer_status'], reason),
            }))

        changed = [
            int(pk) for pk, status in now_state['entitlements'].items()
            if published['entitlements'].get(pk) != status
        ]
        if changed:
            by_id = {
                e.pk: e for e in Entitlement.objects.filter(pk__in=changed).select_related('order_item__product')
            }
            for pk in changed:
                entitlement = by_id[pk]
                pending.append((ChannelEvent.ENTITLEMENT_UPDATED, {
                    **base,
                    'entitlement_id': pk,
                    'sku': entitlement.order_item.product.sku,
                    'kind': entitlement.kind,
                    'status': entitlement.status,
                }))

        if not pending:
            return []

        channel_id = channel_order.channel_id
        webhooks = list(ChannelWebhook.objects.filter(channel_id=channel_id, is_active=True))
        due = timezone.now()
        events = []
        for event_type, data in pending:
            event = ChannelEvent.objects.create(
                channel_id=channel_id,
                # Allocated here, in the caller's transaction: the counter row stays locked until it commits.
                sequence=SequenceCounter.next_value(sequence_prefix(channel_id)),
                event_type=event_type,
                order_number=order.order_number,
                data=data,
            )
            events.append(event)
            WebhookDelivery.objects.bulk_create([
                WebhookDelivery(webhook=webhook, event=event, next_attempt_at=due) for webhook in webhooks
            ])

        channel_order.published_state = now_state
        channel_order.save(update_fields=['published_state'])
        return events
