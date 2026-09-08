"""
Stock rules at the model and back-office layer.

Regression tests for a bug where an order could complete its entire lifecycle
against stock that never existed: a product was registered but no inventory was
ever received, and confirming the order drove its stock negative. Confirmation
is where stock leaves the building, so that is where availability is enforced.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.exceptions import EmptyOrderError, InsufficientStockError, OrderStateError
from core.models import (
    Customer,
    InventoryMovement,
    Product,
    ProductCategory,
    Role,
    SalesOrder,
    SalesOrderItem,
    User,
)
from core.services.inventory import assert_stock_available, available_stock


def _stock(product):
    """Read a product's stock the same way the domain does."""
    return product.current_stock


class StockRuleFixture(TestCase):
    """Shared fixture: a product that was registered but never received."""

    def setUp(self):
        self.manager = User.objects.create_user(
            email='manager@example.com',
            password='ManagerPass123!',
            first_name='Mia',
            last_name='Manager',
            role=Role.objects.create(name=Role.MANAGER),
        )
        self.category = ProductCategory.objects.create(name='Widgets')
        self.customer = Customer.objects.create(
            first_name='Cara', last_name='Customer', email='cara@example.com',
        )
        # The exact reported scenario: the product exists, but no positive
        # inventory movement was ever recorded, so its stock is 0.
        self.product = Product.objects.create(
            sku='NOSTOCK-1',
            name='Never Received',
            category=self.category,
            unit_price=Decimal('10.00'),
            external_image_url='https://example.com/p.png',
        )

    def _order(self, quantity=3, status=SalesOrder.PENDING, product=None):
        product = product or self.product
        order = SalesOrder.objects.create(
            customer=self.customer, status=status, created_by=self.manager,
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            product=product,
            quantity=quantity,
            unit_price=product.unit_price,
            line_total=product.unit_price * quantity,
        )
        return order

    def _receive(self, quantity, product=None):
        product = product or self.product
        InventoryMovement.objects.create(
            product=product,
            movement_type=InventoryMovement.PURCHASE,
            quantity=quantity,
            reference_type=InventoryMovement.MANUAL_ADJUSTMENT,
            reference_id=0,
            notes='test stock',
            created_by=self.manager,
        )


class AvailableStockTests(StockRuleFixture):
    def test_product_with_no_movements_reports_zero_not_missing(self):
        """A product nobody ever received is 0 in stock, not absent."""
        self.assertEqual(available_stock([self.product]), {self.product.pk: 0})
        self.assertEqual(_stock(self.product), 0)

    def test_repeated_products_are_summed_before_checking(self):
        """
        Two lines of five must be checked as ten. Checking each line
        independently would let 5 + 5 pass against 6 units of stock.
        """
        self._receive(6)
        with self.assertRaises(InsufficientStockError) as ctx:
            assert_stock_available([(self.product, 5), (self.product, 5)])
        shortfall = ctx.exception.shortfalls[0]
        self.assertEqual(shortfall['requested'], 10)
        self.assertEqual(shortfall['available'], 6)

    def test_reports_every_short_product_not_just_the_first(self):
        other = Product.objects.create(
            sku='NOSTOCK-2', name='Also Empty', category=self.category,
            unit_price=Decimal('5.00'), external_image_url='https://example.com/q.png',
        )
        with self.assertRaises(InsufficientStockError) as ctx:
            assert_stock_available([(self.product, 1), (other, 1)])
        self.assertEqual(
            [s['sku'] for s in ctx.exception.shortfalls],
            ['NOSTOCK-1', 'NOSTOCK-2'],
        )


