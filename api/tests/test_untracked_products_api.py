"""
Untracked products through the staff API and the kiosk.

The staff API reports them with a null current_stock and counts them as "ok";
they never appear as low or out of stock and cannot be adjusted. The kiosk does
not see them at all until Konteo Express can sell an item without a stock cap.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import InventoryMovement, Payment, Product, Role, SalesOrder
from api.tests.helpers import (
    auth_client,
    make_customer,
    make_kiosk_station,
    make_product,
    make_user,
)


class UntrackedCatalogFixture(APITestCase):
    def setUp(self):
        self.manager = make_user(Role.MANAGER)
        auth_client(self.client, self.manager)
        self.plenty = make_product(sku='OK-1', stock=10, low_stock_threshold=2)
        self.low = make_product(sku='LOW-1', stock=1, low_stock_threshold=2)
        self.out = make_product(sku='OUT-1', stock=0)
        self.licence = make_product(
            sku='LIC-12M', stock=0, kind=Product.DIGITAL, track_inventory=False,
        )

    def _skus(self, query):
        response = self.client.get(f'/api/v1/products/?{query}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return sorted(row['sku'] for row in response.data['results'])


class UntrackedProductFieldsTests(UntrackedCatalogFixture):
    def test_an_untracked_product_reports_no_stock(self):
        response = self.client.get(f'/api/v1/products/{self.licence.pk}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['current_stock'])
        self.assertFalse(response.data['is_low_stock'])
        self.assertFalse(response.data['is_out_of_stock'])
        self.assertEqual(response.data['kind'], Product.DIGITAL)
        self.assertFalse(response.data['track_inventory'])

    def test_tracked_products_report_as_before(self):
        response = self.client.get(f'/api/v1/products/{self.out.pk}/')
        self.assertEqual(response.data['current_stock'], 0)
        self.assertTrue(response.data['is_out_of_stock'])
        self.assertEqual(response.data['kind'], Product.PHYSICAL)
        self.assertTrue(response.data['track_inventory'])

    def test_stock_filters_never_call_an_untracked_product_low_or_out(self):
        self.assertEqual(self._skus('stock=out'), ['OUT-1'])
        self.assertEqual(self._skus('stock=low'), ['LOW-1'])
        # "ok" is what the order workflows offer as sellable.
        self.assertEqual(self._skus('stock=ok'), ['LIC-12M', 'OK-1'])

    def test_kind_and_tracking_can_be_filtered(self):
        self.assertEqual(self._skus('kind=digital'), ['LIC-12M'])
        self.assertEqual(self._skus('track_inventory=false'), ['LIC-12M'])

    def test_an_untracked_service_can_be_created(self):
        category = self.plenty.category
        response = self.client.post('/api/v1/products/', {
            'sku': 'SVC-SETUP', 'name': 'Setup service', 'category_id': category.pk,
            'unit_price': '25.00', 'kind': Product.SERVICE, 'track_inventory': False,
            'external_image_url': 'https://cdn.example.com/svc.png',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(response.data['current_stock'])
        product = Product.objects.get(sku='SVC-SETUP')
        self.assertEqual(product.kind, Product.SERVICE)
        self.assertFalse(product.track_inventory)

    def test_existing_clients_get_physical_tracked_products_by_default(self):
        response = self.client.post('/api/v1/products/', {
            'sku': 'NEW-1', 'name': 'New', 'category_id': self.plenty.category.pk,
            'unit_price': '5.00', 'external_image_url': 'https://cdn.example.com/n.png',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['kind'], Product.PHYSICAL)
        self.assertTrue(response.data['track_inventory'])
        self.assertEqual(response.data['current_stock'], 0)

    def test_the_dashboard_does_not_count_untracked_products_as_low(self):
        response = self.client.get('/api/v1/dashboard/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['low_stock_count'], 2)  # LOW-1 and OUT-1


class UntrackedAdjustmentTests(UntrackedCatalogFixture):
    def test_an_untracked_product_cannot_be_adjusted(self):
        response = self.client.post('/api/v1/inventory/adjust/', {
            'product_id': self.licence.pk, 'quantity': 5,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'inventory_not_tracked')
        self.assertFalse(self.licence.inventory_movements.exists())

    def test_bulk_adjustment_refuses_only_the_untracked_entry(self):
        response = self.client.post('/api/v1/inventory/bulk-adjust/', {
            'adjustments': [
                {'product_id': self.plenty.pk, 'quantity': 3},
                {'product_id': self.licence.pk, 'quantity': 3},
            ],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['succeeded']), 1)
        self.assertEqual(response.data['failed'], [{
            'product_id': self.licence.pk,
            'error': 'LIC-12M does not track inventory, so its stock cannot be adjusted.',
            'code': 'inventory_not_tracked',
        }])
        self.assertFalse(self.licence.inventory_movements.exists())


class UntrackedOrderFlowTests(UntrackedCatalogFixture):
    def _mixed_order(self):
        customer = make_customer()
        created = self.client.post('/api/v1/orders/', {
            'customer_id': customer.pk,
            'items': [
                {'product_id': self.plenty.pk, 'quantity': 2},
                {'product_id': self.licence.pk, 'quantity': 25},
            ],
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        order_id = created.data['id']
        self.assertEqual(self.client.post(f'/api/v1/orders/{order_id}/submit/').status_code, 200)
        return order_id

    def _movements(self, order_id, movement_type):
        return sorted(
            InventoryMovement.objects
            .filter(reference_id=order_id, reference_type=InventoryMovement.SALES_ORDER,
                    movement_type=movement_type)
            .values_list('product__sku', 'quantity')
        )

    def test_a_mixed_order_confirms_and_cancels_through_the_ledger(self):
        order_id = self._mixed_order()

        confirm = self.client.post(f'/api/v1/orders/{order_id}/confirm/')
        self.assertEqual(confirm.status_code, status.HTTP_200_OK, confirm.data)
        self.assertEqual(self._movements(order_id, InventoryMovement.SALE), [('OK-1', -2)])

        cancel = self.client.post(f'/api/v1/orders/{order_id}/cancel/')
        self.assertEqual(cancel.status_code, status.HTTP_200_OK, cancel.data)
        self.assertEqual(cancel.data['status'], SalesOrder.CANCELLED)
        self.assertEqual(self._movements(order_id, InventoryMovement.RETURN), [('OK-1', 2)])
        self.assertEqual(Product.objects.get(pk=self.plenty.pk).current_stock, 10)

    def test_cancelling_twice_reports_the_wrong_status(self):
        order_id = self._mixed_order()
        self.client.post(f'/api/v1/orders/{order_id}/confirm/')
        self.client.post(f'/api/v1/orders/{order_id}/cancel/')

        again = self.client.post(f'/api/v1/orders/{order_id}/cancel/')

        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(again.data['code'], 'wrong_status')
        self.assertEqual(len(self._movements(order_id, InventoryMovement.RETURN)), 1)


class KioskHidesUntrackedTests(APITestCase):
    def setUp(self):
        _station, self.raw_key = make_kiosk_station()
        self.auth = {'HTTP_AUTHORIZATION': f'KioskKey {self.raw_key}'}
        self.shelf = make_product(sku='SHELF-1', name='Shelf item', stock=5)
        self.licence = make_product(
            sku='SHELF-LIC', name='Shelf licence', stock=0,
            kind=Product.DIGITAL, track_inventory=False,
        )
        self.made_to_order = make_product(
            sku='SHELF-MTO', name='Shelf made to order', stock=0, track_inventory=False,
        )
        self.limited_digital = make_product(
            sku='SHELF-DIG', name='Shelf limited digital', stock=5, kind=Product.DIGITAL,
        )
        self.hidden = [self.licence, self.made_to_order, self.limited_digital]

    def test_search_shows_only_tracked_physical_products(self):
        response = self.client.get('/api/v1/kiosk/products/?search=Shelf', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row['sku'] for row in response.data['results']], ['SHELF-1'])

    def test_detail_and_scan_do_not_find_them(self):
        for product in self.hidden:
            detail = self.client.get(f'/api/v1/kiosk/products/{product.pk}/', **self.auth)
            scan = self.client.get(f'/api/v1/kiosk/product/{product.sku}/', **self.auth)
            self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND, product.sku)
            self.assertEqual(scan.status_code, status.HTTP_404_NOT_FOUND, product.sku)

    def test_checkout_refuses_them_as_an_invalid_product(self):
        customer = make_customer()
        response = self.client.post('/api/v1/kiosk/checkout/', {
            'customer_id': customer.pk,
            'items': [{'sku': 'SHELF-1', 'quantity': 1}, {'sku': 'SHELF-LIC', 'quantity': 1}],
            'payment_method': Payment.CARD,
            'payment_reference': 'CARD-1',
        }, format='json', **self.auth)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'invalid_product')
        self.assertIn('SHELF-LIC', response.data['error'])
        self.assertFalse(SalesOrder.objects.exists())
