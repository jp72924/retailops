"""
Kiosk checkout locks products the way every other stock writer does.

It used to lock one SKU at a time in cart order. Two checkouts holding the
same products in a different order could deadlock; PostgreSQL aborted one with
a 500 after its receipt had already been accepted. SQLite emits no FOR UPDATE,
so these pin the call into the shared pk-ordered lock instead of racing.
"""
from unittest.mock import patch

from rest_framework.test import APITestCase

from core.models import Payment, Product, SalesOrder
from core.services import inventory
from api.tests.helpers import make_customer, make_kiosk_station, make_product


class KioskLockOrderTests(APITestCase):
    def setUp(self):
        _station, self.raw_key = make_kiosk_station()
        self.customer = make_customer()
        self.first = make_product(sku='LOCK-A', unit_price='4.00', stock=10)
        self.second = make_product(sku='LOCK-B', unit_price='6.00', stock=10)

    def _checkout(self, skus):
        return self.client.post('/api/v1/kiosk/checkout/', {
            'customer_id': self.customer.pk,
            'items': [{'sku': sku, 'quantity': 1} for sku in skus],
            'payment_method': Payment.CARD,
            'payment_reference': 'CARD-1',
        }, format='json', HTTP_AUTHORIZATION=f'KioskKey {self.raw_key}')

    def test_products_are_locked_once_through_the_shared_rule(self):
        calls = []

        def spy(product_ids):
            product_ids = list(product_ids)
            calls.append(product_ids)
            return inventory.lock_products(product_ids)

        # Cart order is the reverse of primary-key order.
        with patch('api.kiosk.views.lock_products', side_effect=spy):
            response = self._checkout(['LOCK-B', 'LOCK-A'])

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(calls[0]), sorted([self.first.pk, self.second.pk]))
        self.assertEqual(SalesOrder.objects.get().items.count(), 2)

    def test_the_first_unavailable_sku_in_the_cart_is_reported(self):
        response = self._checkout(['LOCK-A', 'GONE-1', 'GONE-2'])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'invalid_product')
        self.assertIn('GONE-1', response.data['error'])

    def test_a_product_deactivated_before_the_lock_is_refused(self):
        real_lock = inventory.lock_products

        def deactivate_then_lock(product_ids):
            product_ids = list(product_ids)
            Product.objects.filter(pk=self.second.pk).update(is_active=False)
            return real_lock(product_ids)

        with patch('api.kiosk.views.lock_products', side_effect=deactivate_then_lock):
            response = self._checkout(['LOCK-A', 'LOCK-B'])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'invalid_product')
        self.assertIn('LOCK-B', response.data['error'])
        self.assertFalse(SalesOrder.objects.exists())
