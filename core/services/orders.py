"""
Order transitions that give stock back.

Cancelling and refunding used to add each line's quantity back to stock,
whatever the order had actually taken. That is only right while every
confirmed order has written exactly one SALE per line. An order whose stock
was never deducted (confirmed before deductions existed, or for products that
do not track inventory) got stock it never took, and an order reversed twice
gave it back twice.

Stock is now restored from the ledger: whatever the order's own movements
still net to is reversed, and nothing else. A second reversal finds nothing
left to give back, and an order that never deducted writes no RETURN.

The transitions lock the order row first, the same lock every payment path
takes, so a cancellation cannot interleave with a payment or with itself.
"""
from django.db import transaction
from django.db.models import Sum

from core.exceptions import OrderStateError
from core.services.entitlements import revoke_for_order
from core.services.payments import lock_order


def restore_stock_for_order(order, user, *, notes):
    """
    Write RETURN movements reversing the stock this order still holds.

    Reads the order's movements, nets them per product, and returns each net
    deduction. Returns the created InventoryMovement rows; none when the
    order holds no stock. Call it with the order row locked, or two
    concurrent reversals could both read the same balance.
    """
    from core.models import InventoryMovement

    held = (
        InventoryMovement.objects
        .filter(reference_type=InventoryMovement.SALES_ORDER, reference_id=order.pk)
        .values('product_id')
        .annotate(net=Sum('quantity'))
        .filter(net__lt=0)
        .order_by('product_id')
    )
    movements = [
        InventoryMovement(
            product_id=row['product_id'],
            movement_type=InventoryMovement.RETURN,
            quantity=-row['net'],
            reference_type=InventoryMovement.SALES_ORDER,
            reference_id=order.pk,
            notes=notes,
            created_by=user,
        )
        for row in held
    ]
    return InventoryMovement.objects.bulk_create(movements)


def _reverse(order, user, *, expected, new_status, verb, revoke_deliveries=False):
    with transaction.atomic():
        locked = lock_order(order.pk)
        if locked.status != expected:
            raise OrderStateError(expected, locked.status)
        locked.status = new_status
        locked.save(update_fields=['status', 'updated_at'])
        if revoke_deliveries:
            revoke_for_order(locked)
        restore_stock_for_order(
            locked, user, notes=f'Stock restored on {verb} of {locked.order_number}',
        )
    order.status = locked.status
    order.updated_at = locked.updated_at
    return order


def cancel_order(order, user):
    """Confirmed -> Cancelled, giving back the stock the order took."""
    from core.models import SalesOrder
    return _reverse(
        order, user, expected=SalesOrder.CONFIRMED, new_status=SalesOrder.CANCELLED,
        verb='cancellation',
    )


def refund_order(order, user):
    """
    Paid -> Refunded, giving back the stock the order took and revoking
    what it delivered: a refunded customer keeps no licence.
    """
    from core.models import SalesOrder
    return _reverse(
        order, user, expected=SalesOrder.PAID, new_status=SalesOrder.REFUNDED,
        verb='refund', revoke_deliveries=True,
    )
