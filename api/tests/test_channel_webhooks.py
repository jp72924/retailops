"""
Sending a channel's events to its store: signed, safe to point at, retried, and switched off when dead.

What matters: every delivery is signed with the time of *that attempt* over the exact body (and by
both secrets while one is retired), an address is checked every time it is used and the connection
actually made is checked too, redirects and proxies are never followed, a failing receiver is retried
on a fixed schedule and then given up on, an address that keeps failing is switched off, a signing
secret is stored encrypted and shown once, and registering an address is an administrator's act.
"""
import hashlib
import hmac
import io
import json
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet, InvalidToken
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from api.tests.helpers import make_channel, make_user
from core.models import ChannelEvent, ChannelWebhook, Role, SequenceCounter, WebhookDelivery
from core.services import channel_events, channel_webhooks, secret_box
from core.services.channel_webhooks import (
    MAX_ATTEMPTS,
    RETRY_DELAYS,
    UnsafeTarget,
    check_target,
    check_url_shape,
    deliver,
    deliver_due,
    payload_for,
    register_webhook,
    rotate_secret,
    sign,
    signature_header,
)
from core.services.entitlements import rotate_keys

KEY = Fernet.generate_key().decode()
OLD_KEY = Fernet.generate_key().decode()

# The same vector is in the store's own tests (trend-store/packages/retailops-kit/test/webhooks.test.ts):
# what RetailOps signs, the receiver verifies, in two languages.
VECTOR_BODY = (
    b'{"channel":"trend-editorial","created_at":"2026-10-12T12:00:00Z",'
    b'"data":{"order_number":"SO-20261012-0001"},"sequence":7,"type":"order.updated"}'
)
VECTOR_TIMESTAMP = 1800000000
VECTOR_SECRET = 'whsec_test_secret_1234567890'
VECTOR_SIGNATURE = 'd86d8d0931486a66868f19d1b0cdb6e49e404e94b6bf33f7d8c6857025878704'
VECTOR_PREVIOUS = 'whsec_previous_secret_abcdefghij'
VECTOR_PREVIOUS_SIGNATURE = 'fdb3395b3c3bd953812ef8929610c97d68601587ccc58bf35d01e53b18d22298'


class Receiver:
    """A web server on this machine that records what it is sent and answers as told."""

    def __init__(self, status=200, reply=b'ok', delay=0.0, headers=None):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                outer.requests.append({
                    'headers': {name.lower(): value for name, value in self.headers.items()},
                    'body': body,
                    'path': self.path,
                })
                if outer.delay:
                    time.sleep(outer.delay)
                try:
                    self.send_response(outer.status)
                    for name, value in outer.headers.items():
                        self.send_header(name, value)
                    self.send_header('Content-Length', str(len(outer.reply)))
                    self.end_headers()
                    self.wfile.write(outer.reply)
                except OSError:
                    pass  # the sender gave up waiting (the timeout test)

            def log_message(self, *args):
                pass

        self.requests = []
        self.status, self.reply, self.delay, self.headers = status, reply, delay, dict(headers or {})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/hook'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY], CHANNEL_WEBHOOK_ALLOW_INSECURE=True)
class WebhookFixture(TestCase):
    def setUp(self):
        self.admin = make_user(Role.ADMIN, is_staff=True)
        self.channel, _ = make_channel(admin=self.admin)
        self.receivers = []

    def receiver(self, **kwargs):
        receiver = Receiver(**kwargs)
        self.addCleanup(receiver.stop)
        return receiver

    def webhook(self, url, **kwargs):
        webhook, secret = register_webhook(self.channel, url, **kwargs)
        return webhook, secret

    def delivery(self, webhook, number='SO-20261012-0001', **fields):
        event = ChannelEvent.objects.create(
            channel=self.channel,
            sequence=SequenceCounter.next_value(channel_events.sequence_prefix(self.channel.pk)),
            event_type=ChannelEvent.ORDER_UPDATED,
            order_number=number,
            data={'order_number': number, 'client_reference': 'WEB-1', 'customer_status': 'processing', 'reason': 'paid'},
        )
        return WebhookDelivery.objects.create(
            webhook=webhook, event=event, next_attempt_at=fields.pop('next_attempt_at', timezone.now()), **fields,
        )

    def fresh(self, delivery):
        return WebhookDelivery.objects.select_related('webhook', 'event__channel').get(pk=delivery.pk)


