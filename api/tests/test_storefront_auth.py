"""
Sales channel authentication and provisioning.

A channel authenticates with a secret held on the storefront's server. These
pin who is let in (an active key of an active channel, nobody else), that the
key opens nothing outside the storefront namespace, and that authenticating is
a single read with no writes.
"""
import hashlib
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase

from api.storefront.authentication import ChannelKeyAuthentication
from api.storefront.provisioning import (
    create_channel,
    issue_key,
    revoke_all_keys,
    revoke_key,
)
from api.storefront.throttling import StorefrontReadThrottle
from core.models import ChannelKey, Role, SalesChannel, User
from api.tests.helpers import (
    auth_client,
    channel_auth,
    make_channel,
    make_kiosk_station,
    make_user,
)

URL = '/api/v1/storefront/catalog/categories/'


class ChannelKeyAuthTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.channel, self.raw_key = make_channel()

    def test_an_active_key_opens_the_storefront(self):
        response = self.client.get(URL, **channel_auth(self.raw_key))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_no_credentials_is_401_and_names_the_scheme(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response['WWW-Authenticate'], 'ChannelKey')

    def test_a_wrong_or_malformed_key_is_401(self):
        forged = self.raw_key[:8] + 'x' * (len(self.raw_key) - 8)
        for header in (forged, 'short', 'nonsense-that-matches-nothing-at-all'):
            response = self.client.get(URL, **channel_auth(header))
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, header)

    def test_a_revoked_key_is_refused(self):
        revoke_all_keys(self.channel)
        response = self.client.get(URL, **channel_auth(self.raw_key))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_channel_can_rotate_keys_without_downtime(self):
        _new, new_raw = issue_key(self.channel, label='rotation')

        # Both work while the storefront switches over.
        self.assertEqual(self.client.get(URL, **channel_auth(self.raw_key)).status_code, 200)
        self.assertEqual(self.client.get(URL, **channel_auth(new_raw)).status_code, 200)

        revoke_key(ChannelKey.objects.get(key_hash=hashlib.sha256(self.raw_key.encode()).hexdigest()))
        self.assertEqual(self.client.get(URL, **channel_auth(self.raw_key)).status_code, 401)
        self.assertEqual(self.client.get(URL, **channel_auth(new_raw)).status_code, 200)

    def test_an_inactive_channel_is_refused(self):
        SalesChannel.objects.filter(pk=self.channel.pk).update(is_active=False)
        response = self.client.get(URL, **channel_auth(self.raw_key))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_deactivated_service_user_is_refused(self):
        User.objects.filter(pk=self.channel.service_user_id).update(is_active=False)
        response = self.client.get(URL, **channel_auth(self.raw_key))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ChannelKeyIsolationTests(APITestCase):
    """A channel key opens the storefront and nothing else; nothing else opens it."""

    def setUp(self):
        cache.clear()
        self.channel, self.raw_key = make_channel()

    def test_a_channel_key_is_refused_by_the_staff_api(self):
        for path in ('/api/v1/products/', '/api/v1/orders/', '/api/v1/customers/',
                     '/api/v1/payments/', '/api/v1/settings/', '/api/v1/auth/me/'):
            response = self.client.get(path, **channel_auth(self.raw_key))
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, path)

    def test_a_channel_key_is_refused_by_the_kiosk_api(self):
        make_kiosk_station()
        for path in ('/api/v1/kiosk/products/', '/api/v1/kiosk/recipient-profiles/'):
            response = self.client.get(path, **channel_auth(self.raw_key))
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, path)

    def test_a_kiosk_key_is_refused_by_the_storefront(self):
        _station, kiosk_key = make_kiosk_station()
        response = self.client.get(URL, HTTP_AUTHORIZATION=f'KioskKey {kiosk_key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_staff_token_is_refused_by_the_storefront(self):
        auth_client(self.client, make_user(Role.ADMIN))
        response = self.client.get(URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_the_storefront_is_read_only(self):
        for method in ('post', 'put', 'patch', 'delete'):
            response = getattr(self.client, method)(URL, **channel_auth(self.raw_key))
            self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED, method)


class ChannelAuthCostTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.channel, self.raw_key = make_channel()

    def test_authenticating_is_one_select_and_writes_nothing(self):
        request = Request(APIRequestFactory().get('/', **channel_auth(self.raw_key)))

        with CaptureQueriesContext(connection) as queries:
            user, key = ChannelKeyAuthentication().authenticate(request)

        self.assertEqual(len(queries), 1)
        self.assertTrue(queries[0]['sql'].lstrip().upper().startswith('SELECT'))
        self.assertEqual(key.channel, self.channel)
        self.assertEqual(request.channel, self.channel)

        # The channel, its service user and the role arrive with that one read.
        with CaptureQueriesContext(connection) as later:
            self.assertEqual(user.role.name, Role.CHANNEL)
            self.assertEqual(key.channel.service_user, user)
        self.assertEqual(len(later), 0)

    def test_a_whole_request_issues_no_writes(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(URL, **channel_auth(self.raw_key))

        self.assertEqual(response.status_code, 200)
        writes = [
            q['sql'] for q in queries
            if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
        ]
        self.assertEqual(writes, [])


class ChannelThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.channel, self.raw_key = make_channel('shop-a')
        _other, self.other_key = make_channel('shop-b', admin=self.channel.created_by)

    def test_each_channel_has_its_own_allowance(self):
        with patch.dict(StorefrontReadThrottle.THROTTLE_RATES, {'storefront_read': '2/min'}):
            codes = [self.client.get(URL, **channel_auth(self.raw_key)).status_code for _ in range(3)]
            other = self.client.get(URL, **channel_auth(self.other_key))

        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(other.status_code, 200)


class ChannelProvisioningTests(APITestCase):
    def test_a_channel_gets_a_service_user_that_cannot_log_in(self):
        channel, raw_key = make_channel('trend-store')

        user = channel.service_user
        self.assertEqual(user.role.name, Role.CHANNEL)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.email, 'channel-trend-store@channel.internal')
        login = self.client.post('/api/v1/auth/token/', {
            'email': user.email, 'password': raw_key,
        }, format='json')
        self.assertGreaterEqual(login.status_code, 400)

    def test_only_a_hash_and_a_prefix_are_stored(self):
        channel, raw_key = make_channel()

        key = channel.keys.get()
        self.assertEqual(key.prefix, raw_key[:8])
        self.assertEqual(key.key_hash, hashlib.sha256(raw_key.encode()).hexdigest())
        stored = [str(value) for value in key.__dict__.values()]
        self.assertNotIn(raw_key, stored)
        self.assertEqual(len(raw_key), 48)

    def test_a_channel_can_hold_several_keys_and_revoking_is_idempotent(self):
        channel, _raw = make_channel()
        second, _second_raw = issue_key(channel, label='second')

        self.assertEqual(channel.keys.filter(revoked_at__isnull=True).count(), 2)
        self.assertTrue(revoke_key(second))
        self.assertFalse(revoke_key(second))
        self.assertEqual(channel.keys.filter(revoked_at__isnull=True).count(), 1)
        self.assertEqual(revoke_all_keys(channel), 1)
        self.assertEqual(revoke_all_keys(channel), 0)

    def test_provisioning_is_all_or_nothing(self):
        admin = make_user(Role.ADMIN, is_staff=True)
        create_channel('One', 'one', admin)

        with self.assertRaises(Exception):
            create_channel('One', 'two', admin)  # name already taken

        self.assertFalse(SalesChannel.objects.filter(slug='two').exists())
        self.assertFalse(User.objects.filter(email='channel-two@channel.internal').exists())
