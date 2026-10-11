"""
Products that do not track inventory, and stock given back from the ledger.

A licence or a service has no stock to run out of: confirming an order for it
must not need stock, lock its row, or write a movement. Cancelling or refunding
gives back what the order's own movements took, and nothing else.
"""
from unittest.mock import patch

from django.test import TestCase

from core.exceptions import InsufficientStockError, OrderStateError
from core.models import InventoryMovement, Product, Role, SalesOrder, SalesOrderItem
from core.services import inventory, orders
from core.services.orders import cancel_order, refund_order, restore_stock_for_order
from api.tests.helpers import make_customer, make_product, make_user


def _movements(order, movement_type):
    return InventoryMovement.objects.filter(
        reference_type=InventoryMovement.SALES_ORDER,
        reference_id=order.pk,
        movement_type=movement_type,
    )


class UntrackedFixture(TestCase):
    def setUp(self):
        self.manager = make_user(Role.MANAGER)
        self.customer = make_customer()
        self.box = make_product(sku='BOX-1', stock=5)
        self.licence = make_product(
            sku='LIC-12M', stock=0, kind=Product.DIGITAL, track_inventory=False,
        )

    def _order(self, *lines, status=SalesOrder.PENDING):
        order = SalesOrder.objects.create(
            customer=self.customer, status=status, created_by=self.manager,
        )
        for product, quantity in lines:
            SalesOrderItem.objects.create(
                sales_order=order, product=product, quantity=quantity,
                unit_price=product.unit_price, line_total=product.unit_price * quantity,
            )
        return order

    def _stock(self, product):
        return Product.objects.get(pk=product.pk).current_stock


class UntrackedStockPropertiesTests(UntrackedFixture):
    def test_an_untracked_product_has_no_stock_to_report(self):
        self.assertIsNone(self.licence.current_stock)
        self.assertFalse(self.licence.is_low_stock)
        self.assertFalse(self.licence.is_out_of_stock)

    def test_stock_recorded_before_tracking_was_turned_off_is_not_reported(self):
        Product.objects.filter(pk=self.box.pk).update(track_inventory=False)
        box = Product.objects.get(pk=self.box.pk)
        self.assertIsNone(box.current_stock)
        self.assertFalse(box.is_out_of_stock)


class AssertStockAvailableTests(UntrackedFixture):
    def test_an_untracked_product_is_never_short(self):
        # No movements and a huge quantity: nothing to run out of.
        inventory.assert_stock_available([(self.licence, 10_000)])

    def test_untracked_lines_do_not_hide_a_tracked_shortfall(self):
        with self.assertRaises(InsufficientStockError) as caught:
            inventory.assert_stock_available([(self.licence, 3), (self.box, 6)])
        self.assertEqual([s['sku'] for s in caught.exception.shortfalls], ['BOX-1'])


class ConfirmUntrackedTests(UntrackedFixture):
    def test_untracked_lines_need_no_stock_and_write_no_movement(self):
        order = self._order((self.box, 2), (self.licence, 50))

        order.confirm(self.manager)

        self.assertEqual(order.status, SalesOrder.CONFIRMED)
        sales = _movements(order, InventoryMovement.SALE)
        self.assertEqual(list(sales.values_list('product_id', 'quantity')), [(self.box.pk, -2)])
        self.assertFalse(self.licence.inventory_movements.exists())
        self.assertEqual(self._stock(self.box), 3)

    def test_tracked_lines_are_still_checked(self):
        order = self._order((self.box, 6), (self.licence, 1))

        with self.assertRaises(InsufficientStockError) as caught:
            order.confirm(self.manager)

        self.assertEqual([s['sku'] for s in caught.exception.shortfalls], ['BOX-1'])
        self.assertFalse(InventoryMovement.objects.filter(movement_type=InventoryMovement.SALE).exists())

    def test_an_order_of_only_untracked_products_locks_nothing(self):
        order = self._order((self.licence, 3))

        with patch('core.services.inventory.lock_products') as lock:
            order.confirm(self.manager)

        lock.assert_not_called()
        self.assertEqual(order.status, SalesOrder.CONFIRMED)

    def test_a_product_switched_to_untracked_before_the_lock_gets_no_movement(self):
        order = self._order((self.box, 2))
        real_lock = inventory.lock_products

        def switch_then_lock(product_ids):
            product_ids = list(product_ids)
            Product.objects.filter(pk=self.box.pk).update(track_inventory=False)
            return real_lock(product_ids)

        with patch('core.services.inventory.lock_products', side_effect=switch_then_lock):
            order.confirm(self.manager)

        self.assertFalse(_movements(order, InventoryMovement.SALE).exists())


