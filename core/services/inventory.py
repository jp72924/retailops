"""
Stock availability rules.

One place decides whether stock may be removed.  Every caller that deducts
stock -- the REST confirm action and its bulk sibling, the back-office
confirm view, and the kiosk checkout -- goes through `deduct_stock_for_order`
or `assert_stock_available`, so the rule cannot drift between surfaces.

Two properties of this domain shape the implementation:

* Stock is event-sourced.  A product's available quantity is the SUM of its
  InventoryMovement rows; there is no balance column to read.  Availability
  must therefore be aggregated, which is why this module exists rather than a
  simple `product.stock >= n` check scattered at each call site.

* The aggregate is only trustworthy while the product rows are locked.
  Without SELECT FOR UPDATE two concurrent confirmations can each read "5
  available", each deduct 5, and leave the product at -5.  That is why the
  check and the write must happen together, inside one transaction.
"""
from django.db import models

from core.exceptions import InsufficientStockError


def available_stock(products):
    """
    Return ``{product_id: available_quantity}`` for the given products.

    Products with no movements at all are reported as 0 rather than omitted,
    so callers never have to distinguish "no stock" from "no rows".
    """
    from core.models import InventoryMovement

    ids = [p.pk for p in products]
    if not ids:
        return {}

    totals = {
        row['product_id']: row['total'] or 0
        for row in (
            InventoryMovement.objects
            .filter(product_id__in=ids)
            .values('product_id')
            .annotate(total=models.Sum('quantity'))
        )
    }
    return {pid: totals.get(pid, 0) for pid in ids}


def assert_stock_available(requirements):
    """
    Raise InsufficientStockError if any requirement exceeds available stock.

    ``requirements`` is an iterable of ``(product, quantity)`` pairs.  Repeated
    products are summed first: two lines for five units each must be checked as
    ten, not twice as five.

    Call this with the product rows already locked (see `lock_products`) if the
    result is about to be acted on.
    """
    needed = {}
    products = {}
    for product, quantity in requirements:
        products[product.pk] = product
        needed[product.pk] = needed.get(product.pk, 0) + quantity

    if not needed:
        return

    stock = available_stock(products.values())
    shortfalls = [
        {
            'sku': products[pid].sku,
            'product_id': pid,
            'requested': quantity,
            'available': stock.get(pid, 0),
        }
        for pid, quantity in needed.items()
        if stock.get(pid, 0) < quantity
    ]
    if shortfalls:
        shortfalls.sort(key=lambda s: s['sku'])
        raise InsufficientStockError(shortfalls)


def lock_products(product_ids):
    """
    Re-fetch products with SELECT FOR UPDATE, ordered by primary key.

    The ordering is deliberate: two confirmations touching the same pair of
    products in opposite orders would deadlock.  Locking by ascending pk gives
    every caller the same acquisition order.

    Must be called inside a transaction.
    """
    from core.models import Product

    return {
        product.pk: product
        for product in (
            Product.objects
            .select_for_update()
            .filter(pk__in=sorted(set(product_ids)))
            .order_by('pk')
        )
    }


def deduct_stock_for_order(order, user, *, notes=None):
    """
    Lock the order's products, verify stock, and write the SALE movements.

    Returns the created InventoryMovement rows.  Raises InsufficientStockError
    -- before writing anything -- if any line exceeds what is available.

    Must be called inside a transaction: the lock taken here has to be held
    until the caller's status change is committed, or the check it guards is
    meaningless.
    """
    from core.models import InventoryMovement

    items = list(order.items.select_related('product').all())
    if not items:
        return []

    locked = lock_products(item.product_id for item in items)
    assert_stock_available(
        (locked[item.product_id], item.quantity) for item in items
    )

    movements = [
        InventoryMovement(
            product=locked[item.product_id],
            movement_type=InventoryMovement.SALE,
            quantity=-item.quantity,
            reference_type=InventoryMovement.SALES_ORDER,
            reference_id=order.pk,
            notes=notes or f'Stock deducted on confirmation of {order.order_number}',
            created_by=user,
        )
        for item in items
    ]
    return InventoryMovement.objects.bulk_create(movements)