class SigningTests(SimpleTestCase):
    def test_the_signature_is_an_hmac_of_the_timestamp_and_the_exact_body(self):
        self.assertEqual(sign(VECTOR_SECRET, VECTOR_TIMESTAMP, VECTOR_BODY), VECTOR_SIGNATURE)
        self.assertEqual(
            VECTOR_SIGNATURE,
            hmac.new(VECTOR_SECRET.encode(), f'{VECTOR_TIMESTAMP}.'.encode() + VECTOR_BODY, hashlib.sha256).hexdigest(),
        )

    def test_a_different_body_secret_or_time_gives_a_different_signature(self):
        base = sign(VECTOR_SECRET, VECTOR_TIMESTAMP, VECTOR_BODY)
        self.assertNotEqual(base, sign(VECTOR_SECRET, VECTOR_TIMESTAMP, VECTOR_BODY + b' '))
        self.assertNotEqual(base, sign(VECTOR_SECRET + 'x', VECTOR_TIMESTAMP, VECTOR_BODY))
        self.assertNotEqual(base, sign(VECTOR_SECRET, VECTOR_TIMESTAMP + 1, VECTOR_BODY))

    def test_the_header_carries_one_signature_per_secret_while_one_is_retired(self):
        self.assertEqual(signature_header([VECTOR_SECRET], VECTOR_TIMESTAMP, VECTOR_BODY), f'v1={VECTOR_SIGNATURE}')
        self.assertEqual(
            signature_header([VECTOR_SECRET, VECTOR_PREVIOUS], VECTOR_TIMESTAMP, VECTOR_BODY),
            f'v1={VECTOR_SIGNATURE},v1={VECTOR_PREVIOUS_SIGNATURE}',
        )


class ThePayloadTests(WebhookFixture):
    def test_the_body_is_compact_sorted_and_the_same_every_time(self):
        webhook, _ = self.webhook('http://127.0.0.1:9/hook')
        delivery = self.fresh(self.delivery(webhook))

        first, second = payload_for(delivery.event), payload_for(delivery.event)

        self.assertEqual(first, second)
        document = json.loads(first)
        self.assertEqual(list(document), sorted(document))
        self.assertEqual(set(document), {'sequence', 'type', 'created_at', 'channel', 'data'})
        self.assertEqual((document['sequence'], document['type'], document['channel']), (1, 'order.updated', self.channel.slug))
        self.assertNotIn(b' ', first.replace(b'order.updated', b''))
        self.assertTrue(document['created_at'].endswith('Z'))


class RegisteringTests(WebhookFixture):
    def test_the_secret_is_shown_once_and_stored_only_encrypted(self):
        webhook, secret = self.webhook('https://shop.example/hook')

        self.assertTrue(secret.startswith('whsec_'))
        self.assertGreaterEqual(len(secret), 40)
        webhook.refresh_from_db()
        self.assertNotIn(secret, webhook.secret)
        self.assertEqual(secret_box.unseal(webhook.secret), secret)
        self.assertEqual(channel_webhooks.signing_secrets(webhook), [secret])

    def test_two_registrations_never_share_a_secret(self):
        _, one = self.webhook('https://shop.example/a')
        _, two = self.webhook('https://shop.example/b')

        self.assertNotEqual(one, two)

    def test_rotating_signs_with_both_until_the_old_one_is_cleared(self):
        webhook, old = self.webhook('https://shop.example/hook')

        new = rotate_secret(webhook)
        webhook.refresh_from_db()
        self.assertEqual(channel_webhooks.signing_secrets(webhook), [new, old])
        self.assertNotEqual(new, old)

        channel_webhooks.clear_previous_secret(webhook)
        webhook.refresh_from_db()
        self.assertEqual(channel_webhooks.signing_secrets(webhook), [new])

    def test_the_address_is_checked_when_it_is_registered(self):
        with override_settings(CHANNEL_WEBHOOK_ALLOW_INSECURE=False):
            with self.assertRaises(UnsafeTarget):
                register_webhook(self.channel, 'http://shop.example/hook')
        self.assertEqual(ChannelWebhook.objects.count(), 0)


