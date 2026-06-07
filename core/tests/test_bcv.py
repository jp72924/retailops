from decimal import Decimal
from io import StringIO

import responses
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import SystemSettings
from core.services.bcv import BCVRateError, fetch_rate, update_secondary_exchange_rate


SOURCE_URL = 'https://rates.test/oficial'


class FetchRateTests(TestCase):
    def setUp(self):
        self.settings = SystemSettings.get()
        self.settings.secondary_rate_source_url = SOURCE_URL
        self.settings.secondary_rate_source_field = 'promedio'
        self.settings.save()

    @responses.activate
    def test_fetch_rate_happy_path(self):
        responses.add(responses.GET, SOURCE_URL, json={'promedio': 36.42}, status=200)
        self.assertEqual(fetch_rate(self.settings), Decimal('36.42'))

    @responses.activate
    def test_fetch_rate_nested_dotted_path(self):
        self.settings.secondary_rate_source_field = 'data.rate'
        self.settings.save()
        responses.add(responses.GET, SOURCE_URL, json={'data': {'rate': '40.5'}}, status=200)
        self.assertEqual(fetch_rate(self.settings), Decimal('40.5'))

    @responses.activate
    def test_fetch_rate_field_not_found(self):
        responses.add(responses.GET, SOURCE_URL, json={'venta': 36.42}, status=200)
        with self.assertRaises(BCVRateError) as ctx:
            fetch_rate(self.settings)
        self.assertEqual(ctx.exception.code, 'field_not_found')

    @responses.activate
    def test_fetch_rate_non_positive_rejected(self):
        responses.add(responses.GET, SOURCE_URL, json={'promedio': 0}, status=200)
        with self.assertRaises(BCVRateError) as ctx:
            fetch_rate(self.settings)
        self.assertEqual(ctx.exception.code, 'invalid_rate')

    @responses.activate
    def test_fetch_rate_http_error(self):
        responses.add(responses.GET, SOURCE_URL, status=503)
        with self.assertRaises(BCVRateError) as ctx:
            fetch_rate(self.settings)
        self.assertEqual(ctx.exception.code, 'http_503')

    def test_fetch_rate_requires_url(self):
        self.settings.secondary_rate_source_url = ''
        self.settings.save()
        with self.assertRaises(BCVRateError) as ctx:
            fetch_rate(self.settings)
        self.assertEqual(ctx.exception.code, 'not_configured')

    @responses.activate
    def test_update_persists_rate_and_timestamp(self):
        responses.add(responses.GET, SOURCE_URL, json={'promedio': 36.42}, status=200)
        rate = update_secondary_exchange_rate(self.settings)
        self.assertEqual(rate, Decimal('36.42'))
        fresh = SystemSettings.get()
        self.assertEqual(fresh.secondary_exchange_rate, Decimal('36.42'))
        self.assertIsNotNone(fresh.secondary_rate_updated_at)


class UpdateBcvRateCommandTests(TestCase):
    def setUp(self):
        self.settings = SystemSettings.get()
        self.settings.secondary_rate_source_url = SOURCE_URL
        self.settings.secondary_rate_source_field = 'promedio'
        self.settings.secondary_rate_auto_update_enabled = True
        self.settings.save()

    @responses.activate
    def test_command_updates_rate(self):
        responses.add(responses.GET, SOURCE_URL, json={'promedio': 36.42}, status=200)
        out = StringIO()
        call_command('update_bcv_rate', stdout=out)
        self.assertEqual(SystemSettings.get().secondary_exchange_rate, Decimal('36.42'))

    def test_command_blocked_when_disabled(self):
        self.settings.secondary_rate_auto_update_enabled = False
        self.settings.save()
        with self.assertRaises(CommandError):
            call_command('update_bcv_rate')

    @responses.activate
    def test_command_force_runs_when_disabled(self):
        self.settings.secondary_rate_auto_update_enabled = False
        self.settings.save()
        responses.add(responses.GET, SOURCE_URL, json={'promedio': 7}, status=200)
        call_command('update_bcv_rate', '--force', stdout=StringIO())
        self.assertEqual(SystemSettings.get().secondary_exchange_rate, Decimal('7'))

    @responses.activate
    def test_command_dry_run_does_not_save(self):
        responses.add(responses.GET, SOURCE_URL, json={'promedio': 99}, status=200)
        call_command('update_bcv_rate', '--dry-run', stdout=StringIO())
        self.assertEqual(SystemSettings.get().secondary_exchange_rate, Decimal('1'))

    @responses.activate
    def test_command_reports_failure(self):
        responses.add(responses.GET, SOURCE_URL, status=500)
        with self.assertRaises(CommandError):
            call_command('update_bcv_rate', stdout=StringIO())
