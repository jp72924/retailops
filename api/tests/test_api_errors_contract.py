from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Payment, Role, SalesOrder, SystemSettings
from api.throttling import LoginRateThrottle
from api.tests.helpers import auth_client, make_customer, make_order, make_product, make_user


class APIErrorContractTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_user(Role.STAFF, email='staff-errors@example.com')
        self.manager = make_user(Role.MANAGER, email='manager-errors@example.com')
        self.product = make_product(sku='ERR-001', stock=5)

    def assert_error(self, response, expected_status, expected_code, *, has_details=False):
        self.assertEqual(response.status_code, expected_status)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], expected_code)
        if has_details:
            self.assertIn('details', response.data)

    def test_standard_auth_permission_validation_not_found_and_method_errors(self):
        unauthenticated = self.client.get('/api/v1/products/')
        self.assert_error(unauthenticated, status.HTTP_401_UNAUTHORIZED, 'not_authenticated')

        self.client.credentials(HTTP_AUTHORIZATION='Token invalid')
        invalid_token = self.client.get('/api/v1/products/')
        self.assert_error(invalid_token, status.HTTP_401_UNAUTHORIZED, 'authentication_failed')

        auth_client(self.client, self.staff)
        forbidden = self.client.post('/api/v1/inventory/adjust/', {
            'product_id': self.product.pk,
            'quantity': 1,
        }, format='json')
        self.assert_error(forbidden, status.HTTP_403_FORBIDDEN, 'permission_denied')

        validation = self.client.post('/api/v1/customers/', {
            'first_name': 'Missing',
        }, format='json')
        self.assert_error(validation, status.HTTP_400_BAD_REQUEST, 'validation_error', has_details=True)
        self.assertIn('email', validation.data['details'])

        not_found = self.client.get('/api/v1/products/999999/')
        self.assert_error(not_found, status.HTTP_404_NOT_FOUND, 'not_found')

        method = self.client.delete('/api/v1/inventory/1/')
        self.assert_error(method, status.HTTP_405_METHOD_NOT_ALLOWED, 'method_not_allowed')

    def test_manual_conflict_and_wrong_status_errors_keep_error_and_code(self):
        auth_client(self.client, self.staff)
        customer = make_customer(email='protected-error@example.com')
        make_order(customer=customer, product=self.product, user=self.staff, status=SalesOrder.DRAFT)
        conflict = self.client.delete(f'/api/v1/customers/{customer.pk}/')
        self.assert_error(conflict, status.HTTP_409_CONFLICT, 'conflict')

        confirmed = make_order(product=self.product, user=self.staff, status=SalesOrder.CONFIRMED)
        wrong_status = self.client.post(f'/api/v1/orders/{confirmed.pk}/submit/')
        self.assert_error(wrong_status, status.HTTP_409_CONFLICT, 'wrong_status')

    def test_receipt_error_statuses_are_explicit(self):
        auth_client(self.client, self.manager)
        settings = SystemSettings.get()
        settings.ocr_enabled = True
        settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT]
        settings.ocr_base_url = 'https://vepay.test'
        settings.ocr_max_file_mb = 1
        settings.save()

        too_large = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': SimpleUploadedFile('big.png', b'x' * (1024 * 1024 + 1), content_type='image/png'),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
        }, format='multipart')
        self.assert_error(too_large, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, 'receipt_too_large')

        unsupported = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': SimpleUploadedFile('receipt.txt', b'not image', content_type='text/plain'),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
        }, format='multipart')
        self.assert_error(unsupported, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, 'unsupported_receipt_type')

        settings.ocr_enabled_methods = []
        settings.save()
        unprocessable = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': SimpleUploadedFile('receipt.png', b'not image', content_type='image/png'),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
        }, format='multipart')
        self.assert_error(unprocessable, status.HTTP_422_UNPROCESSABLE_ENTITY, 'ocr_method_disabled')

    def test_throttled_error_envelope(self):
        cache.clear()
        self.addCleanup(cache.clear)
        with patch.object(LoginRateThrottle, 'THROTTLE_RATES', {'login': '1/min'}):
            first = self.client.post('/api/v1/auth/token/', {
                'email': 'nobody@example.com',
                'password': 'wrong',
            }, format='json')
            self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)

            second = self.client.post('/api/v1/auth/token/', {
                'email': 'nobody@example.com',
                'password': 'wrong',
            }, format='json')
        self.assert_error(second, status.HTTP_429_TOO_MANY_REQUESTS, 'throttled')