class AddressTests(SimpleTestCase):
    SHAPES = {
        'https://shop.example/hook': True,
        'https://shop.example:8443/hook': True,
        'https://user:pass@shop.example/hook': False,
        'https://user@shop.example/hook': False,
        'http://shop.example/hook': False,
        'ftp://shop.example/hook': False,
        'file:///etc/passwd': False,
        'javascript:alert(1)': False,
        'gopher://shop.example/': False,
        '//shop.example/hook': False,
        'shop.example/hook': False,
        'https:///hook': False,
        'https://shop.example:99999/hook': False,
        '': False,
        None: False,
    }

    def test_what_an_address_may_look_like(self):
        for url, ok in self.SHAPES.items():
            if ok:
                check_url_shape(url)
            else:
                with self.assertRaises(UnsafeTarget, msg=str(url)):
                    check_url_shape(url)

    def resolve(self, *addresses):
        return patch.object(
            channel_webhooks.socket, 'getaddrinfo',
            return_value=[(2, 1, 6, '', (address, 443)) for address in addresses],
        )

    def test_a_name_must_resolve_only_to_public_addresses(self):
        public = ['93.184.216.34', '8.8.8.8', '2606:2800:220:1:248:1893:25c8:1946']
        private = [
            '127.0.0.1', '127.1.2.3', '10.0.0.5', '172.16.0.1', '172.31.255.255', '192.168.1.1',
            '169.254.169.254', '100.64.0.1', '0.0.0.0', '192.0.2.1', '198.51.100.7', '203.0.113.9',
            '224.0.0.1', '255.255.255.255', '::1', '::', 'fc00::1', 'fd12:3456::1', 'fe80::1',
            '::ffff:127.0.0.1', '::ffff:10.0.0.1', '::ffff:169.254.169.254', 'ff02::1',
            # An IPv4 address carried inside an IPv6 one (NAT64, 6to4, Teredo) is not a public host.
            '64:ff9b::7f00:1', '64:ff9b::a9fe:a9fe', '64:ff9b:1::1', '2002:7f00:1::1', '2001:0:4136:e378:8000:63bf:3fff:fdd2',
        ]
        for address in public:
            with self.resolve(address):
                check_target('https://shop.example/hook')
        for address in private:
            with self.resolve(address), self.assertRaises(UnsafeTarget, msg=address):
                check_target('https://shop.example/hook')

    def test_one_private_answer_among_public_ones_is_enough_to_refuse(self):
        with self.resolve('93.184.216.34', '10.0.0.5'), self.assertRaises(UnsafeTarget):
            check_target('https://shop.example/hook')

    def test_a_name_that_does_not_resolve_is_refused(self):
        with patch.object(channel_webhooks.socket, 'getaddrinfo', side_effect=channel_webhooks.socket.gaierror), \
                self.assertRaises(UnsafeTarget):
            check_target('https://nowhere.invalid/hook')

    def test_an_address_written_as_a_number_is_judged_as_one(self):
        for host, ok in (('93.184.216.34', True), ('169.254.169.254', False), ('127.0.0.1', False), ('[::1]', False), ('[::ffff:10.0.0.1]', False)):
            url = f'https://{host}/hook'
            if ok:
                check_target(url)
            else:
                with self.assertRaises(UnsafeTarget, msg=host):
                    check_target(url)

    @override_settings(CHANNEL_WEBHOOK_ALLOW_INSECURE=True)
    def test_in_development_this_machine_and_http_are_fine(self):
        check_target('http://127.0.0.1:3001/api/retailops/webhook')
        check_target('http://localhost:3001/hook')
        with self.assertRaises(UnsafeTarget):
            check_target('ftp://127.0.0.1/hook')
        with self.assertRaises(UnsafeTarget):
            check_target('http://user:pw@127.0.0.1/hook')


class TheConnectionIsCheckedToo(WebhookFixture):
    @override_settings(CHANNEL_WEBHOOK_ALLOW_INSECURE=False)
    def test_a_name_that_resolved_to_a_public_address_but_connects_to_a_private_one_is_not_sent_to(self):
        receiver = self.receiver()
        with override_settings(CHANNEL_WEBHOOK_ALLOW_INSECURE=True):
            webhook, _ = self.webhook(f'https://127.0.0.1:{receiver.server.server_address[1]}/hook')
        delivery = self.fresh(self.delivery(webhook))

        # The lookup said "public" (as DNS can the first time), the connection goes to this machine.
        with patch.object(channel_webhooks, 'check_target'):
            ok = deliver(delivery)

        self.assertFalse(ok)
        delivery = self.fresh(delivery)
        self.assertIn('not to a public address', delivery.last_error)
        self.assertEqual(receiver.requests, [])
        self.assertEqual(delivery.state, WebhookDelivery.PENDING)  # a refusal is a failed attempt like another

    def test_the_environment_cannot_route_a_delivery_through_a_proxy(self):
        session = channel_webhooks.new_session()

        self.assertFalse(session.trust_env)
        self.assertTrue(all(isinstance(adapter, channel_webhooks._GuardedAdapter) for adapter in session.adapters.values()))


