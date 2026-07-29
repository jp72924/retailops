"""
Regression tests for the CLI/API parity audit fixes.

Each test here pins a defect that was live in the API and reachable from the
CLI. They are grouped in one module because they share a cause — a promise the
API made (in a docstring, a decorator, or a doc) that the implementation did
not keep.
"""

from rest_framework.test import APITestCase

from core.models import Role, SalesOrder
from .helpers import (
    auth_client,
    make_category,
    make_order,
    make_product,
    make_user,
)


class BulkTransitionPermissionTests(APITestCase):
    """
    OrderViewSet.get_permissions() replaces permission_classes wholesale, so the
    IsManagerOrAdmin set on the bulk_transition @action decorator was never
    consulted. Staff could bulk-confirm — deducting stock — through an endpoint
    whose single-order equivalent requires Manager.
    """

    URL = '/api/v1/orders/bulk-transition/'

    def test_staff_cannot_bulk_confirm(self):
        order = make_order(status=SalesOrder.PENDING)
        auth_client(self.client, make_user(Role.STAFF))
        resp = self.client.post(
            self.URL, {'order_ids': [order.pk], 'action': 'confirm'}, format='json',
        )
        self.assertEqual(resp.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.PENDING, 'stock must not have moved')

    def test_staff_cannot_bulk_ship(self):
        order = make_order(status=SalesOrder.PAID)
        auth_client(self.client, make_user(Role.STAFF))
        resp = self.client.post(
            self.URL, {'order_ids': [order.pk], 'action': 'ship'}, format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_still_bulk_confirm(self):
        """The gate must not have closed on the role that is supposed to pass."""
        order = make_order(status=SalesOrder.PENDING)
        auth_client(self.client, make_user(Role.MANAGER))
        resp = self.client.post(
            self.URL, {'order_ids': [order.pk], 'action': 'confirm'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([o['id'] for o in resp.data['succeeded']], [order.pk])

    def test_single_and_bulk_confirm_agree_on_role(self):
        """
        The whole point: the two paths to the same state change must require
        the same role. Staff is refused by both.
        """
        order = make_order(status=SalesOrder.PENDING)
        auth_client(self.client, make_user(Role.STAFF))
        single = self.client.post(f'/api/v1/orders/{order.pk}/confirm/')
        bulk = self.client.post(
            self.URL, {'order_ids': [order.pk], 'action': 'confirm'}, format='json',
        )
        self.assertEqual(single.status_code, 403)
        self.assertEqual(bulk.status_code, single.status_code)


class UserSearchTests(APITestCase):
    """
    UserViewSet declared no search_fields, so SearchFilter was a no-op and
    ?search= returned the full unfiltered list — a result that looked filtered
    and never was.
    """

    URL = '/api/v1/users/'

    def setUp(self):
        self.admin = make_user(Role.ADMIN, email='admin@example.com',
                               first_name='Ada', last_name='Admin')
        make_user(Role.STAFF, email='bob@example.com',
                  first_name='Bob', last_name='Baker')
        make_user(Role.STAFF, email='carol@example.com',
                  first_name='Carol', last_name='Cooper')
        auth_client(self.client, self.admin)

    def test_search_narrows_by_email(self):
        resp = self.client.get(self.URL, {'search': 'bob@example.com'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([u['email'] for u in resp.data['results']], ['bob@example.com'])

    def test_search_narrows_by_first_name(self):
        resp = self.client.get(self.URL, {'search': 'Carol'})
        self.assertEqual([u['email'] for u in resp.data['results']], ['carol@example.com'])

    def test_search_narrows_by_last_name(self):
        resp = self.client.get(self.URL, {'search': 'Baker'})
        self.assertEqual([u['email'] for u in resp.data['results']], ['bob@example.com'])

    def test_search_with_no_match_returns_empty(self):
        """The failure mode was returning everything, so assert on emptiness."""
        resp = self.client.get(self.URL, {'search': 'nobody-by-that-name'})
        self.assertEqual(resp.data['count'], 0)

    def test_unfiltered_list_still_returns_everyone(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.data['count'], 3)


class ProtectedDeleteTests(APITestCase):
    """
    Category and Product are referenced by on_delete=PROTECT relations, and
    custom_exception_handler has no ProtectedError branch — so deleting one
    that was still in use surfaced as a 500 despite the docs promising 409.
    """

    def setUp(self):
        auth_client(self.client, make_user(Role.MANAGER))

    def test_deleting_a_category_with_products_returns_409(self):
        category = make_category()
        make_product(category=category)
        resp = self.client.delete(f'/api/v1/categories/{category.pk}/')
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data['code'], 'conflict')

    def test_deleting_an_empty_category_still_works(self):
        category = make_category()
        resp = self.client.delete(f'/api/v1/categories/{category.pk}/')
        self.assertEqual(resp.status_code, 204)

    def test_deleting_a_product_with_movements_returns_409(self):
        product = make_product(stock=5)  # stock creates an InventoryMovement
        resp = self.client.delete(f'/api/v1/products/{product.pk}/')
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data['code'], 'conflict')

    def test_deleting_a_product_with_order_items_returns_409(self):
        order = make_order()
        product = order.items.first().product
        resp = self.client.delete(f'/api/v1/products/{product.pk}/')
        self.assertEqual(resp.status_code, 409)

    def test_deleting_an_unreferenced_product_still_works(self):
        product = make_product(stock=0)
        resp = self.client.delete(f'/api/v1/products/{product.pk}/')
        self.assertEqual(resp.status_code, 204)


class RecipientProfileFilterTests(APITestCase):
    """
    The viewset declared search_fields and ordering_fields but no filterset,
    and DjangoFilterBackend is global — so ?payment_method= was silently
    ignored rather than rejected.
    """

    URL = '/api/v1/payment-recipient-profiles/'

    def setUp(self):
        self.manager = make_user(Role.MANAGER)
        auth_client(self.client, self.manager)
        self.client.post(self.URL, {
            'payment_method': 'mobile_payment', 'phone': '04121234567',
            'bank': 'BDV', 'document_id': 'V123', 'label': 'Mobile one',
        }, format='json')
        self.client.post(self.URL, {
            'payment_method': 'bank_transfer', 'account_number': '01020304050607080910',
            'bank': 'Banesco', 'document_id': 'V456', 'label': 'Bank one',
        }, format='json')

    def test_filter_by_payment_method(self):
        resp = self.client.get(self.URL, {'payment_method': 'mobile_payment'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [p['payment_method'] for p in resp.data['results']], ['mobile_payment'],
        )

    def test_filter_by_is_active(self):
        resp = self.client.get(self.URL, {'is_active': 'false'})
        self.assertEqual(resp.data['count'], 0)

    def test_unfiltered_returns_both(self):
        self.assertEqual(self.client.get(self.URL).data['count'], 2)
