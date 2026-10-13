"""
A store holding its own calls to one customer.

RetailOps scopes orders and entitlements to the channel, because it does not
know the store's customers. A store that names the customer with
`?customer=<external_ref>` is held to that customer too, so a mistake in the
store (the wrong id in a lookup) fails closed with a 404 instead of showing one
customer another's order or licence. Without the parameter nothing changes.
"""
from urllib.parse import urlencode

from rest_framework import status

from api.tests.helpers import make_fulfillment, pay_order
from api.tests.test_storefront_orders import ORDERS, OrderFixture
from core.models import Entitlement, Payment, SalesOrder

ENTITLEMENTS = '/api/v1/storefront/entitlements/'


class ScopeFixture(OrderFixture):
    def setUp(self):
        super().setUp()
        make_fulfillment(self.licence)  # a link, active the moment the order is paid
        self.ana = self.place(client_reference='ANA-1', items=[{'sku': 'LIC-12M', 'quantity': 1}]).data['order_number']
        self.luis = self.place(
            client_reference='LUIS-1',
            customer=self.customer(external_ref='user-43', email='luis@example.com'),
            items=[{'sku': 'LIC-12M', 'quantity': 1}],
        ).data['order_number']
        pay_order(SalesOrder.objects.get(order_number=self.ana))
        self.entitlement = Entitlement.objects.get(order_item__sales_order__order_number=self.ana)

    def get(self, path, **params):
        return self.client.get(path, params, **self.auth)

    def post(self, path, **params):
        query = f'?{urlencode(params)}' if params else ''
        return self.client.post(path + query, {}, format='json', **self.auth)


class OrderScopeTests(ScopeFixture):
    def test_the_right_customer_reads_their_order(self):
        response = self.get(f'{ORDERS}{self.ana}/', customer='user-42')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['order_number'], self.ana)

    def test_another_customers_order_is_a_404(self):
        response = self.get(f'{ORDERS}{self.ana}/', customer='user-43')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_it_is_indistinguishable_from_an_order_that_does_not_exist(self):
        wrong_customer = self.get(f'{ORDERS}{self.ana}/', customer='user-43')
        no_such_order = self.get(f'{ORDERS}SO-NOPE/', customer='user-43')

        self.assertEqual(wrong_customer.status_code, no_such_order.status_code)
        self.assertEqual(wrong_customer.data, no_such_order.data)

    def test_without_the_parameter_nothing_changes(self):
        self.assertEqual(self.get(f'{ORDERS}{self.ana}/').status_code, status.HTTP_200_OK)
        self.assertEqual(self.get(f'{ORDERS}{self.luis}/').status_code, status.HTTP_200_OK)

    def test_naming_nobody_matches_nothing_rather_than_everything(self):
        response = self.get(f'{ORDERS}{self.ana}/', customer='')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_customer_the_channel_never_served_matches_nothing(self):
        response = self.get(f'{ORDERS}{self.ana}/', customer='stranger')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_wrong_customer_cannot_pay_the_order(self):
        before = Payment.objects.count()

        response = self.post(f'{ORDERS}{self.luis}/payments/', customer='user-42')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Payment.objects.count(), before)

    def test_a_wrong_customer_cannot_cancel_the_order(self):
        response = self.post(f'{ORDERS}{self.luis}/cancel/', customer='user-42')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(SalesOrder.objects.get(order_number=self.luis).status, SalesOrder.CONFIRMED)

    def test_the_right_customer_can_cancel_the_order(self):
        response = self.post(f'{ORDERS}{self.luis}/cancel/', customer='user-43')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['customer_status'], 'cancelled')


class EntitlementScopeTests(ScopeFixture):
    def test_the_right_customer_reads_their_entitlement(self):
        response = self.get(f'{ENTITLEMENTS}{self.entitlement.pk}/', customer='user-42')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_another_customers_entitlement_is_a_404(self):
        response = self.get(f'{ENTITLEMENTS}{self.entitlement.pk}/', customer='user-43')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_without_the_parameter_the_channel_still_reads_it(self):
        self.assertEqual(self.get(f'{ENTITLEMENTS}{self.entitlement.pk}/').status_code, status.HTTP_200_OK)

    def test_naming_nobody_matches_no_entitlement(self):
        self.assertEqual(
            self.get(f'{ENTITLEMENTS}{self.entitlement.pk}/', customer='').status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.post(f'{ENTITLEMENTS}{self.entitlement.pk}/reveal/', customer='').status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.entitlement.refresh_from_db()
        self.assertIsNone(self.entitlement.revealed_at)

    def test_another_customer_cannot_reveal_it_and_the_reveal_is_not_recorded(self):
        response = self.post(f'{ENTITLEMENTS}{self.entitlement.pk}/reveal/', customer='user-43')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.entitlement.refresh_from_db()
        self.assertIsNone(self.entitlement.revealed_at)

    def test_the_right_customer_can_reveal_it(self):
        response = self.post(f'{ENTITLEMENTS}{self.entitlement.pk}/reveal/', customer='user-42')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.entitlement.refresh_from_db()
        self.assertIsNotNone(self.entitlement.revealed_at)

    def test_another_customer_cannot_supply_it(self):
        response = self.client.put(
            f'{ENTITLEMENTS}{self.entitlement.pk}/?customer=user-43', {'value': 'https://x.example'},
            format='json', **self.auth,
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