class DeliveryTests(WebhookFixture):
    def test_a_delivery_is_signed_and_the_receiver_can_verify_it(self):
        receiver = self.receiver()
        webhook, secret = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))

        ok = deliver(delivery)

        self.assertTrue(ok)
        request = receiver.requests[0]
        headers = request['headers']
        self.assertEqual(headers['content-type'], 'application/json')
        self.assertEqual(headers['x-retailops-event'], 'order.updated')
        self.assertEqual(headers['x-retailops-delivery'], str(delivery.pk))
        self.assertEqual(request['body'], payload_for(delivery.event))
        timestamp = int(headers['x-retailops-timestamp'])
        self.assertAlmostEqual(timestamp, time.time(), delta=5)
        self.assertEqual(headers['x-retailops-signature'], f'v1={sign(secret, timestamp, request["body"])}')

    def test_what_is_recorded_after_a_success(self):
        receiver = self.receiver()
        webhook, _ = self.webhook(receiver.url)
        ChannelWebhook.objects.filter(pk=webhook.pk).update(consecutive_failures=4)
        delivery = self.fresh(self.delivery(webhook))

        deliver(delivery)

        delivery = self.fresh(delivery)
        self.assertEqual((delivery.state, delivery.attempts, delivery.last_status_code, delivery.last_error), (WebhookDelivery.DELIVERED, 1, 200, ''))
        self.assertIsNotNone(delivery.delivered_at)
        self.assertEqual(delivery.webhook.consecutive_failures, 0)

    def test_it_is_signed_at_the_moment_it_is_sent_not_when_the_event_was_recorded(self):
        receiver = self.receiver()
        webhook, secret = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))
        later = delivery.event.created_at + timedelta(hours=3)

        deliver(delivery, now=later)

        headers = receiver.requests[0]['headers']
        self.assertEqual(int(headers['x-retailops-timestamp']), int(later.timestamp()))
        self.assertEqual(headers['x-retailops-signature'], f'v1={sign(secret, int(later.timestamp()), receiver.requests[0]["body"])}')

    def test_a_retry_has_the_same_body_a_new_time_and_a_new_signature(self):
        receiver = self.receiver(status=500)
        webhook, _ = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))
        start = timezone.now()

        deliver(delivery, now=start)
        deliver(self.fresh(delivery), now=start + timedelta(seconds=45))

        first, second = receiver.requests
        self.assertEqual(first['body'], second['body'])
        self.assertEqual(first['headers']['x-retailops-delivery'], second['headers']['x-retailops-delivery'])
        self.assertNotEqual(first['headers']['x-retailops-timestamp'], second['headers']['x-retailops-timestamp'])
        self.assertNotEqual(first['headers']['x-retailops-signature'], second['headers']['x-retailops-signature'])

    def test_while_a_secret_is_retired_both_sign(self):
        receiver = self.receiver()
        webhook, old = self.webhook(receiver.url)
        new = rotate_secret(webhook)
        delivery = self.fresh(self.delivery(webhook))

        deliver(delivery)

        request = receiver.requests[0]
        timestamp = int(request['headers']['x-retailops-timestamp'])
        self.assertEqual(
            request['headers']['x-retailops-signature'],
            f'v1={sign(new, timestamp, request["body"])},v1={sign(old, timestamp, request["body"])}',
        )

    def test_a_redirect_is_a_failure_and_is_not_followed(self):
        elsewhere = self.receiver()
        receiver = self.receiver(status=302, headers={'Location': elsewhere.url})
        webhook, _ = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))

        ok = deliver(delivery)

        self.assertFalse(ok)
        self.assertEqual(elsewhere.requests, [])
        delivery = self.fresh(delivery)
        self.assertIn('Redirects are not followed', delivery.last_error)
        self.assertEqual(delivery.last_status_code, 302)

    def test_what_the_receiver_said_is_kept_but_never_more_than_three_hundred_characters(self):
        receiver = self.receiver(status=500, reply=('boom ' * 400).encode())
        webhook, _ = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))

        deliver(delivery)

        delivery = self.fresh(delivery)
        self.assertEqual(delivery.last_status_code, 500)
        self.assertTrue(delivery.last_error.startswith('HTTP 500: boom'))
        self.assertLessEqual(len(delivery.last_error), 300)

    def test_a_receiver_that_does_not_answer_in_time_is_a_failure(self):
        receiver = self.receiver(delay=1.5)
        webhook, _ = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))

        with patch.object(channel_webhooks, 'READ_TIMEOUT', 0.3):
            started = time.monotonic()
            ok = deliver(delivery)
            elapsed = time.monotonic() - started

        self.assertFalse(ok)
        self.assertLess(elapsed, 1.2)
        self.assertIn('Timeout', self.fresh(delivery).last_error)

    def test_a_receiver_that_is_down_is_a_failure_not_a_crash(self):
        receiver = self.receiver()
        webhook, _ = self.webhook(receiver.url)
        receiver.stop()
        delivery = self.fresh(self.delivery(webhook))

        self.assertFalse(deliver(delivery))
        self.assertTrue(self.fresh(delivery).last_error)

    def test_a_secret_that_can_no_longer_be_opened_is_a_failed_attempt_in_words(self):
        receiver = self.receiver()
        webhook, _ = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))

        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[OLD_KEY]):
            ok = deliver(delivery)

        self.assertFalse(ok)
        self.assertIn('signing secret could not be read', self.fresh(delivery).last_error)
        self.assertEqual(receiver.requests, [])

    @override_settings(CHANNEL_WEBHOOK_ALLOW_INSECURE=False)
    def test_nothing_is_sent_to_an_address_that_is_no_longer_acceptable(self):
        receiver = self.receiver()
        with override_settings(CHANNEL_WEBHOOK_ALLOW_INSECURE=True):
            webhook, _ = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))

        ok = deliver(delivery)  # registered in development, sent in production: refused at the door

        self.assertFalse(ok)
        self.assertIn('Only https', self.fresh(delivery).last_error)
        self.assertEqual(receiver.requests, [])


