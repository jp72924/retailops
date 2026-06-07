import logging
from decimal import Decimal

import responses
from rest_framework.test import APITestCase

from core.models import Role, SystemSettings
from .helpers import auth_client, make_user


SOURCE_URL = 'https://rates.test/oficial'
REFRESH_URL = '/api/v1/settings/secondary-rate/refresh/'


class SecondaryRateRefreshAPITests(APITestCase):
    def setUp(self):
        settings = SystemSettings.get()
        settings.secondary_rate_source_url = SOURCE_URL
        settings.secondary_rate_source_field = 'promedio'
        settings.save()

    @responses.activate
    def test_manager_can_refresh_rate(self):
        responses.add(responses.GET, SOURCE_URL, json={'promedio': 36.42}, status=200)
        auth_client(self.client, make_user(Role.MANAGER))
        resp = self.client.post(REFRESH_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['secondary_exchange_rate'], '36.42')
        self.assertEqual(SystemSettings.get().secondary_exchange_rate, Decimal('36.42'))

    @responses.activate
    def test_source_failure_returns_502(self):
        responses.add(responses.GET, SOURCE_URL, status=500)
        auth_client(self.client, make_user(Role.MANAGER))
        # Silence Django's 5xx server-error logging: under the test client it
        # renders a traceback template, which is unrelated to what we assert.
        logging.disable(logging.CRITICAL)
        try:
            resp = self.client.post(REFRESH_URL)
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(resp.status_code, 502)
        self.assertIn('errors', resp.data)

    def test_staff_forbidden(self):
        auth_client(self.client, make_user(Role.STAFF))
        resp = self.client.post(REFRESH_URL)
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_rejected(self):
        resp = self.client.post(REFRESH_URL)
        self.assertEqual(resp.status_code, 401)
