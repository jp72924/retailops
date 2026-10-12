"""
Telling a store what changed: the events an order's changes publish, and the feed they are read from.

What matters: an event is a difference (never repeated, never invented), it is written in the
transaction of the change (a rollback leaves none), the sequence has no gaps and belongs to one channel,
an event carries nothing private, every service that changes an order publishes, and a store reads only
its own channel's feed.
"""
from datetime import timedelta

from cryptography.fernet import Fernet
from django.core.cache import cache
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.tests.helpers import (
    channel_auth,
    make_category,
    make_channel,
    make_channel_order,
    make_fulfillment,
    make_listing,
    make_order,
    make_payment,
    make_product,
    make_user,
    pay_order,
    set_currency,
)
from core.models import (
    ChannelEvent,
    ChannelOrder,
    ChannelWebhook,
    Payment,
    Product,
    Role,
    SalesOrder,
    SequenceCounter,
    WebhookDelivery,
)
from core.services import channel_events
from core.services.channel_events import publish_order_changes
from core.services.channel_orders import cancel_channel_order, expire_channel_orders
from core.services.channel_webhooks import register_webhook
from core.services.entitlements import revoke, supply_value
from core.services.orders import cancel_order, refund_order
from core.services.payments import approve_payment, apply_confirmed_payments, reject_payment

KEY = Fernet.generate_key().decode()
FEED = '/api/v1/storefront/events/'

ORDER_EVENT_KEYS = {'order_number', 'client_reference', 'customer_status', 'reason'}
PAYMENT_EVENT_KEYS = {'order_number', 'payment_number', 'review_status'}
ENTITLEMENT_EVENT_KEYS = {'order_number', 'entitlement_id', 'sku', 'kind', 'status'}