class TheRetrySchedule(WebhookFixture):
    def test_it_waits_thirty_seconds_two_minutes_ten_minutes_an_hour_six_hours_and_then_gives_up(self):
        receiver = self.receiver(status=503)
        webhook, _ = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))
        clock = timezone.now()
        waited = []

        for attempt in range(MAX_ATTEMPTS):
            deliver(delivery, now=clock)
            delivery = self.fresh(delivery)
            if delivery.state == WebhookDelivery.PENDING:
                waited.append(delivery.next_attempt_at - clock)
                clock = delivery.next_attempt_at

        self.assertEqual(waited, [timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10), timedelta(hours=1), timedelta(hours=6)])
        self.assertEqual(waited, list(RETRY_DELAYS))
        self.assertEqual((delivery.state, delivery.attempts), (WebhookDelivery.FAILED, 6))
        self.assertEqual(len(receiver.requests), 6)

    def test_a_delivery_that_succeeds_on_a_retry_stops(self):
        receiver = self.receiver(status=500)
        webhook, _ = self.webhook(receiver.url)
        delivery = self.fresh(self.delivery(webhook))
        deliver(delivery)
        receiver.status = 200

        deliver(self.fresh(delivery))

        delivery = self.fresh(delivery)
        self.assertEqual((delivery.state, delivery.attempts, delivery.last_error), (WebhookDelivery.DELIVERED, 2, ''))

    def test_what_is_not_due_yet_is_left_alone_and_what_is_due_goes_in_order(self):
        receiver = self.receiver()
        webhook, _ = self.webhook(receiver.url)
        now = timezone.now()
        later = self.delivery(webhook, next_attempt_at=now + timedelta(minutes=5))
        second = self.delivery(webhook, number='SO-2', next_attempt_at=now - timedelta(seconds=1))
        first = self.delivery(webhook, number='SO-1', next_attempt_at=now - timedelta(seconds=30))

        delivered, failed = deliver_due(now=now)

        self.assertEqual((delivered, failed), (2, 0))
        self.assertEqual([json.loads(r['body'])['data']['order_number'] for r in receiver.requests], ['SO-1', 'SO-2'])
        self.assertEqual(self.fresh(later).state, WebhookDelivery.PENDING)
        self.assertEqual(self.fresh(first).state, self.fresh(second).state)

    def test_a_delivery_is_claimed_for_the_length_of_the_lease_before_it_is_sent(self):
        receiver = self.receiver()
        webhook, _ = self.webhook(receiver.url)
        delivery = self.delivery(webhook)
        now = timezone.now()
        seen = []

        def peek(found, **kwargs):
            seen.append(WebhookDelivery.objects.get(pk=found.pk).next_attempt_at)
            return True

        with patch.object(channel_webhooks, 'deliver', side_effect=peek):
            deliver_due(now=now)

        self.assertEqual(seen, [now + channel_webhooks.LEASE])
        self.assertEqual(receiver.requests, [])
        self.assertEqual(self.fresh(delivery).state, WebhookDelivery.PENDING)

    def test_a_delivery_being_sent_is_not_sent_again_by_another_worker(self):
        receiver = self.receiver()
        webhook, _ = self.webhook(receiver.url)
        self.delivery(webhook)
        now = timezone.now()
        claimed_by_another = WebhookDelivery.objects.update(next_attempt_at=now + channel_webhooks.LEASE)

        delivered, failed = deliver_due(now=now)

        self.assertEqual((claimed_by_another, delivered, failed, receiver.requests), (1, 0, 0, []))

    def test_a_worker_that_died_mid_send_leaves_it_to_be_picked_up_after_the_lease(self):
        receiver = self.receiver()
        webhook, _ = self.webhook(receiver.url)
        self.delivery(webhook)
        WebhookDelivery.objects.update(next_attempt_at=timezone.now() + channel_webhooks.LEASE)

        delivered, _ = deliver_due(now=timezone.now() + channel_webhooks.LEASE + timedelta(seconds=1))

        self.assertEqual(delivered, 1)


