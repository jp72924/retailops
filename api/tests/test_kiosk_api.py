from decimal import Decimal

import responses
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Customer, KioskStation, Payment, Role, SalesOrder, SystemSettings
from api.tests.helpers import (
    make_customer,
    make_kiosk_station,
    make_order,
    make_payment,
    make_product,
    make_user,
    png_data_url,
    vepay_payload,
)


class KioskAuthAndCustomerTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.station, self.raw_key = make_kiosk_station()
        self.headers = {'HTTP_AUTHORIZATION': f'KioskKey {self.raw_key}'}
        self.customer = make_customer(
            first_name='Karla',
            last_name='Kiosk',
            email='karla@example.com',
            national_id='V12345678',
        )

    def test_kiosk_key_auth_edges_and_heartbeat(self):
        no_auth = self.client.post('/api/v1/kiosk/heartbeat/')
        self.assertIn(no_auth.status_code, {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})

        wrong_scheme = self.client.post('/api/v1/kiosk/heartbeat/', HTTP_AUTHORIZATION=f'Token {self.raw_key}')
        self.assertIn(wrong_scheme.status_code, {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})

        short_key = self.client.post('/api/v1/kiosk/heartbeat/', HTTP_AUTHORIZATION='KioskKey short')
        self.assertEqual(short_key.status_code, status.HTTP_401_UNAUTHORIZED)

        invalid_key = self.client.post('/api/v1/kiosk/heartbeat/', HTTP_AUTHORIZATION='KioskKey invalid-key')
        self.assertEqual(invalid_key.status_code, status.HTTP_401_UNAUTHORIZED)

        inactive_station, inactive_key = make_kiosk_station(raw_key='inactive-key-123', active=False)
        inactive = self.client.post('/api/v1/kiosk/heartbeat/', HTTP_AUTHORIZATION=f'KioskKey {inactive_key}')
        self.assertEqual(inactive.status_code, status.HTTP_401_UNAUTHORIZED)
        inactive_station.refresh_from_db()
        self.assertFalse(inactive_station.is_active)

        ok = self.client.post('/api/v1/kiosk/heartbeat/', **self.headers)
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertTrue(ok.data['is_active'])

    def test_identify_and_register_validation(self):
        found = self.client.post('/api/v1/kiosk/identify/', {
            'national_id': self.customer.national_id,
        }, format='json', **self.headers)
        self.assertEqual(found.status_code, status.HTTP_200_OK)
        self.assertEqual(found.data['first_name'], 'Karla')

        missing = self.client.post('/api/v1/kiosk/identify/', {
            'national_id': 'V00000000',
        }, format='json', **self.headers)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing.data['code'], 'not_found')

        invalid = self.client.post('/api/v1/kiosk/identify/', {
            'national_id': '123',
        }, format='json', **self.headers)
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('national_id', invalid.data['details'])

        registered = self.client.post('/api/v1/kiosk/register/', {
            'national_id': 'V87654321',
            'first_name': 'New',
            'last_name': 'Customer',
            'email': 'new-kiosk@example.com',
            'phone': '04121234567',
            'date_of_birth': '1990-01-02',
            'gender': 'F',
            'state': 'Distrito Capital',
            'city': 'Caracas',
        }, format='json', **self.headers)
        self.assertEqual(registered.status_code, status.HTTP_201_CREATED)

        duplicate_email = self.client.post('/api/v1/kiosk/register/', {
            'national_id': 'V99999999',
            'first_name': 'Dup',
            'last_name': 'Email',
            'email': 'new-kiosk@example.com',
            'phone': '04121234567',
            'date_of_birth': '1990-01-02',
            'gender': 'F',
            'state': 'Distrito Capital',
            'city': 'Caracas',
        }, format='json', **self.headers)
        self.assertEqual(duplicate_email.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', duplicate_email.data['details'])


class KioskProductAndCheckoutTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.station, self.raw_key = make_kiosk_station()
        self.headers = {'HTTP_AUTHORIZATION': f'KioskKey {self.raw_key}'}
        self.customer = make_customer(email='checkout@example.com', national_id='V20000000')
        self.product = make_product(
            sku='KIOSK-001',
            name='Kiosk Water',
            stock=5,
            unit_price='10.00',
            external_image_url='https://cdn.example.com/kiosk-water.png',
        )
        self.inactive_product = make_product(sku='KIOSK-OFF', stock=5, is_active=False)
        self.provider_url = 'https://vepay.test/v1/receipts/parse'
        settings = SystemSettings.get()
        settings.receipt_image_required_for_receipt_methods = False
        settings.ocr_enabled = True
        settings.ocr_base_url = 'https://vepay.test'
        settings.ocr_api_key = 'test-key'
        settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT]
        settings.secondary_currency_enabled = True
        settings.secondary_currency_code = 'VES'
        settings.secondary_currency_symbol = 'Bs.'
        settings.secondary_exchange_rate = Decimal('50')
        settings.save()

    def test_product_search_detail_lookup_active_only_and_image_url(self):
        search = self.client.get('/api/v1/kiosk/products/?search=water', **self.headers)
        self.assertEqual(search.status_code, status.HTTP_200_OK)
        self.assertEqual(search.data['results'][0]['sku'], 'KIOSK-001')
        self.assertEqual(search.data['results'][0]['image_url'], 'https://cdn.example.com/kiosk-water.png')

        detail = self.client.get(f'/api/v1/kiosk/products/{self.product.pk}/', **self.headers)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

        lookup = self.client.get('/api/v1/kiosk/product/KIOSK-001/', **self.headers)
        self.assertEqual(lookup.status_code, status.HTTP_200_OK)

        inactive_detail = self.client.get(f'/api/v1/kiosk/products/{self.inactive_product.pk}/', **self.headers)
        self.assertEqual(inactive_detail.status_code, status.HTTP_404_NOT_FOUND)

        missing_sku = self.client.get('/api/v1/kiosk/product/NOPE/', **self.headers)
        self.assertEqual(missing_sku.status_code, status.HTTP_404_NOT_FOUND)

    def test_checkout_success_validation_stock_and_receipt_lookup(self):
        response = self._checkout()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['payment_status'], Payment.CONFIRMED)
        order = SalesOrder.objects.get(pk=response.data['order_id'])
        self.assertEqual(order.status, SalesOrder.DELIVERED)

        receipt = self.client.get(f"/api/v1/kiosk/receipt/{response.data['order_id']}/", **self.headers)
        self.assertEqual(receipt.status_code, status.HTTP_200_OK)
        self.assertEqual(receipt.data['order_number'], response.data['order_number'])

        unknown_customer = self._checkout(customer_id=999999)
        self.assertEqual(unknown_customer.status_code, status.HTTP_404_NOT_FOUND)

        bad_product = self._checkout(items=[{'sku': 'NOPE', 'quantity': 1}])
        self.assertEqual(bad_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_product.data['code'], 'invalid_product')

        inactive = self._checkout(items=[{'sku': 'KIOSK-OFF', 'quantity': 1}])
        self.assertEqual(inactive.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(inactive.data['code'], 'invalid_product')

        insufficient = self._checkout(items=[{'sku': 'KIOSK-001', 'quantity': 99}])
        self.assertEqual(insufficient.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(insufficient.data['code'], 'insufficient_stock')

        empty_items = self.client.post('/api/v1/kiosk/checkout/', {
            'customer_id': self.customer.pk,
            'items': [],
            'payment_reference': 'ref',
            'payment_method': Payment.CARD,
        }, format='json', **self.headers)
        self.assertEqual(empty_items.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('items', empty_items.data['details'])

    @responses.activate
    def test_checkout_receipt_validation_errors(self):
        settings = SystemSettings.get()
        settings.receipt_image_required_for_receipt_methods = True
        settings.save()

        missing_image = self._checkout(payment_method=Payment.MOBILE_PAYMENT, receipt={
            'reference': 'REF123',
            'amount_usd': '10.00',
            'paid_on': '2026-05-03',
            'origin_bank': 'BDV',
        })
        self.assertEqual(missing_image.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(missing_image.data['code'], 'receipt_image_required')

        settings.ocr_enabled = False
        settings.save()
        ocr_disabled = self._checkout(payment_method=Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())
        self.assertEqual(ocr_disabled.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(ocr_disabled.data['code'], 'ocr_disabled')

        settings.ocr_enabled = True
        settings.ocr_enabled_methods = []
        settings.save()
        method_disabled = self._checkout(payment_method=Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())
        self.assertEqual(method_disabled.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(method_disabled.data['code'], 'ocr_method_disabled')

        settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT]
        settings.save()
        invalid_base64 = self._checkout(payment_method=Payment.MOBILE_PAYMENT, receipt={
            **self._receipt_payload(),
            'receipt_image_base64': 'data:image/png;base64,not-valid',
        })
        self.assertEqual(invalid_base64.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_base64.data['code'], 'invalid_receipt_image')

        invalid_mime = self._checkout(payment_method=Payment.MOBILE_PAYMENT, receipt={
            **self._receipt_payload(),
            'receipt_image_base64': 'data:text/plain;base64,AAAA',
        })
        self.assertEqual(invalid_mime.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        responses.add(
            responses.POST,
            self.provider_url,
            json=vepay_payload(reference='OTHER', transaction_key='tx-kiosk-mismatch'),
            status=200,
        )
        mismatch = self._checkout(payment_method=Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())
        self.assertEqual(mismatch.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(mismatch.data['code'], 'receipt_field_mismatch')
        self.assertIn('mismatches', mismatch.data['details'])

        make_payment(order=make_order(status=SalesOrder.CONFIRMED), transaction_key='tx-kiosk-dup')
        responses.add(
            responses.POST,
            self.provider_url,
            json=vepay_payload(reference='REF123', transaction_key='tx-kiosk-dup'),
            status=200,
        )
        duplicate = self._checkout(payment_method=Payment.MOBILE_PAYMENT, receipt=self._receipt_payload())
        self.assertEqual(duplicate.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(duplicate.data['code'], 'duplicate_transaction')

        settings.receipt_image_required_for_receipt_methods = False
        settings.save()
        amount_mismatch = self._checkout(payment_method=Payment.MOBILE_PAYMENT, receipt={
            'reference': 'REF-NO-IMAGE',
            'amount_usd': '9.00',
            'paid_on': '2026-05-03',
        })
        self.assertEqual(amount_mismatch.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(amount_mismatch.data['code'], 'amount_mismatch')

    def test_receipt_endpoint_rejects_order_from_other_station_user(self):
        other_user = make_user(Role.KIOSK, email='other-kiosk@example.com', password=None)
        other_order = make_order(
            customer=self.customer,
            product=self.product,
            user=other_user,
            status=SalesOrder.DELIVERED,
        )
        response = self.client.get(f'/api/v1/kiosk/receipt/{other_order.pk}/', **self.headers)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def _checkout(self, *, customer_id=None, items=None, payment_method=Payment.CARD, receipt=None):
        return self.client.post('/api/v1/kiosk/checkout/', {
            'customer_id': customer_id or self.customer.pk,
            'items': items or [{'sku': self.product.sku, 'quantity': 1}],
            'payment_reference': 'kiosk-ref',
            'payment_method': payment_method,
            'receipt': receipt or {},
        }, format='json', **self.headers)

    def _receipt_payload(self):
        return {
            'reference': 'REF123',
            'amount_usd': '10.00',
            'paid_on': '2026-05-03',
            'origin_bank': 'BDV',
            'receipt_image_base64': png_data_url(),
            'receipt_image_content_type': 'image/png',
        }
