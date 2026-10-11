"""
What a store sees of the digital delivery of its orders.

What matters: a store reads its orders with every licence key masked, the
whole key comes back only from the one reveal, and that reveal is a POST that
stamps evidence, never cached, never something a prefetch can trigger.
"""
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.storefront.throttling import StorefrontEntitlementThrottle
from api.tests.helpers import (
    channel_auth,
    make_category,
    make_channel,
    make_customer,
    make_fulfillment,
    make_listing,
    make_order,
    make_product,
    make_user,
    pay_order,
    set_currency,
)
from core.models import Entitlement, Product, Role, SalesOrder
from core.services import entitlements as service
from core.services.channel_orders import create_channel_order
from core.services.customer_links import CustomerProfile
from core.services.entitlements import revoke, supply_value
from core.services.orders import refund_order

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
LICENCE = 'ABCD-1234-EFGH-5678'
LINK = 'https://downloads.example.com/setup'
ORDERS = '/api/v1/storefront/orders/'
ENTITLEMENTS = '/api/v1/storefront/entitlements/'

ENTITLEMENT_KEYS = {'id', 'sku', 'kind', 'status', 'seats', 'value', 'activated_at', 'expires_at'}
MASKED = '•' * 8 + '5678'


@override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY])
class DeliveryFixture(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        set_currency()
        self.admin = make_user(Role.ADMIN, is_staff=True)
        self.channel, raw_key = make_channel(admin=self.admin)
        self.auth = channel_auth(raw_key)
        category = make_category('Software')

        self.keyed = make_product(
            sku='KEY-1Y', unit_price='29.00', stock=0, kind=Product.DIGITAL,
            track_inventory=False, category=category,
        )
        make_fulfillment(self.keyed, mode='supplied', kind='key')
        self.linked = make_product(
            sku='LINK-1Y', unit_price='9.00', stock=0, kind=Product.DIGITAL,
            track_inventory=False, category=category,
        )
        make_fulfillment(self.linked, duration_days=30)
        self.box = make_product(sku='BOX-1', unit_price='15.00', stock=10, category=category)
        for product in (self.keyed, self.linked, self.box):
            make_listing(product, slug=product.sku.lower())

    def place(self, *skus, reference='WEB-1', external_ref='user-1', pay=True):
        channel_order, _ = create_channel_order(
            self.channel,
            client_reference=reference,
            profile=CustomerProfile(external_ref, f'{external_ref}@example.com', 'Ana', 'Perez'),
            items=[(sku, 1) for sku in skus],
            fulfillment='digital',
            contact={},
        )
        if pay:
            pay_order(channel_order.sales_order)
        return channel_order

    def order(self, channel_order, auth=None):
        return self.client.get(
            f'{ORDERS}{channel_order.sales_order.order_number}/', **(auth or self.auth),
        )

    def entitlement_of(self, channel_order, sku):
        return Entitlement.objects.get(
            order_item__sales_order=channel_order.sales_order, order_item__product__sku=sku,
        )

    def supplied_key(self, reference='WEB-1'):
        channel_order = self.place('KEY-1Y', reference=reference)
        entitlement = self.entitlement_of(channel_order, 'KEY-1Y')
        supply_value(entitlement.pk, LICENCE)
        return channel_order, Entitlement.objects.get(pk=entitlement.pk)


class OrderRepresentationTests(DeliveryFixture):
    def test_nothing_is_listed_before_the_order_is_paid(self):
        channel_order = self.place('LINK-1Y', pay=False)

        data = self.order(channel_order).data

        self.assertEqual(data['entitlements'], [])
        self.assertEqual(data['customer_status'], 'awaiting_payment')

    def test_fixed_text_is_delivered_at_once_and_the_order_is_complete(self):
        channel_order = self.place('LINK-1Y')

        data = self.order(channel_order).data

        [entitlement] = data['entitlements']
        self.assertEqual(set(entitlement), ENTITLEMENT_KEYS)
        self.assertEqual((entitlement['sku'], entitlement['kind'], entitlement['status']), ('LINK-1Y', 'link', 'active'))
        self.assertEqual((entitlement['value'], entitlement['seats']), (LINK, 1))
        self.assertIsNotNone(entitlement['activated_at'])
        self.assertIsNotNone(entitlement['expires_at'])
        self.assertEqual(data['customer_status'], 'completed')

    def test_a_key_still_to_be_supplied_has_no_value_and_the_order_keeps_processing(self):
        channel_order = self.place('KEY-1Y')

        data = self.order(channel_order).data

        [entitlement] = data['entitlements']
        self.assertEqual((entitlement['status'], entitlement['value']), ('pending', None))
        self.assertEqual(data['customer_status'], 'processing')

    def test_once_the_key_is_supplied_it_shows_masked_and_the_order_completes(self):
        channel_order, _ = self.supplied_key()

        data = self.order(channel_order).data

        [entitlement] = data['entitlements']
        self.assertEqual((entitlement['status'], entitlement['value']), ('active', MASKED))
        self.assertEqual(data['customer_status'], 'completed')

    def test_a_licence_key_is_in_no_response_except_the_reveal(self):
        channel_order, entitlement = self.supplied_key()
        number = channel_order.sales_order.order_number

        bodies = [
            self.order(channel_order).content,
            self.client.get('/api/v1/storefront/customers/user-1/orders/', **self.auth).content,
            self.client.get(f'{ENTITLEMENTS}{entitlement.pk}/', **self.auth).content,
        ]

        for body in bodies:
            text = body.decode()
            for part in (LICENCE, 'ABCD', '1234', 'EFGH'):
                self.assertNotIn(part, text)
            self.assertIn('5678', text)  # only the mask's last four
        self.assertNotIn('hint', ''.join(b.decode() for b in bodies))
        self.assertTrue(number)

    def test_a_physical_line_keeps_the_order_from_being_complete(self):
        channel_order = self.place('LINK-1Y', 'BOX-1')

        data = self.order(channel_order).data

        self.assertEqual([e['sku'] for e in data['entitlements']], ['LINK-1Y'])
        self.assertEqual(data['customer_status'], 'processing')

    def test_a_refund_revokes_the_delivery_and_reads_as_refunded(self):
        channel_order, _ = self.supplied_key()

        refund_order(channel_order.sales_order, self.admin)
        data = self.order(channel_order).data

        self.assertEqual(data['customer_status'], 'refunded')
        self.assertEqual([e['status'] for e in data['entitlements']], ['revoked'])

    def test_revoking_by_hand_does_not_read_as_complete(self):
        channel_order = self.place('LINK-1Y')
        revoke(self.entitlement_of(channel_order, 'LINK-1Y').pk)

        self.assertEqual(self.order(channel_order).data['customer_status'], 'processing')

    def test_a_store_never_sees_another_stores_entitlements(self):
        channel_order = self.place('LINK-1Y')
        _other, other_key = make_channel('other-shop', admin=self.admin)

        response = self.order(channel_order, auth=channel_auth(other_key))

        self.assertEqual(response.status_code, 404)


class SupplyEndpointTests(DeliveryFixture):
    def setUp(self):
        super().setUp()
        self.channel_order = self.place('KEY-1Y')
        self.entitlement = self.entitlement_of(self.channel_order, 'KEY-1Y')
        self.url = f'{ENTITLEMENTS}{self.entitlement.pk}/'

    def put(self, value=LICENCE, url=None, auth=None):
        return self.client.put(url or self.url, {'value': value}, format='json', **(auth or self.auth))

    def test_a_store_supplies_the_key_it_issued(self):
        response = self.put()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(set(response.data), ENTITLEMENT_KEYS)
        self.assertEqual((response.data['status'], response.data['value']), ('active', MASKED))
        self.assertNotIn('ABCD', response.content.decode())
        stored = Entitlement.objects.get(pk=self.entitlement.pk)
        self.assertNotIn('ABCD', stored.value)
        self.assertEqual(service.unseal(stored.value), LICENCE)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_an_entitlement_is_supplied_once(self):
        self.put()

        again = self.put('ZZZZ-9999-YYYY-0000')

        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(again.data['code'], 'entitlement_not_pending')
        self.assertEqual(again.data['entitlement_status'], 'active')
        self.assertEqual(service.unseal(Entitlement.objects.get(pk=self.entitlement.pk).value), LICENCE)

    def test_a_revoked_entitlement_takes_no_value(self):
        revoke(self.entitlement.pk)

        response = self.put()

        self.assertEqual((response.status_code, response.data['code']), (409, 'entitlement_revoked'))

    def test_a_bad_value_is_a_400_and_changes_nothing(self):
        link = self.place('LINK-1Y', reference='WEB-2')
        # A supplied link, to check URL validation through the API.
        supplied_link = make_product(
            sku='LINK-SUP', unit_price='9.00', stock=0, kind=Product.DIGITAL, track_inventory=False,
            category=self.keyed.category,
        )
        make_fulfillment(supplied_link, mode='supplied', kind='link')
        make_listing(supplied_link, slug='link-sup')
        pending_link = self.entitlement_of(self.place('LINK-SUP', reference='WEB-3'), 'LINK-SUP')

        not_a_url = self.put('download it from our site', url=f'{ENTITLEMENTS}{pending_link.pk}/')
        blank = self.put('   ')
        missing = self.client.put(self.url, {}, format='json', **self.auth)

        self.assertEqual((not_a_url.status_code, not_a_url.data['code']), (400, 'invalid_value'))
        self.assertEqual((blank.status_code, blank.data['code']), (400, 'validation_error'))
        self.assertEqual((missing.status_code, missing.data['code']), (400, 'validation_error'))
        self.assertEqual(Entitlement.objects.get(pk=pending_link.pk).status, 'pending')
        self.assertEqual(Entitlement.objects.get(pk=self.entitlement.pk).status, 'pending')
        self.assertTrue(link)

    def test_another_stores_entitlement_is_a_404_and_stays_untouched(self):
        _other, other_key = make_channel('other-shop', admin=self.admin)

        response = self.put(auth=channel_auth(other_key))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Entitlement.objects.get(pk=self.entitlement.pk).status, 'pending')

    def test_a_delivery_on_an_order_that_did_not_come_from_a_channel_is_a_404(self):
        order = make_order(customer=make_customer(), product=self.keyed, status=SalesOrder.CONFIRMED)
        pay_order(order)
        [elsewhere] = Entitlement.objects.filter(order_item__sales_order=order)

        self.assertEqual(self.put(url=f'{ENTITLEMENTS}{elsewhere.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'{ENTITLEMENTS}{elsewhere.pk}/', **self.auth).status_code, 404)

    def test_without_an_encryption_key_a_key_cannot_be_stored(self):
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[]), \
                self.assertLogs('api.storefront.views', level='ERROR'):
            response = self.put()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['code'], 'encryption_not_configured')
        self.assertNotIn('ENTITLEMENT_ENCRYPTION_KEYS', response.content.decode())
        self.assertEqual(Entitlement.objects.get(pk=self.entitlement.pk).status, 'pending')

    def test_the_detail_is_masked_and_never_cached(self):
        self.put()

        response = self.client.get(self.url, **self.auth)

        self.assertEqual(response.data['value'], MASKED)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_supplying_needs_a_channel_key(self):
        self.assertEqual(self.client.put(self.url, {'value': LICENCE}, format='json').status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 401)