class SwitchingOffAnAddressThatKeepsFailing(WebhookFixture):
    @override_settings(CHANNEL_WEBHOOK_MAX_FAILURES=3)
    def test_after_the_allowed_failures_in_a_row_it_is_switched_off_and_nothing_more_is_sent(self):
        receiver = self.receiver(status=500)
        webhook, _ = self.webhook(receiver.url)
        deliveries = [self.delivery(webhook, number=f'SO-{i}') for i in range(5)]
        now = timezone.now()

        deliver_due(now=now)

        webhook.refresh_from_db()
        self.assertFalse(webhook.is_active)
        self.assertEqual(webhook.consecutive_failures, 3)
        self.assertIsNotNone(webhook.disabled_at)
        self.assertIn('3 failed deliveries in a row', webhook.disabled_reason)
        self.assertEqual(len(receiver.requests), 3)
        waiting = [self.fresh(d) for d in deliveries if self.fresh(d).attempts == 0]
        self.assertEqual(len(waiting), 2)
        self.assertEqual(deliver_due(now=now + timedelta(days=1)), (0, 0))
        self.assertEqual(len(receiver.requests), 3)

    @override_settings(CHANNEL_WEBHOOK_MAX_FAILURES=3)
    def test_a_success_in_between_starts_the_count_again(self):
        receiver = self.receiver(status=500)
        webhook, _ = self.webhook(receiver.url)
        now = timezone.now()
        for number in ('SO-1', 'SO-2'):
            deliver(self.fresh(self.delivery(webhook, number=number)), now=now)
        receiver.status = 200
        deliver(self.fresh(self.delivery(webhook, number='SO-3')), now=now)
        receiver.status = 500
        for number in ('SO-4', 'SO-5'):
            deliver(self.fresh(self.delivery(webhook, number=number)), now=now)

        webhook.refresh_from_db()

        self.assertTrue(webhook.is_active)
        self.assertEqual(webhook.consecutive_failures, 2)

    @override_settings(CHANNEL_WEBHOOK_MAX_FAILURES=1)
    def test_switching_it_back_on_releases_what_waited(self):
        receiver = self.receiver(status=500)
        webhook, _ = self.webhook(receiver.url)
        deliver(self.fresh(self.delivery(webhook)), now=timezone.now())
        waiting = self.delivery(webhook, number='SO-2', next_attempt_at=timezone.now() + timedelta(hours=5))
        webhook.refresh_from_db()
        self.assertFalse(webhook.is_active)

        released = channel_webhooks.reactivate(webhook)

        webhook.refresh_from_db()
        self.assertEqual((webhook.is_active, webhook.consecutive_failures, webhook.disabled_at, webhook.disabled_reason), (True, 0, None, ''))
        self.assertEqual(released, 2)
        self.assertLessEqual(self.fresh(waiting).next_attempt_at, timezone.now())


class TheSecretBoxTests(WebhookFixture):
    def test_it_seals_and_opens_with_the_primary_key_and_with_any_listed_one(self):
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[OLD_KEY]):
            token = secret_box.seal('a secret')
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY, OLD_KEY]):
            self.assertEqual(secret_box.unseal(token), 'a secret')
            self.assertNotEqual(token, secret_box.seal('a secret'))  # fresh every time

    def test_it_always_seals_with_the_first_key_listed(self):
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY, OLD_KEY]):
            token = secret_box.seal('a secret')

        self.assertEqual(Fernet(KEY).decrypt(token.encode()).decode(), 'a secret')
        with self.assertRaises(InvalidToken):
            Fernet(OLD_KEY).decrypt(token.encode())

    def test_a_token_whose_key_was_dropped_cannot_be_opened_and_with_no_keys_nothing_can(self):
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[OLD_KEY]):
            token = secret_box.seal('a secret')
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY]):
            with self.assertRaises(secret_box.Undecryptable):
                secret_box.unseal(token)
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[]):
            with self.assertRaises(secret_box.NotConfigured):
                secret_box.seal('x')

    def test_rotating_the_keys_re_encrypts_the_webhook_secrets_too(self):
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[OLD_KEY]):
            webhook, secret = self.webhook('https://shop.example/hook')
            rotate_secret(webhook)
            webhook.refresh_from_db()
            old_tokens = (webhook.secret, webhook.previous_secret)
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY, OLD_KEY]):
            rotated = rotate_keys()
            webhook.refresh_from_db()
            self.assertEqual(rotated, 2)
            self.assertNotEqual((webhook.secret, webhook.previous_secret), old_tokens)
            self.assertEqual(channel_webhooks.signing_secrets(webhook)[1], secret)
            self.assertEqual(rotate_keys(), 0)  # nothing left under the old key
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY]):
            self.assertEqual(len(channel_webhooks.signing_secrets(webhook)), 2)


