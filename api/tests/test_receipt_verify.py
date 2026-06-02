from decimal import Decimal
from io import BytesIO

import responses
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import (
    Customer,
    OcrCallLog,
    Payment,
    Role,
    SalesOrder,
    SystemSettings,
    User,
)


class ReceiptVerifyAPITests(APITestCase):
    def setUp(self):
        role = Role.objects.create(name=Role.MANAGER)
        self.user = User.objects.create_user(
            email='manager@example.com',
            password='ManagerPass123!',
            first_name='Maya',
            last_name='Manager',
            role=role,
        )
        self.client.force_authenticate(self.user)

        self.customer = Customer.objects.create(
            first_name='Juan',
            last_name='Perez',
            email='juan@example.com',
            national_id='V12345678',
        )
        self.order = SalesOrder.objects.create(
            customer=self.customer,
            status=SalesOrder.CONFIRMED,
            subtotal=Decimal('19.28'),
            total_amount=Decimal('19.28'),
            created_by=self.user,
            confirmed_by=self.user,
            confirmed_at=timezone.now(),
        )

        settings = SystemSettings.get()
        settings.ocr_enabled = True
        settings.ocr_base_url = 'https://vepay.test'
        settings.ocr_api_key = 'test-key'
        settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT]
        settings.ocr_strict_amount = True
        settings.ocr_require_complete = True
        settings.secondary_currency_enabled = True
        settings.secondary_currency_code = 'VES'
        settings.secondary_currency_symbol = 'Bs.'
        settings.secondary_exchange_rate = Decimal('50')
        settings.save()

        self.url = reverse('api:payment-verify-receipt')
        self.provider_url = 'https://vepay.test/v1/receipts/parse'

    @responses.activate
    def test_verify_receipt_success_returns_checks_and_logs_call(self):
        responses.add(
            responses.POST,
            self.provider_url,
            json=self._vepay_payload(),
            status=200,
        )

        response = self.client.post(
            self.url,
            self._multipart_payload(),
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['valid'], True)
        self.assertIn('vepay', response.data)
        self.assertIn('checks', response.data)
        self.assertIn('warnings', response.data)
        self.assertEqual(response.data['checks']['amount_matches'], True)
        self.assertEqual(response.data['checks']['duplicate'], False)
        self.assertEqual(response.data['checks']['complete'], True)
        self.assertEqual(response.data['checks']['transaction_key'], 'bdv-000123')
        self.assertEqual(response.data['checks']['origin_phone'], '04121234567')

        log = OcrCallLog.objects.get()
        self.assertEqual(log.status, 'success')
        self.assertEqual(log.sales_order, self.order)
        self.assertEqual(log.request_id, 'req-verify-ok')
        self.assertGreater(log.bytes_sent, 0)
        self.assertIsNotNone(log.latency_ms)
        self.assertEqual(responses.calls[0].request.headers['X-API-Key'], 'test-key')

    @responses.activate
    def test_verify_receipt_duplicate_transaction_returns_409_and_logs(self):
        Payment.objects.create(
            sales_order=self.order,
            amount=Decimal('19.28'),
            payment_method=Payment.MOBILE_PAYMENT,
            status=Payment.CONFIRMED,
            transaction_key='bdv-000123',
            recorded_by=self.user,
        )
        responses.add(
            responses.POST,
            self.provider_url,
            json=self._vepay_payload(),
            status=200,
        )

        response = self.client.post(
            self.url,
            self._multipart_payload(),
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['valid'], False)
        self.assertEqual(response.data['code'], 'duplicate_transaction')
        self.assertEqual(response.data['checks']['duplicate'], True)
        self.assertEqual(OcrCallLog.objects.get().status, 'duplicate_transaction')

    @responses.activate
    def test_verify_receipt_amount_mismatch_returns_422_and_logs(self):
        payload = self._vepay_payload()
        payload['request_id'] = 'req-mismatch'
        payload['payment']['amount']['value'] = '100.00'
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self.client.post(
            self.url,
            self._multipart_payload(),
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['valid'], False)
        self.assertEqual(response.data['code'], 'amount_mismatch')
        self.assertEqual(response.data['checks']['amount_matches'], False)
        log = OcrCallLog.objects.get()
        self.assertEqual(log.status, 'amount_mismatch')
        self.assertEqual(log.request_id, 'req-mismatch')

    @responses.activate
    def test_verify_receipt_unwraps_vepay_envelope_successfully(self):
        receipt = self._vepay_payload()
        receipt.pop('request_id')
        responses.add(
            responses.POST,
            self.provider_url,
            json=self._vepay_envelope(receipt, request_id='req-envelope'),
            status=200,
        )

        response = self.client.post(
            self.url,
            self._multipart_payload(),
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['valid'], True)
        self.assertEqual(response.data['vepay']['request_id'], 'req-envelope')
        self.assertEqual(OcrCallLog.objects.get().request_id, 'req-envelope')

    @responses.activate
    def test_verify_receipt_accepts_kiosk_expected_fields_without_order(self):
        responses.add(
            responses.POST,
            self.provider_url,
            json=self._vepay_payload(),
            status=200,
        )

        response = self.client.post(
            self.url,
            self._multipart_payload({
                'sales_order': None,
                'expected_amount_usd': '19.28',
                'expected_reference': '000-123',
                'expected_paid_on': '2026-05-03',
                'expected_origin_bank': 'Banco de Venezuela',
            }),
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['valid'], True)
        self.assertEqual(response.data['checks']['field_matches']['reference'], True)
        self.assertEqual(response.data['checks']['field_matches']['paid_on'], True)
        self.assertEqual(response.data['checks']['field_matches']['origin_bank'], True)
        self.assertEqual(response.data['checks']['receipt_fields']['reference'], '000123')
        self.assertIsNone(response.data['checks']['order_amount_outstanding'])

    @responses.activate
    def test_verify_receipt_returns_field_mismatch_for_kiosk_expected_fields(self):
        payload = self._vepay_payload()
        payload['request_id'] = 'req-field-mismatch'
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self.client.post(
            self.url,
            self._multipart_payload({
                'sales_order': None,
                'expected_amount_usd': '19.28',
                'expected_reference': '999999',
                'expected_paid_on': '2026-05-04',
                'expected_origin_bank': 'Mercantil',
            }),
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['valid'], False)
        self.assertEqual(response.data['code'], 'receipt_field_mismatch')
        self.assertEqual(response.data['checks']['field_matches']['amount_usd'], True)
        self.assertEqual(response.data['checks']['field_matches']['reference'], False)
        self.assertEqual(response.data['checks']['field_matches']['paid_on'], False)
        self.assertEqual(response.data['checks']['field_matches']['origin_bank'], False)
        self.assertIn('reference', response.data['details']['mismatches'])
        self.assertEqual(OcrCallLog.objects.get().status, 'receipt_field_mismatch')

    @responses.activate
    def test_verify_receipt_reports_observed_bdv_amount_mismatch_from_envelope(self):
        responses.add(
            responses.POST,
            self.provider_url,
            json=self._vepay_envelope(self._observed_bdv_receipt()),
            status=200,
        )

        response = self.client.post(
            self.url,
            self._multipart_payload({
                'sales_order': None,
                'expected_amount_usd': '19.28',
                'expected_reference': '000123',
                'expected_paid_on': '2026-05-03',
                'expected_origin_bank': 'BDV',
            }),
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'receipt_field_mismatch')
        self.assertEqual(response.data['checks']['field_matches']['reference'], True)
        self.assertEqual(response.data['checks']['field_matches']['paid_on'], True)
        self.assertEqual(response.data['checks']['field_matches']['origin_bank'], True)
        self.assertEqual(response.data['checks']['field_matches']['amount_usd'], False)
        self.assertEqual(set(response.data['details']['mismatches']), {'amount_usd'})

    @responses.activate
    def test_verify_receipt_accepts_corrected_observed_bdv_envelope(self):
        receipt = self._observed_bdv_receipt()
        receipt['payment']['amount']['value'] = '963.89'
        receipt['payment']['bank_app'] = 'BDV'
        receipt['validation'] = {'is_complete': True, 'missing_fields': []}
        responses.add(
            responses.POST,
            self.provider_url,
            json=self._vepay_envelope(receipt),
            status=200,
        )

        response = self.client.post(
            self.url,
            self._multipart_payload({
                'sales_order': None,
                'expected_amount_usd': '19.28',
                'expected_reference': '000123',
                'expected_paid_on': '2026-05-03',
                'expected_origin_bank': 'BDV',
            }),
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['valid'], True)
        self.assertEqual(response.data['checks']['field_matches']['amount_usd'], True)
        self.assertEqual(response.data['checks']['field_matches']['origin_bank'], True)

    @responses.activate
    def test_verify_receipt_incomplete_returns_422_when_required_and_logs(self):
        payload = self._vepay_payload()
        payload['request_id'] = 'req-incomplete'
        payload['validation'] = {
            'is_complete': False,
            'missing_fields': ['origin.phone'],
        }
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self.client.post(
            self.url,
            self._multipart_payload(),
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['valid'], False)
        self.assertEqual(response.data['code'], 'incomplete_receipt')
        self.assertEqual(response.data['details']['missing_fields'], ['origin.phone'])
        self.assertEqual(OcrCallLog.objects.get().status, 'incomplete_receipt')

    def test_verify_receipt_rejects_unsupported_heif_before_provider_call(self):
        image = SimpleUploadedFile(
            'receipt.heic',
            b'not-a-heif-image',
            content_type='image/heic',
        )

        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            response = self.client.post(
                self.url,
                {
                    'sales_order': self.order.pk,
                    'payment_method': Payment.MOBILE_PAYMENT,
                    'image': image,
                },
                format='multipart',
            )

            self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
            self.assertEqual(response.data['code'], 'unsupported_heif')
            self.assertEqual(len(rsps.calls), 0)
            self.assertEqual(OcrCallLog.objects.get().status, 'unsupported_heif')

    def _multipart_payload(self, overrides=None):
        payload = {
            'sales_order': self.order.pk,
            'payment_method': Payment.MOBILE_PAYMENT,
            'image': self._image_file(),
        }
        if overrides:
            for key, value in overrides.items():
                if value is None:
                    payload.pop(key, None)
                else:
                    payload[key] = value
        return payload

    def _image_file(self):
        out = BytesIO()
        Image.new('RGB', (32, 32), color=(255, 255, 255)).save(out, format='PNG')
        return SimpleUploadedFile(
            'redacted_receipt.png',
            out.getvalue(),
            content_type='image/png',
        )

    def _vepay_payload(self):
        return {
            'request_id': 'req-verify-ok',
            'payment': {
                'bank_app': 'BDV',
                'reference': '000123',
                'amount': {
                    'value': '963.89',
                    'currency': 'VES',
                },
                'date_time': {
                    'iso': '2026-05-03T15:42:00-04:00',
                },
            },
            'origin': {
                'phone': '04121234567',
                'account': '01020000000000000000',
                'bank': 'BDV',
            },
            'recipient': {
                'phone': '04129876543',
                'document_id': 'J-12345678-9',
                'bank': 'Mercantil',
            },
            'transaction_key': 'bdv-000123',
            'validation': {
                'is_complete': True,
                'missing_fields': [],
            },
        }

    def _vepay_envelope(self, receipt, request_id='req-bdv-envelope'):
        return {
            'request_id': request_id,
            'errors': [],
            'receipts': [receipt],
            'summary': {'total': 1, 'complete': 1, 'incomplete': 0, 'errors': 0},
        }

    def _observed_bdv_receipt(self):
        return {
            'origin': {
                'account': '0102****3488',
                'bank': None,
                'phone': None,
            },
            'payment': {
                'amount': {
                    'currency': 'VES',
                    'raw': None,
                    'value': None,
                },
                'bank_app': 'bancamiga',
                'concept': 'PAGO',
                'date_time': {
                    'iso': '2026-05-03',
                    'raw': '03/05/2026',
                },
                'reference': '000123',
            },
            'recipient': {
                'bank': '0172 - BANCAMIGA BANCO',
                'document_id': '30759313',
                'phone': '04245750659',
            },
            'transaction_key': 'observed-bdv',
            'validation': {
                'is_complete': False,
                'missing_fields': ['payment.amount.value'],
            },
        }