class RestoreFromLedgerTests(UntrackedFixture):
    def test_cancelling_returns_only_what_was_deducted(self):
        order = self._order((self.box, 2), (self.licence, 4))
        order.confirm(self.manager)

        cancel_order(order, self.manager)

        self.assertEqual(order.status, SalesOrder.CANCELLED)
        returns = _movements(order, InventoryMovement.RETURN)
        self.assertEqual(list(returns.values_list('product_id', 'quantity')), [(self.box.pk, 2)])
        self.assertEqual(self._stock(self.box), 5)
        self.assertFalse(self.licence.inventory_movements.exists())

    def test_refunding_returns_only_what_was_deducted(self):
        order = self._order((self.box, 1), (self.licence, 1))
        order.confirm(self.manager)
        SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.PAID)
        order.refresh_from_db()

        refund_order(order, self.manager)

        self.assertEqual(order.status, SalesOrder.REFUNDED)
        returns = _movements(order, InventoryMovement.RETURN)
        self.assertEqual(list(returns.values_list('product_id', 'quantity')), [(self.box.pk, 1)])
        self.assertEqual(self._stock(self.box), 5)

    def test_an_order_that_never_deducted_gets_nothing_back(self):
        # Confirmed without a SALE movement, as orders were before deductions
        # existed. Adding its quantity back would invent stock.
        order = self._order((self.box, 3), status=SalesOrder.CONFIRMED)

        cancel_order(order, self.manager)

        self.assertFalse(_movements(order, InventoryMovement.RETURN).exists())
        self.assertEqual(self._stock(self.box), 5)

    def test_restoring_twice_gives_stock_back_once(self):
        order = self._order((self.box, 2))
        order.confirm(self.manager)

        restore_stock_for_order(order, self.manager, notes='first')
        second = restore_stock_for_order(order, self.manager, notes='second')

        self.assertEqual(second, [])
        self.assertEqual(_movements(order, InventoryMovement.RETURN).count(), 1)
        self.assertEqual(self._stock(self.box), 5)

    def test_a_second_cancel_from_a_stale_copy_is_refused(self):
        order = self._order((self.box, 2))
        order.confirm(self.manager)
        stale = SalesOrder.objects.get(pk=order.pk)

        cancel_order(order, self.manager)
        with self.assertRaises(OrderStateError):
            cancel_order(stale, self.manager)

        self.assertEqual(_movements(order, InventoryMovement.RETURN).count(), 1)
        self.assertEqual(self._stock(self.box), 5)

    def test_stock_taken_while_tracked_comes_back_after_tracking_is_turned_off(self):
        order = self._order((self.box, 2))
        order.confirm(self.manager)
        Product.objects.filter(pk=self.box.pk).update(track_inventory=False)

        cancel_order(order, self.manager)

        Product.objects.filter(pk=self.box.pk).update(track_inventory=True)
        self.assertEqual(self._stock(self.box), 5)

    def test_the_order_row_is_locked_before_the_ledger_is_read(self):
        order = self._order((self.box, 2))
        order.confirm(self.manager)
        calls = []
        real_lock = orders.lock_order

        def spy(order_id):
            calls.append(order_id)
            self.assertFalse(_movements(order, InventoryMovement.RETURN).exists())
            return real_lock(order_id)

        with patch('core.services.orders.lock_order', side_effect=spy):
            cancel_order(order, self.manager)

        self.assertEqual(calls, [order.pk])