@override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY], CHANNEL_WEBHOOK_ALLOW_INSECURE=True)
class EventFixture(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        set_currency()
        self.admin = make_user(Role.ADMIN, is_staff=True)
        self.channel, self.raw_key = make_channel(admin=self.admin)
        category = make_category('Software')
        self.keyed = make_product(sku='KEY-1Y', unit_price='29.00', stock=0, kind=Product.DIGITAL, track_inventory=False, category=category)
        make_fulfillment(self.keyed, mode='supplied', kind='key')
        self.linked = make_product(sku='LINK-1Y', unit_price='9.00', stock=0, kind=Product.DIGITAL, track_inventory=False, category=category)
        make_fulfillment(self.linked)
        self.box = make_product(sku='BOX-1', unit_price='15.00', stock=10, category=category)
        for product in (self.keyed, self.linked, self.box):
            make_listing(product, slug=product.sku.lower())

    def order(self, product=None, reference='WEB-1', channel=None, **customer):
        return make_channel_order(channel or self.channel, product or self.keyed, reference=reference, fulfillment='digital', **customer)

    def events(self, channel=None):
        return list(ChannelEvent.objects.filter(channel=channel or self.channel).order_by('sequence'))

    def summary(self, channel=None):
        """What the store was told, in order, as (type, the field that says what happened)."""
        said = []
        for event in self.events(channel):
            data = event.data
            if event.event_type == ChannelEvent.ORDER_UPDATED:
                said.append(('order', data['customer_status'], data['reason']))
            elif event.event_type == ChannelEvent.PAYMENT_UPDATED:
                said.append(('payment', data['review_status']))
            else:
                said.append(('entitlement', data['kind'], data['status']))
        return said

    def pending_payment(self, channel_order):
        payment = make_payment(
            order=channel_order.sales_order, user=self.channel.service_user,
            payment_method=Payment.MOBILE_PAYMENT, status=Payment.PENDING_REVIEW, reference_number='000123',
        )
        apply_confirmed_payments(channel_order.sales_order)  # as registering a receipt does
        return payment


class WhatAnOrderPublishesTests(EventFixture):
    def test_a_new_order_says_nothing(self):
        channel_order = self.order()

        self.assertEqual(self.events(), [])
        self.assertEqual(channel_order.published_state, {})

    def test_a_paid_order_tells_the_store_the_payment_the_status_and_what_it_is_owed(self):
        channel_order = self.order()

        pay_order(channel_order.sales_order)

        self.assertEqual(self.summary(), [('payment', 'verified'), ('order', 'processing', 'paid'), ('entitlement', 'key', 'pending')])

    def test_an_order_that_delivers_at_once_is_completed_in_the_same_breath(self):
        channel_order = self.order(self.linked)

        pay_order(channel_order.sales_order)

        self.assertEqual(self.summary(), [('payment', 'verified'), ('order', 'completed', 'delivered'), ('entitlement', 'link', 'active')])

    def test_what_an_event_carries_is_only_what_to_read_again(self):
        channel_order = self.order()
        pay_order(channel_order.sales_order)

        by_type = {event.event_type: event for event in self.events()}

        number = channel_order.sales_order.order_number
        self.assertEqual(set(by_type[ChannelEvent.ORDER_UPDATED].data), ORDER_EVENT_KEYS)
        self.assertEqual(set(by_type[ChannelEvent.PAYMENT_UPDATED].data), PAYMENT_EVENT_KEYS)
        self.assertEqual(set(by_type[ChannelEvent.ENTITLEMENT_UPDATED].data), ENTITLEMENT_EVENT_KEYS)
        for event in by_type.values():
            self.assertEqual(event.data['order_number'], number)
            self.assertEqual(event.order_number, number)
        self.assertEqual(by_type[ChannelEvent.ORDER_UPDATED].data['client_reference'], 'WEB-1')
        self.assertEqual(by_type[ChannelEvent.ENTITLEMENT_UPDATED].data['sku'], 'KEY-1Y')
        everything = str([event.data for event in by_type.values()])
        for private in ('ana@example.com', 'Perez', 'user-1', '29.00', 'amount', 'value', 'transaction', 'ocr'):
            self.assertNotIn(private, everything, private)

    def test_the_same_state_is_never_told_twice(self):
        channel_order = self.order()
        pay_order(channel_order.sales_order)
        told = len(self.events())

        for _ in range(3):
            publish_order_changes(channel_order.sales_order_id)
            apply_confirmed_payments(channel_order.sales_order)

        self.assertEqual(len(self.events()), told)

    def test_a_key_supplied_completes_the_order_and_activates_the_entitlement(self):
        channel_order = self.order()
        pay_order(channel_order.sales_order)
        before = len(self.events())
        entitlement = channel_order.sales_order.items.get().entitlements.get()

        supply_value(entitlement.pk, 'ABCD-1234-EFGH-5678')

        self.assertEqual(self.summary()[before:], [('order', 'completed', 'delivered'), ('entitlement', 'key', 'active')])

    def test_a_receipt_waiting_for_a_person_is_told_and_so_is_its_approval(self):
        channel_order = self.order()

        payment = self.pending_payment(channel_order)
        self.assertEqual(self.summary(), [('payment', 'needs_review'), ('order', 'payment_under_review', 'payment_under_review')])

        approve_payment(payment, user=self.admin)
        self.assertEqual(self.summary()[2:], [('payment', 'verified'), ('order', 'processing', 'paid'), ('entitlement', 'key', 'pending')])

    def test_a_rejected_receipt_puts_the_order_back_to_waiting_for_payment(self):
        channel_order = self.order()
        payment = self.pending_payment(channel_order)

        reject_payment(payment, user=self.admin, notes='Not our account.')

        self.assertEqual(self.summary()[2:], [('payment', 'rejected'), ('order', 'awaiting_payment', 'payment_rejected')])

    def test_cancelling_tells_the_store_it_was_cancelled(self):
        channel_order = self.order()

        cancel_channel_order(channel_order)

        self.assertEqual(self.summary(), [('order', 'cancelled', 'cancelled')])

    def test_an_order_that_lapsed_says_it_expired(self):
        channel_order = self.order()
        ChannelOrder.objects.filter(pk=channel_order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

        expire_channel_orders()

        self.assertEqual(self.summary(), [('order', 'cancelled', 'expired')])

    def test_a_staff_cancellation_is_a_cancellation(self):
        channel_order = self.order()

        cancel_order(channel_order.sales_order, make_user(Role.MANAGER))

        self.assertEqual(self.summary(), [('order', 'cancelled', 'cancelled')])

    def test_a_refund_tells_the_store_and_revokes_what_was_delivered(self):
        channel_order = self.order(self.linked)
        pay_order(channel_order.sales_order)
        before = len(self.events())

        refund_order(channel_order.sales_order, make_user(Role.ADMIN))

        self.assertEqual(self.summary()[before:], [('order', 'refunded', 'refunded'), ('entitlement', 'link', 'revoked')])

    def test_revoking_one_entitlement_is_told(self):
        channel_order = self.order(self.linked)
        pay_order(channel_order.sales_order)
        before = len(self.events())

        revoke(channel_order.sales_order.items.get().entitlements.get().pk)

        self.assertEqual(self.summary()[before:], [('order', 'processing', 'paid'), ('entitlement', 'link', 'revoked')])

    def test_marking_a_physical_order_delivered_is_told_and_shipping_it_is_not_news(self):
        channel_order = self.order(self.box)
        order = channel_order.sales_order
        pay_order(order)
        before = len(self.events())

        order.refresh_from_db()
        order.status = SalesOrder.SHIPPED
        order.save()
        self.assertEqual(len(self.events()), before)  # paid and shipped both read "processing"

        order.status = SalesOrder.DELIVERED
        order.save()
        self.assertEqual(self.summary()[before:], [('order', 'completed', 'delivered')])

    def test_an_order_that_is_not_a_channels_publishes_nothing_and_breaks_nothing(self):
        order = make_order(status=SalesOrder.CONFIRMED)

        pay_order(order)
        order.status = SalesOrder.DELIVERED
        order.save()

        self.assertEqual(ChannelEvent.objects.count(), 0)

    def test_an_order_created_before_events_existed_is_told_from_what_it_was_then(self):
        channel_order = self.order(self.linked)
        ChannelOrder.objects.filter(pk=channel_order.pk).update(published_state={})  # as a legacy row is

        pay_order(channel_order.sales_order)

        self.assertEqual(self.summary(), [('payment', 'verified'), ('order', 'completed', 'delivered'), ('entitlement', 'link', 'active')])


class TheSequenceTests(EventFixture):
    def test_it_starts_at_one_and_has_no_gaps(self):
        pay_order(self.order().sales_order)
        pay_order(self.order(self.linked, reference='WEB-2').sales_order)

        sequences = [event.sequence for event in self.events()]

        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        self.assertGreater(len(sequences), 4)

    def test_each_channel_counts_for_itself(self):
        other, _ = make_channel(slug='other-store', admin=self.admin)
        pay_order(self.order(self.linked).sales_order)
        pay_order(self.order(self.linked, reference='WEB-9', channel=other, external_ref='user-2', email='bea@example.com').sales_order)

        mine = [event.sequence for event in self.events()]
        theirs = [event.sequence for event in self.events(other)]

        self.assertEqual(mine, theirs)
        self.assertEqual(SequenceCounter.objects.get(prefix=channel_events.sequence_prefix(other.pk)).last_value, len(theirs))

    def test_a_change_that_is_rolled_back_leaves_no_event_and_burns_no_number(self):
        channel_order = self.order()
        make_payment(order=channel_order.sales_order, user=self.channel.service_user, status=Payment.CONFIRMED)

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                apply_confirmed_payments(channel_order.sales_order)
                self.assertGreater(ChannelEvent.objects.count(), 0)  # inside, it is there
                raise RuntimeError('the transaction fails')

        self.assertEqual(ChannelEvent.objects.count(), 0)
        self.assertEqual(SequenceCounter.objects.filter(prefix__startswith='chev-').count(), 0)
        channel_order.refresh_from_db()
        self.assertEqual(channel_order.published_state, {})
        # And the same change, done again, is told from the start: nothing was lost.
        apply_confirmed_payments(SalesOrder.objects.get(pk=channel_order.sales_order_id))
        self.assertEqual(self.summary()[0], ('payment', 'verified'))
        self.assertEqual([event.sequence for event in self.events()][0], 1)


class WhoIsToldTests(EventFixture):
    def test_every_active_address_of_the_channel_gets_a_delivery_for_each_event(self):
        first, _ = register_webhook(self.channel, 'http://127.0.0.1:9001/hook')
        second, _ = register_webhook(self.channel, 'http://127.0.0.1:9002/hook')
        off, _ = register_webhook(self.channel, 'http://127.0.0.1:9003/hook')
        ChannelWebhook.objects.filter(pk=off.pk).update(is_active=False)
        other, _ = make_channel(slug='other-store', admin=self.admin)
        register_webhook(other, 'http://127.0.0.1:9004/hook')

        pay_order(self.order(self.linked).sales_order)

        told = self.events()
        deliveries = WebhookDelivery.objects.all()
        self.assertEqual(deliveries.count(), len(told) * 2)
        self.assertEqual({d.webhook_id for d in deliveries}, {first.pk, second.pk})
        self.assertTrue(all(d.state == WebhookDelivery.PENDING and d.attempts == 0 for d in deliveries))
        self.assertTrue(all(d.next_attempt_at <= timezone.now() for d in deliveries))

    def test_with_no_address_events_are_still_kept_for_the_feed(self):
        pay_order(self.order().sales_order)

        self.assertGreater(len(self.events()), 0)
        self.assertEqual(WebhookDelivery.objects.count(), 0)


class TheFeedTests(APITestCase, EventFixture):
    def setUp(self):
        super().setUp()
        self.auth = channel_auth(self.raw_key)

    def feed(self, auth=None, **params):
        return self.client.get(FEED, params, **(auth if auth is not None else self.auth))

    def make_events(self, count=3):
        for i in range(count):
            self.order(self.linked, reference=f'WEB-{i}')
        for order in ChannelOrder.objects.order_by('pk'):
            pay_order(order.sales_order)

    def test_it_lists_what_happened_oldest_first(self):
        self.make_events(2)

        response = self.feed()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        results = response.data['results']
        self.assertEqual([r['sequence'] for r in results], list(range(1, len(results) + 1)))
        self.assertEqual(set(results[0]), {'sequence', 'type', 'created_at', 'data'})
        self.assertEqual(response.data['next_after'], results[-1]['sequence'])
        self.assertFalse(response.data['has_more'])

    def test_after_continues_where_the_store_left_off(self):
        self.make_events(2)
        everything = [r['sequence'] for r in self.feed().data['results']]

        rest = self.feed(after=everything[2]).data

        self.assertEqual([r['sequence'] for r in rest['results']], everything[3:])
        self.assertEqual(self.feed(after=everything[-1]).data['results'], [])
        self.assertEqual(self.feed(after=everything[-1]).data['next_after'], everything[-1])

    def test_limit_pages_the_feed_and_says_there_is_more(self):
        self.make_events(2)

        page = self.feed(limit=2).data

        self.assertEqual(len(page['results']), 2)
        self.assertTrue(page['has_more'])
        nxt = self.feed(after=page['next_after'], limit=100).data
        self.assertEqual(page['results'][0]['sequence'] + 2, nxt['results'][0]['sequence'])

    def test_bad_parameters_are_refused_in_words(self):
        for params in ({'after': 'x'}, {'after': -1}, {'limit': 0}, {'limit': 501}, {'limit': 'many'}):
            response = self.feed(**params)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, params)

    def test_a_store_reads_only_its_own_channels_events(self):
        other, other_key = make_channel(slug='other-store', admin=self.admin)
        pay_order(self.order(self.linked).sales_order)
        pay_order(self.order(self.linked, reference='WEB-9', channel=other, external_ref='user-2', email='bea@example.com').sales_order)

        mine = self.feed().data['results']
        theirs = self.feed(auth=channel_auth(other_key)).data['results']

        self.assertTrue(all(r['data']['order_number'] != theirs[0]['data']['order_number'] for r in mine))
        self.assertEqual(len(mine), len(theirs))

    def test_it_needs_the_channels_own_key_and_is_never_cached(self):
        self.make_events(1)

        self.assertEqual(self.feed(auth={}).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.client.get(FEED, HTTP_AUTHORIZATION='Token nope').status_code, status.HTTP_401_UNAUTHORIZED)
        response = self.feed()
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('Authorization', response['Vary'])

    def test_it_costs_the_same_however_much_is_in_it(self):
        self.make_events(1)
        with CaptureQueriesContext(connection) as small:
            self.feed()
        self.make_events(4)
        with CaptureQueriesContext(connection) as large:
            self.feed()

        self.assertEqual(len(small), len(large))
