from unittest import mock

import requests
import responses
from asgiref.sync import async_to_sync
from django.test import TestCase

from core.models import SystemSettings
from core.services.vepay import RECEIPT_UPLOAD_FIELD, VEPayClient, VEPayError


class VEPayClientTests(TestCase):
    def setUp(self):
        self.settings = SystemSettings.get()
        self.settings.ocr_base_url = 'https://vepay.test'
        self.settings.ocr_api_key = 'test-key'
        self.settings.ocr_timeout_seconds = 5
        self.settings.save()
        self.url = 'https://vepay.test/v1/receipts/parse'
        self.image = b'fake-image-bytes'

    def _parse(self):
        return async_to_sync(VEPayClient().parse_receipt)(
            self.image,
            'receipt.jpg',
            'image/jpeg',
        )

    @responses.activate
    def test_parse_receipt_happy_path(self):
        payload = {
            'request_id': 'req-123',
            'transaction_key': 'tx-123',
            'payment': {'amount': {'value': '963.89', 'currency': 'VES'}},
        }
        responses.add(responses.POST, self.url, json=payload, status=200)

        data = self._parse()

        self.assertEqual(data, payload)
        self.assertEqual(len(responses.calls), 1)
        self.assertEqual(responses.calls[0].request.headers['X-API-Key'], 'test-key')
        self.assertIn(
            f'name="{RECEIPT_UPLOAD_FIELD}"'.encode(),
            responses.calls[0].request.body,
        )

    @responses.activate
    def test_parse_receipt_unwraps_single_receipt_envelope(self):
        payload = {
            'request_id': 'req-envelope',
            'errors': [],
            'receipts': [{
                'transaction_key': 'tx-envelope',
                'payment': {'reference': '005901670379'},
            }],
            'summary': {'total': 1, 'complete': 1, 'incomplete': 0, 'errors': 0},
        }
        responses.add(responses.POST, self.url, json=payload, status=200)

        data = self._parse()

        self.assertEqual(data['request_id'], 'req-envelope')
        self.assertEqual(data['transaction_key'], 'tx-envelope')
        self.assertEqual(data['payment']['reference'], '005901670379')

    @responses.activate
    def test_parse_receipt_rejects_empty_receipt_envelope(self):
        responses.add(
            responses.POST,
            self.url,
            json={'request_id': 'req-empty', 'errors': [], 'receipts': []},
            status=200,
        )

        with self.assertRaises(VEPayError) as ctx:
            self._parse()

        self.assertEqual(ctx.exception.code, 'no_receipt')
        self.assertFalse(ctx.exception.is_retryable)

    @responses.activate
    def test_parse_receipt_rejects_multiple_receipt_envelope(self):
        responses.add(
            responses.POST,
            self.url,
            json={'receipts': [{'transaction_key': 'tx-1'}, {'transaction_key': 'tx-2'}]},
            status=200,
        )

        with self.assertRaises(VEPayError) as ctx:
            self._parse()

        self.assertEqual(ctx.exception.code, 'multiple_receipts')
        self.assertFalse(ctx.exception.is_retryable)

    @responses.activate
    def test_parse_receipt_surfaces_envelope_errors(self):
        responses.add(
            responses.POST,
            self.url,
            json={
                'receipts': [],
                'errors': [{'file_name': 'receipt.jpg', 'error': 'OCR failed'}],
            },
            status=200,
        )

        with self.assertRaises(VEPayError) as ctx:
            self._parse()

        self.assertEqual(ctx.exception.code, 'receipt_parse_error')
        self.assertIn('OCR failed', ctx.exception.message)
        self.assertFalse(ctx.exception.is_retryable)

    @responses.activate
    def test_parse_receipt_timeout_is_retryable(self):
        responses.add(responses.POST, self.url, body=requests.Timeout())
        responses.add(responses.POST, self.url, body=requests.Timeout())

        with mock.patch('core.services.vepay.time.sleep', return_value=None):
            with self.assertRaises(VEPayError) as ctx:
                self._parse()

        self.assertEqual(ctx.exception.code, 'timeout')
        self.assertTrue(ctx.exception.is_retryable)
        self.assertEqual(len(responses.calls), 2)

    @responses.activate
    def test_parse_receipt_4xx_is_not_retryable(self):
        responses.add(
            responses.POST,
            self.url,
            json={'detail': 'Invalid receipt image.'},
            status=422,
        )

        with self.assertRaises(VEPayError) as ctx:
            self._parse()

        self.assertEqual(ctx.exception.code, 'http_422')
        self.assertFalse(ctx.exception.is_retryable)
        self.assertIn('Invalid receipt image', ctx.exception.message)
        self.assertEqual(len(responses.calls), 1)

    @responses.activate
    def test_parse_receipt_5xx_retries_once_then_succeeds(self):
        responses.add(responses.POST, self.url, json={'error': 'temporary'}, status=502)
        responses.add(responses.POST, self.url, json={'request_id': 'req-ok'}, status=200)

        with mock.patch('core.services.vepay.time.sleep', return_value=None) as sleep:
            data = self._parse()

        self.assertEqual(data['request_id'], 'req-ok')
        self.assertEqual(len(responses.calls), 2)
        sleep.assert_called_once_with(1)

    @responses.activate
    def test_parse_receipt_5xx_after_retry_raises_retryable(self):
        responses.add(responses.POST, self.url, json={'error': 'temporary'}, status=503)
        responses.add(responses.POST, self.url, json={'error': 'still down'}, status=503)

        with mock.patch('core.services.vepay.time.sleep', return_value=None):
            with self.assertRaises(VEPayError) as ctx:
                self._parse()

        self.assertEqual(ctx.exception.code, 'http_503')
        self.assertTrue(ctx.exception.is_retryable)
        self.assertEqual(len(responses.calls), 2)

    @responses.activate
    def test_parse_receipt_malformed_json_is_retryable(self):
        responses.add(
            responses.POST,
            self.url,
            body='not-json',
            status=200,
            content_type='text/plain',
        )

        with self.assertRaises(VEPayError) as ctx:
            self._parse()

        self.assertEqual(ctx.exception.code, 'invalid_response')
        self.assertTrue(ctx.exception.is_retryable)

    @responses.activate
    def test_parse_receipt_omits_api_key_header_when_key_is_blank(self):
        self.settings.ocr_api_key = ''
        self.settings.save()
        responses.add(responses.POST, self.url, json={'request_id': 'req-no-key'}, status=200)

        data = self._parse()

        self.assertEqual(data['request_id'], 'req-no-key')
        self.assertNotIn('X-API-Key', responses.calls[0].request.headers)

    @responses.activate
    def test_health_prefers_health_alias_over_healthz(self):
        responses.add(
            responses.GET,
            'https://vepay.test/health',
            json={'ok': True, 'languages': ['eng', 'osd', 'spa']},
            status=200,
        )

        data = async_to_sync(VEPayClient().healthz)()

        self.assertTrue(data['ok'])
        self.assertEqual(data['data']['languages'], ['eng', 'osd', 'spa'])
        self.assertEqual(responses.calls[0].request.url, 'https://vepay.test/health')

    @responses.activate
    def test_health_falls_back_to_healthz_on_404(self):
        responses.add(responses.GET, 'https://vepay.test/health', status=404)
        responses.add(responses.GET, 'https://vepay.test/healthz', json={'ok': True}, status=200)

        data = async_to_sync(VEPayClient().healthz)()

        self.assertTrue(data['ok'])
        self.assertEqual(len(responses.calls), 2)
