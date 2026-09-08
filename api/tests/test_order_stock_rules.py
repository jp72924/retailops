"""
Stock rules on the REST surface.

The single-order confirm action and the bulk one previously each re-implemented
the transition, and neither checked stock -- so an order could be confirmed
against inventory that never existed and drive a product negative. Both now
delegate to SalesOrder.confirm(), and these tests pin that they agree.
"""
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from core.models import InventoryMovement, Role, SalesOrder
from api.tests.helpers import auth_client, make_order, make_product, make_user


class OrderConfirmStockAPITests(APITestCase):
    def setUp(self):
        self.manager = make_user(Role.MANAGER)
        auth_client(self.client, self.manager)

    def _pending_order(self, stock, quantity):
        """A Pending order for `quantity` of a product holding `stock` units."""
        product = make_product(stock=stock, unit_price='10.00')
        order = make_order(
            product=product,
            user=self.manager,
            status=SalesOrder.PENDING,
            quantity=quantity,
        )
        return order, product

    # ── single-order confirm ────────────────────────────────────────────────

    def test_confirm_refuses_stock_that_never_existed(self):
        order, product = self._pending_order(stock=0, quantity=3)

        response = self.client.post(f'/api/v1/orders/{order.pk}/confirm/')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'insufficient_stock')

        shortfalls = response.data['insufficient']
        self.assertEqual(len(shortfalls), 1)
        self.assertEqual(shortfalls[0]['sku'], product.sku)
        self.assertEqual(shortfalls[0]['requested'], 3)
        self.assertEqual(shortfalls[0]['available'], 0)

    def test_refused_confirm_never_drives_stock_negative(self):
        """The invariant the original bug violated."""
        order, product = self._pending_order(stock=0, quantity=3)

        self.client.post(f'/api/v1/orders/{order.pk}/confirm/')

        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.PENDING)
        self.assertEqual(product.current_stock, 0)
        self.assertFalse(
            InventoryMovement.objects.filter(
                product=product, movement_type=InventoryMovement.SALE,
            ).exists()
        )

    def test_confirm_refuses_partial_stock(self):
        order, product = self._pending_order(stock=2, quantity=3)

        response = self.client.post(f'/api/v1/orders/{order.pk}/confirm/')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['insufficient'][0]['available'], 2)
        self.assertEqual(product.current_stock, 2)

    def test_confirm_succeeds_and_deducts_with_enough_stock(self):
        order, product = self._pending_order(stock=5, quantity=3)

        response = self.client.post(f'/api/v1/orders/{order.pk}/confirm/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], SalesOrder.CONFIRMED)
        self.assertEqual(product.current_stock, 2)

    def test_wrong_status_still_reports_wrong_status(self):
        """Pre-existing error contract must survive the refactor."""
        product = make_product(stock=5)
        order = make_order(
            product=product, user=self.manager, status=SalesOrder.DRAFT, quantity=1,
        )

        response = self.client.post(f'/api/v1/orders/{order.pk}/confirm/')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'wrong_status')

    def test_empty_order_still_reports_no_items(self):
        """Pre-existing error contract must survive the refactor."""
        order = make_order(user=self.manager, status=SalesOrder.PENDING)
        order.items.all().delete()

        response = self.client.post(f'/api/v1/orders/{order.pk}/confirm/')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'no_items')

    # ── bulk confirm ────────────────────────────────────────────────────────

    def test_bulk_confirm_enforces_the_same_rule(self):
        """
        The bulk path used to re-implement the transition inline, which is how
        it skipped checks the single-order path enforced.
        """
        short_order, short_product = self._pending_order(stock=0, quantity=2)

        response = self.client.post(
            '/api/v1/orders/bulk-transition/',
            {'order_ids': [short_order.pk], 'action': 'confirm'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['succeeded'], [])
        self.assertEqual(len(response.data['failed']), 1)

        failure = response.data['failed'][0]
        self.assertEqual(failure['id'], short_order.pk)
        self.assertEqual(failure['code'], 'insufficient_stock')
        self.assertEqual(failure['insufficient'][0]['sku'], short_product.sku)

        short_order.refresh_from_db()
        self.assertEqual(short_order.status, SalesOrder.PENDING)
        self.assertEqual(short_product.current_stock, 0)

    def test_bulk_confirm_is_per_order_not_all_or_nothing(self):
        """One short order must not block a well-stocked sibling."""
        ok_order, ok_product = self._pending_order(stock=5, quantity=1)
        short_order, short_product = self._pending_order(stock=0, quantity=1)

        response = self.client.post(
            '/api/v1/orders/bulk-transition/',
            {'order_ids': [ok_order.pk, short_order.pk], 'action': 'confirm'},
            format='json',
        )

        self.assertEqual(
            [o['id'] for o in response.data['succeeded']], [ok_order.pk],
        )
        self.assertEqual(
            [f['id'] for f in response.data['failed']], [short_order.pk],
        )

        ok_order.refresh_from_db()
        short_order.refresh_from_db()
        self.assertEqual(ok_order.status, SalesOrder.CONFIRMED)
        self.assertEqual(short_order.status, SalesOrder.PENDING)
        self.assertEqual(ok_product.current_stock, 4)
        self.assertEqual(short_product.current_stock, 0)

    def test_single_and_bulk_confirm_agree(self):
        """
        Same order shape, both paths: identical outcome. This is the parity the
        shared SalesOrder.confirm() exists to guarantee.
        """
        single_order, _ = self._pending_order(stock=0, quantity=1)
        bulk_order, _ = self._pending_order(stock=0, quantity=1)

        single = self.client.post(f'/api/v1/orders/{single_order.pk}/confirm/')
        bulk = self.client.post(
            '/api/v1/orders/bulk-transition/',
            {'order_ids': [bulk_order.pk], 'action': 'confirm'},
            format='json',
        )

        self.assertEqual(single.data['code'], 'insufficient_stock')
        self.assertEqual(bulk.data['failed'][0]['code'], 'insufficient_stock')

        single_order.refresh_from_db()
        bulk_order.refresh_from_db()
        self.assertEqual(single_order.status, bulk_order.status)
        self.assertEqual(single_order.status, SalesOrder.PENDING)

    # ── lifecycle ───────────────────────────────────────────────────────────

    def test_order_cannot_reach_delivered_without_stock(self):
        """
        The reported symptom: an order completing its whole lifecycle against
        stock that never existed. The lifecycle must stop at confirmation.
        """
        order, product = self._pending_order(stock=0, quantity=1)

        confirm = self.client.post(f'/api/v1/orders/{order.pk}/confirm/')
        self.assertEqual(confirm.status_code, status.HTTP_409_CONFLICT)

        # Every later transition requires a status the order never reached.
        for step in ('ship', 'deliver'):
            response = self.client.post(f'/api/v1/orders/{order.pk}/{step}/')
            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.PENDING)
        self.assertEqual(product.current_stock, 0)
        self.assertGreaterEqual(product.current_stock, 0)