class SalesOrderConfirmTests(StockRuleFixture):
    def test_confirm_refuses_when_stock_was_never_received(self):
        order = self._order(quantity=3)
        with self.assertRaises(InsufficientStockError) as ctx:
            order.confirm(self.manager)

        shortfall = ctx.exception.shortfalls[0]
        self.assertEqual(shortfall['sku'], 'NOSTOCK-1')
        self.assertEqual(shortfall['requested'], 3)
        self.assertEqual(shortfall['available'], 0)

    def test_refused_confirm_leaves_no_trace(self):
        """
        The refusal must be atomic: no status change, no movements, and above
        all no negative stock. This is the invariant the original bug broke.
        """
        order = self._order(quantity=3)
        with self.assertRaises(InsufficientStockError):
            order.confirm(self.manager)

        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.PENDING)
        self.assertIsNone(order.confirmed_at)
        self.assertIsNone(order.confirmed_by)
        self.assertEqual(InventoryMovement.objects.count(), 0)
        self.assertEqual(_stock(self.product), 0)

    def test_confirm_refuses_partial_stock(self):
        """Two units on hand does not cover an order for three."""
        self._receive(2)
        order = self._order(quantity=3)
        with self.assertRaises(InsufficientStockError):
            order.confirm(self.manager)
        self.assertEqual(_stock(self.product), 2)

    def test_confirm_succeeds_and_deducts_when_stock_suffices(self):
        self._receive(5)
        order = self._order(quantity=3)
        order.confirm(self.manager)

        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.CONFIRMED)
        self.assertEqual(order.confirmed_by, self.manager)
        self.assertIsNotNone(order.confirmed_at)
        self.assertEqual(_stock(self.product), 2)

    def test_confirm_allows_exactly_enough_stock(self):
        """The boundary is >=, not >. Selling the last unit is legitimate."""
        self._receive(3)
        order = self._order(quantity=3)
        order.confirm(self.manager)
        self.assertEqual(_stock(self.product), 0)

    def test_confirm_rejects_wrong_status(self):
        self._receive(5)
        order = self._order(quantity=1, status=SalesOrder.DRAFT)
        with self.assertRaises(OrderStateError):
            order.confirm(self.manager)

    def test_confirm_rejects_empty_order(self):
        order = SalesOrder.objects.create(
            customer=self.customer, status=SalesOrder.PENDING, created_by=self.manager,
        )
        with self.assertRaises(EmptyOrderError):
            order.confirm(self.manager)

    def test_second_confirm_cannot_double_deduct(self):
        """
        Confirming twice must not deduct twice. The status guard is what stops
        it, so this also pins that the guard runs before any stock is touched.
        """
        self._receive(5)
        order = self._order(quantity=3)
        order.confirm(self.manager)
        with self.assertRaises(OrderStateError):
            order.confirm(self.manager)
        self.assertEqual(_stock(self.product), 2)


class BackOfficeConfirmTests(StockRuleFixture):
    """The HTML back-office must enforce the same rule as the API."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.manager)

    def test_confirm_view_refuses_and_explains(self):
        order = self._order(quantity=3)
        response = self.client.post(
            reverse('order-confirm', args=[order.pk]), follow=True,
        )
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.PENDING)
        self.assertEqual(_stock(self.product), 0)

        text = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('NOSTOCK-1', text)
        self.assertIn('was not confirmed', text)

    def test_confirm_view_succeeds_with_stock(self):
        self._receive(5)
        order = self._order(quantity=3)
        self.client.post(reverse('order-confirm', args=[order.pk]), follow=True)

        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.CONFIRMED)
        self.assertEqual(_stock(self.product), 2)


class EmptyCatalogueOrderFormTests(TestCase):
    """
    With no products registered at all, the order form must say so rather than
    claiming every product is already on the order -- the two states have
    different fixes, and an empty catalogue is the normal state of a fresh
    install.
    """

    def setUp(self):
        self.manager = User.objects.create_user(
            email='manager2@example.com',
            password='ManagerPass123!',
            first_name='Mo',
            last_name='Manager',
            role=Role.objects.create(name=Role.MANAGER),
        )
        self.client.force_login(self.manager)
        self.customer = Customer.objects.create(
            first_name='Cara', last_name='Customer', email='cara2@example.com',
        )

    def test_order_form_offers_empty_catalogue_guidance(self):
        self.assertEqual(Product.objects.count(), 0)
        response = self.client.get(reverse('order-create'))
        self.assertEqual(response.status_code, 200)

        body = response.content.decode('utf-8')
        self.assertIn('No products have been registered yet', body)

    def test_order_form_still_carries_the_exhausted_catalogue_message(self):
        """Both messages must exist; the fix distinguishes them, not replaces one."""
        response = self.client.get(reverse('order-create'))
        body = response.content.decode('utf-8')
        self.assertIn('Every product is already on this order', body)
        self.assertIn('No products have been registered yet', body)
