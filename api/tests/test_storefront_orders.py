"""
Creating orders through a sales channel, tying the store's customers to
RetailOps customers, and the queries a store makes about its orders.

What matters: the same client_reference is the same order, a failed order
leaves nothing behind (no order, no reserved stock, no customer), a channel can
only order what it can see, a customer is never matched on anything but a
verified email the channel is trusted to vouch for, and a channel only ever
reaches its own orders.
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.tests.helpers import (
    channel_auth,
    make_category,
    make_channel,
    make_customer,
    make_listing,
    make_product,
    make_user,
    set_currency,
)
from core.models import (
    SequenceCounter,
    ChannelCustomerLink,
    ChannelOrder,
    Customer,
    InventoryMovement,
    Payment,
    Product,
    RecipientProfile,
    Role,
    SalesChannel,
    SalesOrder,
)
from core.services import channel_orders as channel_order_service
from core.services.channel_orders import customer_status, review_status
from core.services.customer_links import CustomerEmailConflict, CustomerProfile, link_customer

ORDERS = '/api/v1/storefront/orders/'

ORDER_KEYS = {
    'order_number', 'client_reference', 'customer_status', 'created_at', 'expires_at',
    'fulfillment', 'contact', 'items', 'total', 'amount_paid', 'amount_outstanding',
    'quote', 'payments',
}


class OrderFixture(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        set_currency()  # 50 Bs per USD
        self.admin = make_user(Role.ADMIN, is_staff=True)
        self.channel, raw_key = make_channel(admin=self.admin)
        self.auth = channel_auth(raw_key)

        category = make_category('Software')
        self.shelf = make_product(sku='BOX-1', unit_price='10.00', stock=10, category=category)
        self.licence = make_product(
            sku='LIC-12M', unit_price='29.00', stock=0, kind=Product.DIGITAL,
            track_inventory=False, category=category,
        )
        make_listing(self.shelf, slug='box')
        make_listing(self.licence, slug='licence')
        RecipientProfile.objects.create(
            payment_method=RecipientProfile.MOBILE_PAYMENT, phone='0414-1234567',
            bank='Banco de Venezuela', document_id='J-12345678-9',
            is_primary=True, created_by=self.admin,
        )

    def customer(self, **overrides):
        data = {
            'external_ref': 'user-42', 'email': 'ana@example.com', 'email_verified': True,
            'first_name': 'Ana', 'last_name': 'Pérez', 'phone': '0414-5550000',
        }
        data.update(overrides)
        return data

    def body(self, **overrides):
        body = {
            'client_reference': 'WEB-1001',
            'customer': self.customer(),
            'items': [{'sku': 'BOX-1', 'quantity': 1}],
            'fulfillment': 'pickup',
            'contact': {'phone': '0414-5550000'},
        }
        body.update(overrides)
        return body

    def place(self, auth=None, **overrides):
        return self.client.post(ORDERS, self.body(**overrides), format='json', **(auth or self.auth))

    def stock(self, product):
        return Product.objects.get(pk=product.pk).current_stock

    def nothing_was_created(self):
        self.assertEqual(SalesOrder.objects.count(), 0)
        self.assertFalse(InventoryMovement.objects.filter(movement_type=InventoryMovement.SALE).exists())
        self.assertEqual(ChannelOrder.objects.count(), 0)


class CreateOrderTests(OrderFixture):
    def test_an_order_is_created_confirmed_and_quoted(self):
        before = timezone.now()
        response = self.place()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        data = response.data
        self.assertEqual(set(data), ORDER_KEYS | {'payment_instructions'})
        self.assertEqual(data['customer_status'], 'awaiting_payment')
        self.assertEqual(data['client_reference'], 'WEB-1001')
        self.assertEqual((data['total'], data['amount_paid'], data['amount_outstanding']),
                         ('10.00', '0.00', '10.00'))
        self.assertEqual(data['items'], [
            {'sku': 'BOX-1', 'name': self.shelf.name, 'quantity': 1,
             'unit_price': '10.00', 'line_total': '10.00'},
        ])
        self.assertEqual(data['quote']['total'], '10.00')
        self.assertEqual(data['quote']['secondary_total'], '500.00')  # 10.00 at 50 Bs
        self.assertEqual(data['quote']['currency']['secondary']['rate'], '50.00000000')
        self.assertEqual(data['payments'], [])
        self.assertEqual(
            [p['payment_method'] for p in data['payment_instructions']], ['mobile_payment'],
        )

        order = SalesOrder.objects.get()
        self.assertEqual(order.status, SalesOrder.CONFIRMED)
        self.assertEqual(order.created_by, self.channel.service_user)
        self.assertEqual(order.confirmed_by, self.channel.service_user)
        self.assertEqual(data['order_number'], order.order_number)
        channel_order = ChannelOrder.objects.get()
        self.assertEqual((channel_order.channel, channel_order.fulfillment), (self.channel, 'pickup'))
        self.assertEqual(channel_order.contact, {'phone': '0414-5550000'})
        self.assertGreaterEqual(channel_order.expires_at, before + timedelta(minutes=59))
        self.assertLessEqual(channel_order.expires_at, timezone.now() + timedelta(minutes=61))

    def test_stock_is_reserved_for_tracked_lines_only(self):
        self.place(items=[{'sku': 'BOX-1', 'quantity': 3}, {'sku': 'LIC-12M', 'quantity': 5}])

        sales = InventoryMovement.objects.filter(movement_type=InventoryMovement.SALE)
        self.assertEqual(list(sales.values_list('product__sku', 'quantity')), [('BOX-1', -3)])
        self.assertEqual(self.stock(self.shelf), 7)
        self.assertFalse(self.licence.inventory_movements.exists())

    def test_products_are_locked_before_an_order_number_is_drawn(self):
        # The kiosk locks products and then draws a number from the counter row;
        # taking them in the other order here could deadlock the two.
        sequence = []
        real_lock = channel_order_service.lock_products
        real_next = SequenceCounter.next_value

        def lock(product_ids):
            product_ids = list(product_ids)
            sequence.append(('lock', product_ids))
            return real_lock(product_ids)

        def next_value(prefix):
            sequence.append(('counter', None))
            return real_next(prefix)

        with patch.object(channel_order_service, 'lock_products', side_effect=lock), \
                patch.object(SequenceCounter, 'next_value', side_effect=next_value):
            response = self.place(items=[{'sku': 'BOX-1', 'quantity': 2}])

        self.assertEqual(response.status_code, 201)
        self.assertEqual([step[0] for step in sequence], ['lock', 'counter'])
        self.assertEqual(sequence[0][1], [self.shelf.pk])

    def test_a_digital_only_order_takes_no_product_locks(self):
        with patch('core.services.channel_orders.lock_products') as lock, \
                patch('core.services.inventory.lock_products') as inner:
            response = self.place(items=[{'sku': 'LIC-12M', 'quantity': 2}], fulfillment='digital')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(list(lock.call_args.args[0]), [])
        inner.assert_not_called()

    def test_repeated_lines_are_merged(self):
        response = self.place(items=[
            {'sku': 'BOX-1', 'quantity': 2}, {'sku': 'LIC-12M', 'quantity': 1},
            {'sku': 'BOX-1', 'quantity': 3},
        ])

        self.assertEqual(
            [(i['sku'], i['quantity']) for i in response.data['items']],
            [('BOX-1', 5), ('LIC-12M', 1)],
        )
        self.assertEqual(response.data['total'], '79.00')

    def test_the_expiry_follows_the_channel_setting(self):
        SalesChannel.objects.filter(pk=self.channel.pk).update(order_expiry_minutes=15)
        self.channel.refresh_from_db()

        self.place()

        expires = ChannelOrder.objects.get().expires_at
        self.assertLess(expires, timezone.now() + timedelta(minutes=16))

    def test_the_quote_does_not_follow_the_live_rate(self):
        order_number = self.place().data['order_number']

        set_currency(secondary_exchange_rate='61.5')
        quote = self.client.get(f'{ORDERS}{order_number}/', **self.auth).data['quote']

        self.assertEqual(quote['secondary_total'], '500.00')
        self.assertEqual(quote['currency']['secondary']['rate'], '50.00000000')


class IdempotencyTests(OrderFixture):
    def test_the_same_reference_is_the_same_order(self):
        first = self.place()
        again = self.place()

        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(again.data['order_number'], first.data['order_number'])
        self.assertEqual(SalesOrder.objects.count(), 1)
        # Stock was reserved once, not twice.
        self.assertEqual(self.stock(self.shelf), 9)
        self.assertEqual(InventoryMovement.objects.filter(movement_type=InventoryMovement.SALE).count(), 1)

    def test_a_replay_does_not_need_the_stock_that_is_gone(self):
        self.place(items=[{'sku': 'BOX-1', 'quantity': 10}])  # takes all of it

        again = self.place(items=[{'sku': 'BOX-1', 'quantity': 10}])

        self.assertEqual(again.status_code, 200)

    def test_a_reference_reused_for_a_different_cart_is_refused(self):
        self.place()

        different_cart = self.place(items=[{'sku': 'BOX-1', 'quantity': 2}])
        different_customer = self.place(customer=self.customer(external_ref='user-99'))

        for response in (different_cart, different_customer):
            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(response.data['code'], 'client_reference_conflict')
        self.assertEqual(SalesOrder.objects.count(), 1)

    def test_each_channel_has_its_own_references(self):
        _other, other_key = make_channel('other-shop', admin=self.admin)

        mine = self.place()
        theirs = self.place(auth=channel_auth(other_key), customer=self.customer(email='ana@other.example'))

        self.assertEqual((mine.status_code, theirs.status_code), (201, 201))
        self.assertNotEqual(mine.data['order_number'], theirs.data['order_number'])

    def test_a_lost_race_gets_the_winners_order_and_reserves_nothing_twice(self):
        winner = self.place()
        real_existing = channel_order_service._existing
        calls = []

        def stale_first_look(channel, client_reference):
            # The loser looked before the winner committed.
            calls.append(1)
            return None if len(calls) == 1 else real_existing(channel, client_reference)

        with patch.object(channel_order_service, '_existing', side_effect=stale_first_look):
            loser = self.place()

        self.assertEqual(loser.status_code, 200)
        self.assertEqual(loser.data['order_number'], winner.data['order_number'])
        self.assertEqual(SalesOrder.objects.count(), 1)
        self.assertEqual(self.stock(self.shelf), 9)


class RefusedOrderTests(OrderFixture):
    def test_insufficient_stock_is_refused_without_revealing_the_count(self):
        response = self.place(items=[{'sku': 'BOX-1', 'quantity': 11}, {'sku': 'LIC-12M', 'quantity': 1}])

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'insufficient_stock')
        self.assertEqual(response.data['insufficient'], [{'sku': 'BOX-1', 'requested': 11}])
        self.assertNotIn('available', response.content.decode())
        self.nothing_was_created()
        self.assertEqual(self.stock(self.shelf), 10)

    def test_a_refused_order_leaves_no_customer_behind(self):
        self.place(items=[{'sku': 'BOX-1', 'quantity': 11}])

        self.assertFalse(Customer.objects.filter(email='ana@example.com').exists())
        self.assertEqual(ChannelCustomerLink.objects.count(), 0)

    def test_only_products_in_a_published_listing_can_be_ordered(self):
        internal = make_product(sku='INTERNAL', stock=5)                  # in no listing
        draft = make_product(sku='DRAFT', stock=5)
        make_listing(draft, slug='draft', published=False)
        withdrawn = make_product(sku='GONE', stock=5, is_active=False)
        make_listing(withdrawn, slug='gone')

        for sku in ('INTERNAL', 'DRAFT', 'GONE', 'NOPE'):
            response = self.place(items=[{'sku': 'BOX-1', 'quantity': 1}, {'sku': sku, 'quantity': 1}])
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, sku)
            self.assertEqual(response.data['code'], 'invalid_product', sku)
            self.assertIn(sku, response.data['error'])
        self.nothing_was_created()
        self.assertEqual(self.stock(internal), 5)

    def test_malformed_requests_are_validation_errors(self):
        cases = {
            'no items': {'items': []},
            'zero quantity': {'items': [{'sku': 'BOX-1', 'quantity': 0}]},
            'huge quantity': {'items': [{'sku': 'LIC-12M', 'quantity': 1001}]},
            'too many lines': {'items': [{'sku': f'S{i}', 'quantity': 1} for i in range(51)]},
            'bad fulfillment': {'fulfillment': 'drone'},
            'no reference': {'client_reference': ''},
            'bad email': {'customer': self.customer(email='not-an-email')},
            'bad external ref': {'customer': self.customer(external_ref='has space/slash')},
            'contact not strings': {'contact': {'phone': ['1']}},
            'contact too long': {'contact': {'note': 'x' * 256}},
            'contact too many': {'contact': {f'k{i}': 'v' for i in range(11)}},
        }
        for name, overrides in cases.items():
            response = self.place(**overrides)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, name)
            self.assertEqual(response.data['code'], 'validation_error', name)
        self.nothing_was_created()

    def test_merged_quantity_over_the_limit_is_refused(self):
        response = self.place(items=[
            {'sku': 'LIC-12M', 'quantity': 600}, {'sku': 'LIC-12M', 'quantity': 600},
        ])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'invalid_quantity')
        self.nothing_was_created()


class CustomerLinkingTests(OrderFixture):
    def test_a_new_customer_is_created_with_a_normalised_email_and_linked(self):
        self.place(customer=self.customer(email='Ana@Example.COM'))

        customer = Customer.objects.get()
        self.assertEqual((customer.email, customer.first_name, customer.phone),
                         ('ana@example.com', 'Ana', '0414-5550000'))
        self.assertIsNone(customer.national_id)
        link = ChannelCustomerLink.objects.get()
        self.assertEqual((link.channel, link.external_ref, link.customer), (self.channel, 'user-42', customer))
        self.assertEqual(link.profile['email'], 'Ana@Example.COM')

    def test_an_existing_link_decides_and_never_rewrites_the_customer(self):
        self.place()
        customer = Customer.objects.get()

        self.place(
            client_reference='WEB-1002',
            customer=self.customer(email='other@example.com', first_name='Changed', phone='1'),
        )

        self.assertEqual(Customer.objects.count(), 1)
        customer.refresh_from_db()
        self.assertEqual((customer.email, customer.first_name), ('ana@example.com', 'Ana'))
        self.assertEqual(SalesOrder.objects.filter(customer=customer).count(), 2)
        # What the channel last said is kept, for support, on the link only.
        self.assertEqual(ChannelCustomerLink.objects.get().profile['first_name'], 'Changed')

    def test_an_email_of_an_existing_customer_is_a_conflict_that_reveals_nothing(self):
        make_customer(first_name='Rosa', last_name='Salazar', email='ana@example.com',
                      phone='0412-9998888', national_id='V99887766')

        response = self.place()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'customer_email_conflict')
        body = response.content.decode()
        for secret in ('Rosa', 'Salazar', '0412-9998888', 'V99887766'):
            self.assertNotIn(secret, body)
        self.nothing_was_created()
        self.assertEqual(ChannelCustomerLink.objects.count(), 0)

    def test_a_trusted_channel_links_a_verified_email_without_touching_the_customer(self):
        existing = make_customer(first_name='Rosa', last_name='Salazar', email='Ana@Example.com')
        SalesChannel.objects.filter(pk=self.channel.pk).update(trusts_email_verification=True)

        response = self.place()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Customer.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual((existing.first_name, existing.last_name), ('Rosa', 'Salazar'))
        self.assertEqual(SalesOrder.objects.get().customer, existing)
        self.assertEqual(ChannelCustomerLink.objects.get().customer, existing)

    def test_trust_needs_both_a_trusting_channel_and_a_verified_email(self):
        make_customer(email='ana@example.com')

        unverified = self.place(customer=self.customer(email_verified=False))
        SalesChannel.objects.filter(pk=self.channel.pk).update(trusts_email_verification=True)
        still_unverified = self.place(customer=self.customer(email_verified=False))

        for response in (unverified, still_unverified):
            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(response.data['code'], 'customer_email_conflict')
        self.nothing_was_created()

        SalesChannel.objects.filter(pk=self.channel.pk).update(trusts_email_verification=False)
        untrusted = self.place()  # verified, but the channel is not trusted
        self.assertEqual(untrusted.status_code, status.HTTP_409_CONFLICT)

    def test_a_national_id_never_links_a_customer(self):
        existing = make_customer(email='rosa@example.com', national_id='V12345678')

        response = self.place(customer={**self.customer(), 'national_id': 'V-12.345.678'})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Customer.objects.count(), 2)
        new = ChannelCustomerLink.objects.get().customer
        self.assertNotEqual(new, existing)
        self.assertIsNone(new.national_id)
        existing.refresh_from_db()
        self.assertEqual(existing.national_id, 'V12345678')

    def test_two_store_accounts_on_one_customer_see_only_their_own_orders(self):
        make_customer(email='shared@example.com')
        SalesChannel.objects.filter(pk=self.channel.pk).update(trusts_email_verification=True)
        mine = self.customer(external_ref='user-1', email='shared@example.com')
        theirs = self.customer(external_ref='user-2', email='shared@example.com')
        self.place(client_reference='A', customer=mine)
        self.place(client_reference='B', customer=theirs)

        listed = lambda ref: [  # noqa: E731
            o['client_reference']
            for o in self.client.get(f'/api/v1/storefront/customers/{ref}/orders/', **self.auth).data['results']
        ]

        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual((listed('user-1'), listed('user-2')), (['A'], ['B']))

    def test_losing_a_race_to_create_the_same_customer_finds_the_winners(self):
        channel = SalesChannel.objects.get()
        profile = CustomerProfile('user-42', 'ana@example.com', 'Ana', 'Pérez')
        winner = link_customer(channel, profile)
        real_filter_link = ChannelCustomerLink.objects.filter
        real_filter_customer = Customer.objects.filter
        blind = {'link': 1, 'customer': 1}

        def blind_once(real, key):
            def lookup(*args, **kwargs):
                if blind[key]:
                    blind[key] -= 1
                    return real(pk__in=[])  # the winner had not committed yet
                return real(*args, **kwargs)
            return lookup

        with patch.object(ChannelCustomerLink.objects, 'filter', blind_once(real_filter_link, 'link')), \
                patch.object(Customer.objects, 'filter', blind_once(real_filter_customer, 'customer')):
            found = link_customer(channel, profile)

        self.assertEqual(found.pk, winner.pk)
        self.assertEqual(Customer.objects.count(), 1)

    def test_a_conflict_is_a_domain_error_the_service_raises(self):
        make_customer(email='ana@example.com')
        with self.assertRaises(CustomerEmailConflict):
            link_customer(self.channel, CustomerProfile('u', 'ana@example.com', 'A', 'B', email_verified=True))


class OrderQueryTests(OrderFixture):
    def test_an_order_reads_with_exactly_the_documented_fields(self):
        order_number = self.place().data['order_number']

        response = self.client.get(f'{ORDERS}{order_number}/', **self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), ORDER_KEYS)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_nothing_about_the_customer_is_ever_returned(self):
        order_number = self.place().data['order_number']

        for path in (f'{ORDERS}{order_number}/', '/api/v1/storefront/customers/user-42/orders/'):
            body = self.client.get(path, **self.auth).content.decode()
            for private in ('ana@example.com', 'Pérez', 'P\\u00e9rez', 'national_id'):
                self.assertNotIn(private, body, path)
        created = self.place(client_reference='WEB-2').content.decode()
        self.assertNotIn('ana@example.com', created)

    def test_another_channels_order_is_a_404(self):
        order_number = self.place().data['order_number']
        _other, other_key = make_channel('other-shop', admin=self.admin)

        theirs = self.client.get(f'{ORDERS}{order_number}/', **channel_auth(other_key))
        missing = self.client.get(f'{ORDERS}SO-00000000-0000/', **self.auth)

        self.assertEqual(theirs.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(theirs.data, missing.data)  # indistinguishable

    def test_a_customers_orders_are_theirs_alone_newest_first_and_paged(self):
        for index in range(3):
            self.place(client_reference=f'A-{index}', items=[{'sku': 'LIC-12M', 'quantity': 1}])
        self.place(client_reference='B-0', customer=self.customer(external_ref='user-7', email='bo@example.com'),
                   items=[{'sku': 'LIC-12M', 'quantity': 1}])

        page = self.client.get('/api/v1/storefront/customers/user-42/orders/', {'page_size': 2}, **self.auth).data

        self.assertEqual(page['count'], 3)
        self.assertEqual([o['client_reference'] for o in page['results']], ['A-2', 'A-1'])
        self.assertIsNotNone(page['next'])

    def test_an_unknown_customer_has_no_orders_not_an_error(self):
        response = self.client.get('/api/v1/storefront/customers/nobody/orders/', **self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['count'], response.data['results']), (0, []))

    def test_another_channels_customer_reference_is_not_visible(self):
        self.place()
        _other, other_key = make_channel('other-shop', admin=self.admin)

        response = self.client.get('/api/v1/storefront/customers/user-42/orders/', **channel_auth(other_key))

        self.assertEqual(response.data['results'], [])

    def test_listing_orders_costs_the_same_however_many_there_are(self):
        def cost():
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get('/api/v1/storefront/customers/user-42/orders/', **self.auth)
            self.assertEqual(response.status_code, 200)
            return len(queries)

        for index in range(2):
            self.place(client_reference=f'N-{index}', items=[{'sku': 'LIC-12M', 'quantity': 1}])
        small = cost()
        for index in range(2, 9):
            self.place(client_reference=f'N-{index}', items=[{'sku': 'LIC-12M', 'quantity': 1}])

        self.assertEqual(cost(), small)
        self.assertLessEqual(small, 8)

    def test_the_orders_endpoints_are_the_channels_alone(self):
        order_number = self.place().data['order_number']
        for path in (ORDERS, f'{ORDERS}{order_number}/', f'{ORDERS}{order_number}/payments/',
                     '/api/v1/storefront/customers/user-42/orders/'):
            self.assertEqual(self.client.get(path).status_code, 401, path)
            self.assertEqual(self.client.post(path, {}, format='json').status_code, 401, path)


class StatusRuleTests(SimpleTestCase):
    """customer_status and review_status are pure: the same input, the same words."""

    def order(self, state):
        return SimpleNamespace(status=state)

    def payments(self, *states):
        return [SimpleNamespace(status=s) for s in states]

    def test_what_a_customer_is_told_for_every_order_state(self):
        expected = {
            SalesOrder.DRAFT: 'awaiting_payment',
            SalesOrder.PENDING: 'awaiting_payment',
            SalesOrder.CONFIRMED: 'awaiting_payment',
            SalesOrder.PAID: 'processing',
            SalesOrder.SHIPPED: 'processing',
            SalesOrder.DELIVERED: 'completed',
            SalesOrder.CANCELLED: 'cancelled',
            SalesOrder.REFUNDED: 'refunded',
        }
        for state, words in expected.items():
            self.assertEqual(customer_status(self.order(state), []), words, state)

    def test_a_payment_under_review_shows_while_the_order_is_unpaid(self):
        confirmed = self.order(SalesOrder.CONFIRMED)

        self.assertEqual(customer_status(confirmed, self.payments(Payment.PENDING_REVIEW)), 'payment_under_review')
        self.assertEqual(customer_status(confirmed, self.payments(Payment.REJECTED)), 'awaiting_payment')
        self.assertEqual(
            customer_status(confirmed, self.payments(Payment.REJECTED, Payment.PENDING_REVIEW)),
            'payment_under_review',
        )

    def test_a_cancelled_order_stays_cancelled_whatever_was_paid(self):
        cancelled = self.order(SalesOrder.CANCELLED)
        self.assertEqual(customer_status(cancelled, self.payments(Payment.PENDING_REVIEW)), 'cancelled')

    def test_how_each_payment_reads_to_the_customer(self):
        for state, words in {
            Payment.CONFIRMED: 'verified',
            Payment.PENDING_REVIEW: 'needs_review',
            Payment.REJECTED: 'rejected',
        }.items():
            self.assertEqual(review_status(SimpleNamespace(status=state)), words)
