"""
Rules that used to hold only in the browser.

AUDIT_CLIENT_SIDE_RULES.md found four rules enforced in the order form or in a
single endpoint but nowhere else, so the API, MCP and the kiosk could each
violate them. These tests pin each rule on the surfaces that previously had no
check, so a future change cannot quietly re-open the gap.
"""
from decimal import Decimal

from django.db.utils import IntegrityError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Customer, Role, SalesOrder, SalesOrderItem
from api.tests.helpers import (
    auth_client,
    make_customer,
    make_kiosk_station,
    make_order,
    make_product,
    make_user,
)


class OrderPriceAuthorityAPITests(APITestCase):
    """The catalogue owns the price. A client cannot supply or influence one."""

    def setUp(self):
        self.user = make_user(Role.MANAGER)
        auth_client(self.client, self.user)
        self.customer = make_customer()
        self.product = make_product(stock=10, unit_price='100.00')

    def _create(self, **overrides):
        payload = {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': 1}],
        }
        payload.update(overrides)
        return self.client.post('/api/v1/orders/', payload, format='json')

    def test_supplying_a_unit_price_is_rejected(self):
        response = self._create(
            items=[{'product_id': self.product.pk, 'quantity': 1, 'unit_price': '0.01'}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('items', response.data['details'])

    def test_supplying_line_total_or_tax_rate_is_rejected(self):
        for field in ('line_total', 'tax_rate'):
            with self.subTest(field=field):
                response = self._create(
                    items=[{'product_id': self.product.pk, 'quantity': 1, field: '1.00'}]
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_omitting_the_price_uses_the_catalogue(self):
        response = self._create(
            items=[{'product_id': self.product.pk, 'quantity': 2}]
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(str(response.data['items'][0]['unit_price'])), Decimal('100.00'))
        self.assertEqual(Decimal(str(response.data['total_amount'])), Decimal('200.00'))

    def test_a_negative_discount_is_rejected(self):
        response = self._create(discount_amount='-5.00')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('discount_amount', response.data['details'])

    def test_a_huge_discount_cannot_make_the_total_negative(self):
        response = self._create(discount_amount='9999999.00')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertGreaterEqual(Decimal(str(response.data['total_amount'])), Decimal('0.00'))

    def test_the_rule_also_holds_on_update(self):
        """PATCH previously skipped validation entirely when items were sent."""
        order = self._create().data
        response = self.client.patch(
            f"/api/v1/orders/{order['id']}/",
            {'items': [{'product_id': self.product.pk, 'quantity': 1, 'unit_price': '0.01'}]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class OrderDuplicateProductAPITests(APITestCase):
    """One line per product, on every surface that writes an order."""

    def setUp(self):
        self.user = make_user(Role.MANAGER)
        auth_client(self.client, self.user)
        self.customer = make_customer()
        self.product = make_product(stock=10, unit_price='10.00')

    def test_duplicate_product_is_rejected_on_create(self):
        response = self.client.post('/api/v1/orders/', {
            'customer_id': self.customer.pk,
            'items': [
                {'product_id': self.product.pk, 'quantity': 1},
                {'product_id': self.product.pk, 'quantity': 2},
            ],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('more than one line', str(response.data['details']))
        self.assertEqual(SalesOrder.objects.count(), 0)

    def test_duplicate_product_is_rejected_on_update(self):
        order = self.client.post('/api/v1/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': 1}],
        }, format='json').data

        response = self.client.patch(f"/api/v1/orders/{order['id']}/", {
            'items': [
                {'product_id': self.product.pk, 'quantity': 1},
                {'product_id': self.product.pk, 'quantity': 1},
            ],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SalesOrderItem.objects.filter(sales_order_id=order['id']).count(), 1)

    def test_distinct_products_are_accepted(self):
        other = make_product(stock=5, unit_price='7.00')
        response = self.client.post('/api/v1/orders/', {
            'customer_id': self.customer.pk,
            'items': [
                {'product_id': self.product.pk, 'quantity': 1},
                {'product_id': other.pk, 'quantity': 1},
            ],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data['items']), 2)


class OrderLineDatabaseConstraintTests(TestCase):
    """The DB backstop, which also covers the admin inline and the shell."""

    def test_a_second_line_for_the_same_product_is_refused(self):
        order = make_order(status=SalesOrder.DRAFT, quantity=1)
        item = order.items.get()

        with self.assertRaises(IntegrityError):
            SalesOrderItem.objects.create(
                sales_order=order,
                product=item.product,
                quantity=1,
                unit_price=item.unit_price,
                line_total=item.unit_price,
            )


class CustomerNationalIdAPITests(APITestCase):
    """Required, and unique once punctuation and case are stripped."""

    def setUp(self):
        self.user = make_user(Role.MANAGER)
        auth_client(self.client, self.user)

    def _create(self, **overrides):
        payload = {
            'first_name': 'Ana', 'last_name': 'Lopez',
            'email': 'ana@example.com', 'national_id': 'V12345678',
        }
        payload.update(overrides)
        return self.client.post('/api/v1/customers/', payload, format='json')

    def test_national_id_is_required(self):
        response = self._create(national_id=None)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('national_id', response.data['details'])

    def test_an_omitted_national_id_is_rejected(self):
        response = self.client.post('/api/v1/customers/', {
            'first_name': 'Ana', 'last_name': 'Lopez', 'email': 'ana@example.com',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('national_id', response.data['details'])

    def test_it_is_stored_normalized(self):
        response = self._create(national_id='v-12.345.678')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Customer.objects.get(pk=response.data['id']).national_id, 'V12345678')

    def test_a_variant_spelling_is_a_duplicate(self):
        self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)

        response = self._create(national_id='V-12.345.678', email='other@example.com')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('national_id', response.data['details'])
        self.assertEqual(Customer.objects.count(), 1)

    def test_editing_a_customer_does_not_collide_with_itself(self):
        created = self._create().data
        response = self.client.patch(
            f"/api/v1/customers/{created['id']}/",
            {'national_id': 'V-12.345.678'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class KioskNationalIdTests(APITestCase):
    """The kiosk must find the people the back office registered, and vice versa."""

    def setUp(self):
        self.station, raw_key = make_kiosk_station()
        self.headers = {'HTTP_AUTHORIZATION': f'KioskKey {raw_key}'}

    def _identify(self, national_id):
        return self.client.post(
            '/api/v1/kiosk/identify/', {'national_id': national_id},
            format='json', **self.headers,
        )

    def test_identify_finds_a_customer_through_a_punctuation_variant(self):
        customer = make_customer(national_id='V12345678')

        response = self._identify('V-12.345.678')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['customer_id'], customer.pk)

    def test_identify_finds_a_legacy_punctuated_row(self):
        """
        Rows written before IDs were normalized must stay reachable.

        The stored value keeps its punctuation, so an exact match on the
        normalized input misses it; the resolver's SQL-normalized fallback is
        what finds it.
        """
        customer = make_customer(national_id='V99999999')
        Customer.objects.filter(pk=customer.pk).update(national_id='V-9.999.999.9')

        response = self._identify('V99999999')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['customer_id'], customer.pk)

    def test_identify_does_not_500_on_a_legacy_ambiguous_pair(self):
        """
        Two legacy rows can still normalize alike. get() would raise
        MultipleObjectsReturned; the view must resolve deterministically.
        """
        first = make_customer(national_id='V55555555')
        second = make_customer(national_id='TMP-DUPE-1')
        Customer.objects.filter(pk=second.pk).update(national_id='V-55.555.555')

        response = self._identify('V55555555')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['customer_id'], first.pk)

    def test_register_rejects_a_variant_of_an_existing_id(self):
        make_customer(national_id='V77777777')

        response = self.client.post('/api/v1/kiosk/register/', {
            'national_id': 'V-77.777.777',
            'first_name': 'Luis', 'last_name': 'Perez',
            'email': 'luis@example.com', 'phone': '04141234567',
            'date_of_birth': '1990-01-01', 'gender': 'M',
            'state': 'Miranda', 'city': 'Caracas',
        }, format='json', **self.headers)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('national_id', response.data['details'])
