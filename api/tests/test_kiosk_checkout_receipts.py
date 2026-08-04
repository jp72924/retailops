import base64
import hashlib
import json
from decimal import Decimal

import responses
from django.core.cache import cache
from django.db import connections
from rest_framework import status
from rest_framework.test import APITestCase, APITransactionTestCase

from core.models import (
    Customer,
    InventoryMovement,
    KioskStation,
    Payment,
    Product,
    ProductCategory,
    RecipientProfile,
    Role,
    SalesOrder,
    SystemSettings,
    User,
)
from api.tests.helpers import (
    make_customer,
    make_kiosk_station,
    make_product,
    png_data_url,
    vepay_payload,
)


class KioskCheckoutReceiptTests(APITestCase):
    def setUp(self):
        admin_role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        kiosk_role, _ = Role.objects.get_or_create(name=Role.KIOSK)
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='AdminPass123!',
            first_name='Ada',
            last_name='Admin',
            role=admin_role,
            is_staff=True,
        )
        self.service_user = User.objects.create_user(
            email='kiosk@example.com',
            password=None,
            first_name='Kiosk',
            last_name='Station',
            role=kiosk_role,
        )
        self.raw_key = 'testkey-1234567890'
        KioskStation.objects.create(
            store_identifier='MAIN',
            station_number=1,
            label='Front',
            api_key_prefix=self.raw_key[:8],
            api_key_hash=hashlib.sha256(self.raw_key.encode()).hexdigest(),
            service_user=self.service_user,
            created_by=self.admin,
        )

        self.customer = Customer.objects.create(
            first_name='Juan',
            last_name='Perez',
            email='juan-kiosk@example.com',
            national_id='V12345678',
        )
        category = ProductCategory.objects.create(name='Beverages')
        self.product = Product.objects.create(
            sku='SKU-001',
            name='Water',
            category=category,
            unit_price=Decimal('10.00'),
        )
        InventoryMovement.objects.create(
            product=self.product,
            movement_type=InventoryMovement.PURCHASE,
            quantity=20,
            reference_type=InventoryMovement.MANUAL_ADJUSTMENT,
            reference_id=0,
            created_by=self.admin,
        )

        sys_settings = SystemSettings.get()
        sys_settings.receipt_image_required_for_receipt_methods = True
        sys_settings.save()
        self.url = '/api/v1/kiosk/checkout/'
        self.provider_url = 'https://vepay.test/v1/receipts/parse'

    def test_missing_receipt_image_is_rejected_for_receipt_methods(self):
        for payment_method in (Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER):
            with self.subTest(payment_method=payment_method):
                response = self._checkout(payment_method, receipt={
                    'reference': f'ref-{payment_method}',
                    'amount_usd': '10.00',
                    'paid_on': '2026-05-03',
                })

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data['code'], 'receipt_image_required')

        self.assertEqual(Payment.objects.count(), 0)

    def test_missing_receipt_image_is_allowed_when_setting_is_disabled(self):
        sys_settings = SystemSettings.get()
        sys_settings.receipt_image_required_for_receipt_methods = False
        sys_settings.save()

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt={
            'reference': 'ref-no-image',
            'amount_usd': '10.00',
            'paid_on': '2026-05-03',
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.PENDING_REVIEW)
        self.assertFalse(payment.receipt_image)

    def test_empty_receipt_image_payload_is_rejected_when_required(self):
        response = self._checkout(Payment.MOBILE_PAYMENT, receipt={
            'reference': 'ref-empty-image',
            'amount_usd': '10.00',
            'paid_on': '2026-05-03',
            'receipt_image_base64': 'data:image/png;base64,',
            'receipt_image_content_type': 'image/png',
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'receipt_image_required')
        self.assertEqual(Payment.objects.count(), 0)

    def test_paid_on_is_stored_as_date_and_concept_is_ignored(self):
        sys_settings = SystemSettings.get()
        sys_settings.receipt_image_required_for_receipt_methods = False
        sys_settings.save()

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt={
            'reference': 'ref-paid-on',
            'amount_usd': '10.00',
            'paid_on': '2026-05-03',
            'origin_document': 'V-12345678',
            'concept': 'Compra RetailOps Kiosk',
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get()
        self.assertIn('Paid date: 2026-05-03', payment.notes)
        self.assertIn('Origin document: V-12345678', payment.notes)
        self.assertNotIn('Concept:', payment.notes)
        self.assertNotIn('Compra RetailOps Kiosk', payment.notes)

    def test_legacy_paid_at_is_normalized_to_date_only(self):
        sys_settings = SystemSettings.get()
        sys_settings.receipt_image_required_for_receipt_methods = False
        sys_settings.save()

        response = self._checkout(Payment.BANK_TRANSFER, receipt={
            'reference': 'ref-paid-at',
            'amount_usd': '10.00',
            'paid_at': '2026-05-03T15:42:00-04:00',
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get()
        self.assertIn('Paid date: 2026-05-03', payment.notes)
        self.assertNotIn('15:42', payment.notes)
        self.assertNotIn('Paid at:', payment.notes)

    @responses.activate
    def test_matching_ocr_receipt_is_confirmed_and_stored(self):
        self._enable_ocr()
        responses.add(
            responses.POST,
            self.provider_url,
            json=self._vepay_payload(),
            status=200,
        )

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.CONFIRMED)
        self.assertEqual(payment.transaction_key, 'bdv-000123')
        self.assertEqual(payment.ocr_receipt_data['request_id'], 'req-checkout')
        self.assertTrue(payment.receipt_image)

    @responses.activate
    def test_checkout_accepts_corrected_vepay_envelope(self):
        self._enable_ocr()
        receipt = self._vepay_payload()
        receipt.pop('request_id')
        responses.add(
            responses.POST,
            self.provider_url,
            json=self._vepay_envelope(receipt, request_id='req-envelope-checkout'),
            status=200,
        )

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.CONFIRMED)
        self.assertEqual(payment.ocr_receipt_data['request_id'], 'req-envelope-checkout')

    @responses.activate
    def test_checkout_rejects_receipt_amount_mismatch_from_ocr(self):
        self._enable_ocr()
        payload = self._vepay_payload()
        payload['payment']['amount']['value'] = '600.00'
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'receipt_field_mismatch')
        self.assertIn('amount_usd', response.data['details']['mismatches'])
        self.assertEqual(Payment.objects.count(), 0)

    @responses.activate
    def test_checkout_rejects_observed_bdv_payload_until_amount_is_extracted(self):
        self._enable_ocr()
        responses.add(
            responses.POST,
            self.provider_url,
            json=self._vepay_envelope(self._observed_bdv_receipt()),
            status=200,
        )

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'receipt_field_mismatch')
        self.assertEqual(response.data['details']['field_matches']['reference'], True)
        self.assertEqual(response.data['details']['field_matches']['paid_on'], True)
        self.assertEqual(response.data['details']['field_matches']['origin_bank'], True)
        self.assertEqual(response.data['details']['field_matches']['amount_usd'], False)
        self.assertEqual(set(response.data['details']['mismatches']), {'amount_usd'})
        self.assertEqual(Payment.objects.count(), 0)

    @responses.activate
    def test_checkout_rejects_receipt_reference_date_and_bank_mismatches_from_ocr(self):
        self._enable_ocr()
        cases = [
            ('reference', ('payment', 'reference'), '999999'),
            ('paid_on', ('payment', 'date_time', 'iso'), '2026-05-04T15:42:00-04:00'),
            ('origin_bank', ('payment', 'bank_app'), 'Mercantil'),
        ]

        for expected_key, path, value in cases:
            with self.subTest(expected_key=expected_key):
                responses.reset()
                payload = self._vepay_payload(transaction_key=f'tx-{expected_key}')
                target = payload
                for segment in path[:-1]:
                    target = target[segment]
                target[path[-1]] = value
                responses.add(responses.POST, self.provider_url, json=payload, status=200)

                response = self._checkout(
                    Payment.BANK_TRANSFER,
                    receipt=self._receipt_payload(reference='000123'),
                )

                self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
                self.assertEqual(response.data['code'], 'receipt_field_mismatch')
                self.assertIn(expected_key, response.data['details']['mismatches'])

        self.assertEqual(Payment.objects.count(), 0)

    @responses.activate
    def test_recipient_mismatch_is_rejected_with_422(self):
        self._enable_ocr()
        self._enable_recipient_validation(payment_method=Payment.MOBILE_PAYMENT)
        payload = self._vepay_payload()
        payload['recipient'] = {'phone': '0424-0000000', 'bank': 'Banesco', 'document_id': 'V00000000'}
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'recipient_mismatch')
        self.assertEqual(Payment.objects.count(), 0)

    @responses.activate
    def test_recipient_match_allows_checkout(self):
        self._enable_ocr()
        self._enable_recipient_validation(payment_method=Payment.MOBILE_PAYMENT)
        payload = self._vepay_payload()
        payload['recipient'] = {'phone': '0414-1234567', 'bank': 'Banco de Venezuela', 'document_id': 'V-12345678'}
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.CONFIRMED)
        self.assertEqual(payment.recipient_bank, 'Banco de Venezuela')
        self.assertEqual(payment.recipient_account, '')

    @responses.activate
    def test_recipient_validation_disabled_skips_check(self):
        self._enable_ocr()
        payload = self._vepay_payload()
        payload['recipient'] = {'phone': '0424-0000000', 'bank': 'Banesco', 'document_id': 'V00000000'}
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @responses.activate
    def test_recipient_validation_scoped_to_payment_method(self):
        self._enable_ocr()
        self._enable_recipient_validation(payment_method=Payment.BANK_TRANSFER)
        payload = self._vepay_payload()
        payload['recipient'] = {'phone': '0414-1234567', 'bank': 'Banco de Venezuela', 'document_id': 'V-12345678'}
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self._checkout(Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'recipient_mismatch')
        self.assertEqual(response.data['details']['checked_profiles_count'], 0)

    @responses.activate
    def test_bank_transfer_recipient_match_allows_checkout(self):
        """Bank transfer profiles are matched on account_number, not phone —
        confirms the full checkout pipeline (OCR -> recipient match -> accept)
        works end-to-end for the account_number identifier."""
        self._enable_ocr()
        self._enable_recipient_validation(payment_method=Payment.BANK_TRANSFER)
        payload = self._vepay_payload()
        payload['recipient'] = {
            'account': '0102-1234-5678-9012', 'bank': 'Banco de Venezuela',
            'document_id': 'V-12345678',
        }
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self._checkout(Payment.BANK_TRANSFER, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Payment.objects.get().status, Payment.CONFIRMED)

    @responses.activate
    def test_bank_transfer_recipient_mismatch_is_rejected_with_422(self):
        self._enable_ocr()
        self._enable_recipient_validation(payment_method=Payment.BANK_TRANSFER)
        payload = self._vepay_payload()
        payload['recipient'] = {
            'account': '9999999999999999', 'bank': 'Banesco', 'document_id': 'V00000000',
        }
        responses.add(responses.POST, self.provider_url, json=payload, status=200)

        response = self._checkout(Payment.BANK_TRANSFER, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'recipient_mismatch')
        self.assertEqual(Payment.objects.count(), 0)

    def test_checkout_blocks_receipt_image_when_ocr_is_disabled(self):
        response = self._checkout(Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'ocr_disabled')
        self.assertEqual(Payment.objects.count(), 0)

    def _checkout(self, payment_method, receipt):
        payload = {
            'customer_id': self.customer.pk,
            'items': [{'sku': self.product.sku, 'quantity': 1}],
            'payment_method': payment_method,
            'payment_reference': receipt.get('reference', 'ref-001'),
            'receipt': receipt,
        }
        return self.client.post(
            self.url,
            payload,
            format='json',
            HTTP_AUTHORIZATION=f'KioskKey {self.raw_key}',
        )

    def _enable_ocr(self):
        sys_settings = SystemSettings.get()
        sys_settings.ocr_enabled = True
        sys_settings.ocr_base_url = 'https://vepay.test'
        sys_settings.ocr_api_key = 'test-key'
        sys_settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER]
        sys_settings.secondary_currency_enabled = True
        sys_settings.secondary_currency_code = 'VES'
        sys_settings.secondary_exchange_rate = Decimal('50')
        sys_settings.save()

    def _enable_recipient_validation(self, payment_method):
        RecipientProfile.objects.create(
            label='Main store', payment_method=payment_method,
            phone='4141234567' if payment_method == Payment.MOBILE_PAYMENT else '',
            account_number='0102123456789012' if payment_method == Payment.BANK_TRANSFER else '',
            bank='BDV', document_id='V12345678',
            created_by=self.admin,
        )
        sys_settings = SystemSettings.get()
        sys_settings.recipient_validation_enabled = True
        sys_settings.save()

    def _receipt_payload(self, reference='000123'):
        return {
            'origin_bank': 'BDV',
            'origin_phone': '04121234567',
            'origin_document': 'V-12345678',
            'reference': reference,
            'paid_on': '2026-05-03',
            'amount_usd': '10.00',
            'receipt_image_base64': self._image_data_url(),
            'receipt_image_content_type': 'image/png',
        }

    def _image_data_url(self):
        raw = base64.b64encode(b'test receipt image bytes').decode('ascii')
        return f'data:image/png;base64,{raw}'

    def _vepay_payload(self, transaction_key='bdv-000123'):
        return {
            'request_id': 'req-checkout',
            'payment': {
                'bank_app': 'BDV',
                'reference': '000123',
                'amount': {
                    'value': '500.00',
                    'currency': 'VES',
                },
                'date_time': {
                    'iso': '2026-05-03T15:42:00-04:00',
                },
            },
            'origin': {
                'phone': '04121234567',
                'bank': 'Banco de Venezuela',
            },
            'transaction_key': transaction_key,
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


class KioskCheckoutTransactionScopeTests(APITransactionTestCase):
    """
    Where the VEPay call sits relative to the checkout transaction.

    Order numbers are drawn from one SequenceCounter row per day under
    SELECT FOR UPDATE, and SequenceCounter.next_value's own atomic() degrades
    to a savepoint when the checkout transaction is already open — so the lock
    is held until *that* transaction commits, not until next_value returns.
    An OCR round-trip inside the transaction therefore stalls every other
    order created that day for as long as VEPay takes to answer (two attempts
    against ocr_timeout_seconds, 30s by default).

    APITransactionTestCase rather than APITestCase: the latter wraps each test
    in a transaction of its own, which would leave in_atomic_block True no
    matter where the OCR call ran.
    """

    def setUp(self):
        cache.clear()
        # `requests` is called from an asgiref worker thread, whose own
        # connections['default'] is a different wrapper that is never in a
        # transaction. Capture the connection the view itself uses — the test
        # and the view share a thread — so the callback can read that one.
        self.view_connection = connections['default']
        self.station, self.raw_key = make_kiosk_station()
        self.headers = {'HTTP_AUTHORIZATION': f'KioskKey {self.raw_key}'}
        self.customer = make_customer(email='scope@example.com', national_id='V30000000')
        self.product = make_product(sku='SCOPE-001', stock=5, unit_price='10.00')
        self.provider_url = 'https://vepay.test/v1/receipts/parse'

        sys_settings = SystemSettings.get()
        sys_settings.receipt_image_required_for_receipt_methods = True
        sys_settings.ocr_enabled = True
        sys_settings.ocr_base_url = 'https://vepay.test'
        sys_settings.ocr_api_key = 'test-key'
        sys_settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT]
        sys_settings.secondary_currency_enabled = True
        sys_settings.secondary_currency_code = 'VES'
        sys_settings.secondary_currency_symbol = 'Bs.'
        sys_settings.secondary_exchange_rate = Decimal('50')
        sys_settings.save()

    @responses.activate
    def test_ocr_call_runs_with_no_transaction_open(self):
        observed = []

        def answer(request):
            observed.append(self.view_connection.in_atomic_block)
            return 200, {}, json.dumps(vepay_payload(transaction_key='tx-scope'))

        responses.add_callback(
            responses.POST, self.provider_url,
            callback=answer, content_type='application/json',
        )

        response = self._checkout()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(observed, [False])
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.CONFIRMED)
        self.assertEqual(payment.transaction_key, 'tx-scope')

    @responses.activate
    def test_price_change_during_ocr_is_rejected_not_silently_charged(self):
        """
        The flip side of validating the receipt before the transaction: the
        total it was accepted against is no longer read under a lock. The
        write block re-checks it, so a price that moves mid-OCR is rejected
        rather than written at a total the receipt never proved payment for.

        The concurrent write below is only possible because no transaction is
        open during the call — which is the property the sibling test asserts.
        """
        def answer(request):
            Product.objects.filter(pk=self.product.pk).update(unit_price=Decimal('12.00'))
            return 200, {}, json.dumps(vepay_payload(transaction_key='tx-drift'))

        responses.add_callback(
            responses.POST, self.provider_url,
            callback=answer, content_type='application/json',
        )

        response = self._checkout()

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'amount_mismatch')
        self.assertEqual(response.data['details']['receipt_amount'], '10.00')
        self.assertEqual(response.data['details']['order_total'], '12.00')
        self.assertEqual(SalesOrder.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def _checkout(self):
        return self.client.post(
            '/api/v1/kiosk/checkout/',
            {
                'customer_id': self.customer.pk,
                'items': [{'sku': self.product.sku, 'quantity': 1}],
                'payment_method': Payment.MOBILE_PAYMENT,
                'payment_reference': 'REF123',
                'receipt': {
                    'reference': 'REF123',
                    'amount_usd': '10.00',
                    'paid_on': '2026-05-03',
                    'origin_bank': 'BDV',
                    'receipt_image_base64': png_data_url(),
                    'receipt_image_content_type': 'image/png',
                },
            },
            format='json',
            **self.headers,
        )