class TheCommandsTests(WebhookFixture):
    def run_command(self, name, *args, **options):
        out = io.StringIO()
        call_command(name, *args, stdout=out, **options)
        return out.getvalue()

    def test_deliver_once_sends_what_is_due_and_exits(self):
        receiver = self.receiver()
        webhook, _ = self.webhook(receiver.url)
        self.delivery(webhook)

        output = self.run_command('deliver_channel_webhooks', once=True)

        self.assertIn('Delivered 1', output)
        self.assertEqual(len(receiver.requests), 1)
        self.assertIn('Delivered 0', self.run_command('deliver_channel_webhooks', once=True))

    def test_deliver_needs_a_mode_and_a_sensible_interval(self):
        with self.assertRaises(CommandError):
            call_command('deliver_channel_webhooks')
        with self.assertRaises(CommandError):
            call_command('deliver_channel_webhooks', interval=0)

    def test_prune_deletes_old_events_with_their_deliveries_but_not_what_still_waits(self):
        webhook, _ = self.webhook('http://127.0.0.1:9/hook')
        old_done = self.delivery(webhook, number='SO-1', state=WebhookDelivery.DELIVERED)
        old_waiting = self.delivery(webhook, number='SO-2')
        recent = self.delivery(webhook, number='SO-3', state=WebhookDelivery.DELIVERED)
        ChannelEvent.objects.filter(pk__in=[old_done.event_id, old_waiting.event_id]).update(created_at=timezone.now() - timedelta(days=40))

        dry = self.run_command('prune_channel_events', days=30, dry_run=True)
        self.assertIn('1 event(s) would be deleted', dry)
        self.assertEqual(ChannelEvent.objects.count(), 3)

        self.run_command('prune_channel_events', days=30)

        self.assertEqual(sorted(ChannelEvent.objects.values_list('order_number', flat=True)), ['SO-2', 'SO-3'])
        self.assertEqual(WebhookDelivery.objects.count(), 2)
        self.assertIsNotNone(recent.pk)

    def test_register_prints_the_secret_once_when_there_is_no_file_for_it(self):
        output = self.run_command('register_channel_webhook', channel=self.channel.slug, url='https://shop.example/hook')

        self.assertIn('Registered https://shop.example/hook', output)
        secret = [line.strip() for line in output.splitlines() if line.strip().startswith('whsec_')][0]
        webhook = ChannelWebhook.objects.get()
        self.assertEqual(channel_webhooks.signing_secrets(webhook), [secret])

    def test_register_writes_the_secret_to_dev_vars_and_never_prints_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.dev.vars'
            path.write_text('RETAILOPS_CHANNEL_KEY=k\n', encoding='utf-8')

            output = self.run_command('register_channel_webhook', channel=self.channel.slug, url='http://127.0.0.1:3001/hook', dev_vars=str(path))

            text = path.read_text(encoding='utf-8')
            secret = [line for line in text.splitlines() if line.startswith('RETAILOPS_WEBHOOK_SECRET=')][0].split('=', 1)[1]
            self.assertIn('RETAILOPS_CHANNEL_KEY=k', text)
            self.assertTrue(secret.startswith('whsec_'))
            self.assertNotIn(secret, output)
            self.assertFalse(text.startswith('﻿'))
            self.assertEqual(channel_webhooks.signing_secrets(ChannelWebhook.objects.get()), [secret])

    def test_register_is_idempotent_and_replaces_a_secret_whose_file_was_lost(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.dev.vars'
            url = 'http://127.0.0.1:3001/hook'
            self.run_command('register_channel_webhook', channel=self.channel.slug, url=url, dev_vars=str(path))
            first = path.read_text(encoding='utf-8')

            again = self.run_command('register_channel_webhook', channel=self.channel.slug, url=url, dev_vars=str(path))
            self.assertIn('already registered', again)
            self.assertEqual(path.read_text(encoding='utf-8'), first)
            self.assertEqual(ChannelWebhook.objects.count(), 1)

            path.unlink()
            self.run_command('register_channel_webhook', channel=self.channel.slug, url=url, dev_vars=str(path))
            self.assertNotEqual(path.read_text(encoding='utf-8'), first)
            webhook = ChannelWebhook.objects.get()
            self.assertEqual(webhook.previous_secret, '')  # the old one went with the file
            self.assertEqual(len(channel_webhooks.signing_secrets(webhook)), 1)

    def test_register_can_rotate_and_keep_the_old_secret_until_told_to_drop_it(self):
        url = 'https://shop.example/hook'
        self.run_command('register_channel_webhook', channel=self.channel.slug, url=url)

        self.run_command('register_channel_webhook', channel=self.channel.slug, url=url, new_secret=True)
        self.assertEqual(len(channel_webhooks.signing_secrets(ChannelWebhook.objects.get())), 2)

        self.run_command('register_channel_webhook', channel=self.channel.slug, url=url, drop_previous=True)
        self.assertEqual(len(channel_webhooks.signing_secrets(ChannelWebhook.objects.get())), 1)

    def test_register_refuses_an_unknown_channel_an_unsafe_address_and_a_missing_encryption_key(self):
        with self.assertRaises(CommandError):
            self.run_command('register_channel_webhook', channel='nope', url='https://shop.example/hook')
        with override_settings(CHANNEL_WEBHOOK_ALLOW_INSECURE=False):
            with self.assertRaises(CommandError):
                self.run_command('register_channel_webhook', channel=self.channel.slug, url='http://shop.example/hook')
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[]):
            with self.assertRaises(CommandError):
                self.run_command('register_channel_webhook', channel=self.channel.slug, url='https://shop.example/hook')
        self.assertEqual(ChannelWebhook.objects.count(), 0)