class RevealEndpointTests(DeliveryFixture):
    def setUp(self):
        super().setUp()
        self.channel_order, self.entitlement = self.supplied_key()
        self.url = f'{ENTITLEMENTS}{self.entitlement.pk}/reveal/'

    def reveal(self, auth=None):
        return self.client.post(self.url, **(auth or self.auth))

    def test_the_whole_key_comes_back_once_asked_for_and_is_never_cached(self):
        response = self.reveal()

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.data), {'id', 'kind', 'value', 'revealed_at'})
        self.assertEqual((response.data['kind'], response.data['value']), ('key', LICENCE))
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn('Authorization', response['Vary'])

    def test_the_first_reveal_is_stamped_and_a_repeat_keeps_it(self):
        first = self.reveal()
        stamped = Entitlement.objects.get(pk=self.entitlement.pk).revealed_at
        again = self.reveal()

        self.assertIsNotNone(stamped)
        self.assertEqual(first.data['revealed_at'], again.data['revealed_at'])
        self.assertEqual(Entitlement.objects.get(pk=self.entitlement.pk).revealed_at, stamped)

    def test_it_is_not_something_a_prefetch_or_a_link_can_trigger(self):
        for method in ('get', 'head', 'put', 'delete'):
            response = getattr(self.client, method)(self.url, **self.auth)
            self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED, method)
        self.assertIsNone(Entitlement.objects.get(pk=self.entitlement.pk).revealed_at)

    def test_a_link_is_revealed_as_it_is(self):
        linked = self.entitlement_of(self.place('LINK-1Y', reference='WEB-2'), 'LINK-1Y')

        response = self.client.post(f'{ENTITLEMENTS}{linked.pk}/reveal/', **self.auth)

        self.assertEqual((response.data['kind'], response.data['value']), ('link', LINK))

    def test_a_pending_or_revoked_delivery_is_not_revealed_and_nothing_is_stamped(self):
        pending = self.entitlement_of(self.place('KEY-1Y', reference='WEB-2'), 'KEY-1Y')
        revoke(self.entitlement.pk)

        not_yet = self.client.post(f'{ENTITLEMENTS}{pending.pk}/reveal/', **self.auth)
        withdrawn = self.reveal()

        self.assertEqual((not_yet.status_code, not_yet.data['code']), (409, 'entitlement_not_active'))
        self.assertEqual(not_yet.data['entitlement_status'], 'pending')
        self.assertEqual((withdrawn.status_code, withdrawn.data['code']), (409, 'entitlement_revoked'))
        self.assertIsNone(Entitlement.objects.get(pk=pending.pk).revealed_at)
        self.assertIsNone(Entitlement.objects.get(pk=self.entitlement.pk).revealed_at)

    def test_another_store_cannot_reveal_it_and_leaves_no_trace(self):
        _other, other_key = make_channel('other-shop', admin=self.admin)

        response = self.reveal(auth=channel_auth(other_key))

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('ABCD', response.content.decode())
        self.assertIsNone(Entitlement.objects.get(pk=self.entitlement.pk).revealed_at)

    def test_a_key_that_cannot_be_decrypted_is_a_503_and_is_not_stamped(self):
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[OTHER_KEY]), \
                self.assertLogs('api.storefront.views', level='ERROR'):
            response = self.reveal()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['code'], 'decryption_failed')
        self.assertNotIn('ABCD', response.content.decode())
        self.assertIsNone(Entitlement.objects.get(pk=self.entitlement.pk).revealed_at)

    def test_revealing_is_limited_per_store_and_apart_from_reading(self):
        _other, other_key = make_channel('other-shop', admin=self.admin)
        with patch.dict(StorefrontEntitlementThrottle.THROTTLE_RATES, {'storefront_entitlement': '2/min'}):
            codes = [self.reveal().status_code for _ in range(3)]
            other = self.client.post(self.url, **channel_auth(other_key))
            reading = self.order(self.channel_order)

        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(other.status_code, 404)  # reached the view: not throttled by the first store's count
        self.assertEqual(reading.status_code, 200)


class QueryCostTests(DeliveryFixture):
    def test_listing_orders_costs_the_same_with_or_without_deliveries(self):
        def cost():
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get('/api/v1/storefront/customers/user-1/orders/', **self.auth)
            self.assertEqual(response.status_code, 200)
            return len(queries)

        for index in range(2):
            self.place('LINK-1Y', 'KEY-1Y', reference=f'A-{index}')
        small = cost()
        for index in range(2, 9):
            self.place('LINK-1Y', 'KEY-1Y', reference=f'A-{index}')

        self.assertEqual(cost(), small)
        self.assertLessEqual(small, 9)