@override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY], CHANNEL_WEBHOOK_ALLOW_INSECURE=True)
class TheAdminTests(TestCase):
    def setUp(self):
        self.root = make_user(Role.ADMIN, is_staff=True, is_superuser=True)
        self.channel, _ = make_channel(admin=self.root)
        self.client.force_login(self.root)

    def messages(self, response):
        return [str(message) for message in response.context['messages']]

    def test_adding_an_address_shows_the_secret_once_and_keeps_it_encrypted(self):
        response = self.client.post('/admin/core/channelwebhook/add/', {
            'channel': self.channel.pk, 'url': 'https://shop.example/hook', 'label': 'main', 'is_active': 'on',
        }, follow=True)

        webhook = ChannelWebhook.objects.get()
        shown = [m for m in self.messages(response) if 'whsec_' in m]
        self.assertEqual(len(shown), 1)
        secret = shown[0].split(': ')[-1].strip()
        self.assertEqual(channel_webhooks.signing_secrets(webhook), [secret])
        self.assertEqual(webhook.created_by, self.root)
        page = self.client.get(f'/admin/core/channelwebhook/{webhook.pk}/change/')
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, secret)
        self.assertNotContains(page, webhook.secret)

    def test_an_address_that_will_not_be_sent_to_is_refused_at_the_form(self):
        with override_settings(CHANNEL_WEBHOOK_ALLOW_INSECURE=False):
            response = self.client.post('/admin/core/channelwebhook/add/', {
                'channel': self.channel.pk, 'url': 'http://shop.example/hook', 'is_active': 'on',
            })

        self.assertContains(response, 'Only https addresses are allowed.')
        self.assertEqual(ChannelWebhook.objects.count(), 0)

    def test_the_rotate_action_shows_the_new_secret_once_and_keeps_signing_with_the_old(self):
        webhook, old = register_webhook(self.channel, 'https://shop.example/hook')

        response = self.client.post('/admin/core/channelwebhook/', {
            'action': 'rotate_secrets', '_selected_action': [webhook.pk],
        }, follow=True)

        webhook.refresh_from_db()
        shown = [m for m in self.messages(response) if 'New signing secret' in m]
        new = shown[0].split(': ')[-1].strip()
        self.assertEqual(channel_webhooks.signing_secrets(webhook), [new, old])

    def test_the_other_actions_finish_a_rotation_and_switch_an_address_back_on(self):
        webhook, _ = register_webhook(self.channel, 'https://shop.example/hook')
        rotate_secret(webhook)

        self.client.post('/admin/core/channelwebhook/', {
            'action': 'clear_previous_secrets', '_selected_action': [webhook.pk],
        }, follow=True)
        webhook.refresh_from_db()
        self.assertEqual(webhook.previous_secret, '')

        ChannelWebhook.objects.filter(pk=webhook.pk).update(is_active=False, consecutive_failures=10, disabled_reason='too many')
        self.client.post('/admin/core/channelwebhook/', {
            'action': 'reactivate_webhooks', '_selected_action': [webhook.pk],
        }, follow=True)
        webhook.refresh_from_db()
        self.assertEqual((webhook.is_active, webhook.consecutive_failures, webhook.disabled_reason), (True, 0, ''))

    def test_the_channel_cannot_be_moved_and_a_delivery_cannot_be_edited(self):
        webhook, _ = register_webhook(self.channel, 'https://shop.example/hook')
        event = ChannelEvent.objects.create(channel=self.channel, sequence=1, event_type='order.updated', order_number='SO-1', data={})
        delivery = WebhookDelivery.objects.create(webhook=webhook, event=event, next_attempt_at=timezone.now())

        page = self.client.get(f'/admin/core/channelwebhook/{webhook.pk}/change/')
        self.assertNotContains(page, 'name="channel"')
        self.assertEqual(self.client.get('/admin/core/webhookdelivery/add/').status_code, 403)
        self.assertEqual(self.client.get('/admin/core/channelevent/add/').status_code, 403)
        listing = self.client.get('/admin/core/webhookdelivery/')
        self.assertContains(listing, 'SO-1')
        self.assertIsNotNone(delivery.pk)

    def test_the_retry_action_queues_a_failed_delivery_again(self):
        webhook, _ = register_webhook(self.channel, 'https://shop.example/hook')
        event = ChannelEvent.objects.create(channel=self.channel, sequence=1, event_type='order.updated', order_number='SO-1', data={})
        delivery = WebhookDelivery.objects.create(
            webhook=webhook, event=event, state=WebhookDelivery.FAILED, attempts=6, next_attempt_at=timezone.now() + timedelta(days=1),
        )

        self.client.post('/admin/core/webhookdelivery/', {'action': 'retry_now', '_selected_action': [delivery.pk]}, follow=True)

        delivery.refresh_from_db()
        self.assertEqual((delivery.state, delivery.attempts), (WebhookDelivery.PENDING, 0))
        self.assertLessEqual(delivery.next_attempt_at, timezone.now())


class AStoreCannotRegisterAnAddressWithItsKey(TestCase):
    def test_no_storefront_endpoint_creates_or_lists_webhooks(self):
        from api.storefront import urls

        paths = [str(pattern.pattern) for pattern in urls.urlpatterns]
        self.assertTrue([p for p in paths if p.startswith('events')], paths)
        self.assertFalse([p for p in paths if 'webhook' in p], paths)
