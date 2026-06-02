from decimal import Decimal
from unittest.mock import patch

import requests
import responses
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import InventoryMovement, Payment, Role, SalesOrder, SystemSettings
from api.tests.helpers import (
    auth_client,
    make_customer,
    make_order,
    make_payment,
    make_product,
    make_user,
    png_upload,
    vepay_payload,
)


class OrderAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_user(Role.STAFF, email='staff-orders@example.com')
        self.manager = make_user(Role.MANAGER, email='manager-orders@example.com')
        self.admin = make_user(Role.ADMIN, email='admin-orders@example.com', is_staff=True)
        self.customer = make_customer(email='order-customer@example.com')
        self.product = make_product(sku='ORD-001', stock=20, unit_price='12.50')

    def test_create_update_delete_and_validation(self):
        auth_client(self.client, self.staff)
        created = self.client.post('/api/v1/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': 2}],
            'notes': 'API order',
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(created.data['status'], SalesOrder.DRAFT)
        self.assertEqual(len(created.data['items']), 1)

        no_items = self.client.post('/api/v1/orders/', {
            'customer_id': self.customer.pk,
            'items': [],
        }, format='json')
        self.assertEqual(no_items.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('items', no_items.data['details'])

        bad_item = self.client.post('/api/v1/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': 999999, 'quantity': 0, 'unit_price': '0.00'}],
        }, format='json')
        self.assertEqual(bad_item.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('items', bad_item.data['details'])

        patch = self.client.patch(f"/api/v1/orders/{created.data['id']}/", {
            'notes': 'Updated',
        }, format='json')
        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        self.assertEqual(patch.data['notes'], 'Updated')

        delete = self.client.delete(f"/api/v1/orders/{created.data['id']}/")
        self.assertEqual(delete.status_code, status.HTTP_204_NO_CONTENT)

    def test_lifecycle_transitions_stock_side_effects_and_wrong_status(self):
        draft = make_order(customer=self.customer, product=self.product, user=self.staff, status=SalesOrder.DRAFT)
        auth_client(self.client, self.staff)
        submit = self.client.post(f'/api/v1/orders/{draft.pk}/submit/')
        self.assertEqual(submit.status_code, status.HTTP_200_OK)
        self.assertEqual(submit.data['status'], SalesOrder.PENDING)

        wrong_submit = self.client.post(f'/api/v1/orders/{draft.pk}/submit/')
        self.assertEqual(wrong_submit.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(wrong_submit.data['code'], 'wrong_status')

        auth_client(self.client, self.manager)
        confirm = self.client.post(f'/api/v1/orders/{draft.pk}/confirm/')
        self.assertEqual(confirm.status_code, status.HTTP_200_OK)
        self.assertEqual(confirm.data['status'], SalesOrder.CONFIRMED)
        self.assertTrue(InventoryMovement.objects.filter(
            product=self.product,
            movement_type=InventoryMovement.SALE,
            reference_id=draft.pk,
        ).exists())

        cancel = self.client.post(f'/api/v1/orders/{draft.pk}/cancel/')
        self.assertEqual(cancel.status_code, status.HTTP_200_OK)
        self.assertEqual(cancel.data['status'], SalesOrder.CANCELLED)
        self.assertTrue(InventoryMovement.objects.filter(
            product=self.product,
            movement_type=InventoryMovement.RETURN,
            reference_id=draft.pk,
        ).exists())

        paid = make_order(customer=self.customer, product=self.product, user=self.staff, status=SalesOrder.PAID)
        auth_client(self.client, self.staff)
        ship = self.client.post(f'/api/v1/orders/{paid.pk}/ship/')
        self.assertEqual(ship.status_code, status.HTTP_200_OK)
        deliver = self.client.post(f'/api/v1/orders/{paid.pk}/deliver/')
        self.assertEqual(deliver.status_code, status.HTTP_200_OK)
        self.assertEqual(deliver.data['status'], SalesOrder.DELIVERED)

        refund_forbidden = self.client.post(f'/api/v1/orders/{paid.pk}/refund/')
        self.assertEqual(refund_forbidden.status_code, status.HTTP_403_FORBIDDEN)

        paid_for_refund = make_order(customer=self.customer, product=self.product, user=self.staff, status=SalesOrder.PAID)
        auth_client(self.client, self.admin)
        refund = self.client.post(f'/api/v1/orders/{paid_for_refund.pk}/refund/')
        self.assertEqual(refund.status_code, status.HTTP_200_OK)
        self.assertEqual(refund.data['status'], SalesOrder.REFUNDED)

    def test_bulk_transition_filters_search_ordering_and_partial_failures(self):
        pending = make_order(customer=self.customer, product=self.product, user=self.staff, status=SalesOrder.PENDING)
        draft = make_order(customer=self.customer, product=self.product, user=self.staff, status=SalesOrder.DRAFT)

        auth_client(self.client, self.manager)
        response = self.client.post('/api/v1/orders/bulk-transition/', {
            'order_ids': [pending.pk, draft.pk, 999999],
            'action': 'confirm',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['succeeded']), 1)
        self.assertEqual(len(response.data['failed']), 2)

        invalid_action = self.client.post('/api/v1/orders/bulk-transition/', {
            'order_ids': [pending.pk],
            'action': 'refund',
        }, format='json')
        self.assertEqual(invalid_action.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_action.data['code'], 'invalid_action')

        empty = self.client.post('/api/v1/orders/bulk-transition/', {
            'order_ids': [],
            'action': 'confirm',
        }, format='json')
        self.assertEqual(empty.status_code, status.HTTP_400_BAD_REQUEST)

        filtered = self.client.get(
            f'/api/v1/orders/?customer={self.customer.pk}&status=confirmed&search={pending.order_number}&ordering=-created_at'
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(filtered.data['count'], 1)


class PaymentAndReceiptAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_user(Role.STAFF, email='staff-payments@example.com')
        self.manager = make_user(Role.MANAGER, email='manager-payments@example.com')
        self.product = make_product(sku='PAY-001', stock=20, unit_price='10.00')
        self.order = make_order(product=self.product, user=self.staff, status=SalesOrder.CONFIRMED)
        self.provider_url = 'https://vepay.test/v1/receipts/parse'
        self.health_url = 'https://vepay.test/health'
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

    def test_create_payment_auto_marks_order_paid_and_validates_inputs(self):
        auth_client(self.client, self.staff)
        response = self.client.post('/api/v1/payments/', {
            'sales_order': self.order.pk,
            'amount': '10.00',
            'payment_method': Payment.CASH,
            'reference_number': 'cash-001',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['amount'], '10.00')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.PAID)

        paid_order_error = self.client.post('/api/v1/payments/', {
            'sales_order': self.order.pk,
            'amount': '1.00',
            'payment_method': Payment.CASH,
        }, format='json')
        self.assertEqual(paid_order_error.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('sales_order', paid_order_error.data['details'])

        new_order = make_order(product=self.product, user=self.staff, status=SalesOrder.CONFIRMED)
        invalid_amount = self.client.post('/api/v1/payments/', {
            'sales_order': new_order.pk,
            'amount': '0.00',
            'payment_method': Payment.CASH,
        }, format='json')
        self.assertEqual(invalid_amount.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('amount', invalid_amount.data['details'])

        invalid_method = self.client.post('/api/v1/payments/', {
            'sales_order': new_order.pk,
            'amount': '1.00',
            'payment_method': 'wire',
        }, format='json')
        self.assertEqual(invalid_method.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('payment_method', invalid_method.data['details'])

    def test_payment_filters_and_immutable_methods(self):
        payment = make_payment(
            order=self.order,
            user=self.staff,
            payment_method=Payment.BANK_TRANSFER,
            status=Payment.PENDING_REVIEW,
            origin_bank='Banesco',
            recipient_bank='Mercantil',
            notes='Manual review',
        )
        auth_client(self.client, self.staff)

        filtered = self.client.get(
            f'/api/v1/payments/?sales_order={self.order.pk}&method=bank_transfer&status=pending_review&bank=banes&page_size=1'
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.data['count'], 1)
        self.assertEqual(filtered.data['results'][0]['id'], payment.pk)

        for method in ('put', 'patch', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    f'/api/v1/payments/{payment.pk}/',
                    {'amount': '1.00'},
                    format='json',
                )
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @responses.activate
    def test_verify_receipt_success_mismatch_duplicate_and_provider_errors(self):
        auth_client(self.client, self.manager)
        responses.add(
            responses.POST,
            self.provider_url,
            json=vepay_payload(amount='500.00', reference='REF123', transaction_key='tx-ok'),
            status=200,
        )
        success = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': png_upload(),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
            'expected_reference': 'REF123',
            'expected_paid_on': '2026-05-03',
            'expected_origin_bank': 'BDV',
        }, format='multipart')
        self.assertEqual(success.status_code, status.HTTP_200_OK)
        self.assertTrue(success.data['valid'])
        self.assertTrue(success.data['checks']['field_matches']['reference'])

        responses.add(
            responses.POST,
            self.provider_url,
            json=vepay_payload(amount='500.00', reference='OTHER', transaction_key='tx-mismatch'),
            status=200,
        )
        mismatch = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': png_upload(),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
            'expected_reference': 'REF123',
            'expected_paid_on': '2026-05-03',
            'expected_origin_bank': 'BDV',
        }, format='multipart')
        self.assertEqual(mismatch.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(mismatch.data['code'], 'receipt_field_mismatch')
        self.assertIn('reference', mismatch.data['details']['mismatches'])

        make_payment(order=self.order, user=self.staff, transaction_key='tx-dup')
        responses.add(
            responses.POST,
            self.provider_url,
            json=vepay_payload(transaction_key='tx-dup'),
            status=200,
        )
        duplicate = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': png_upload(),
            'sales_order': self.order.pk,
            'payment_method': Payment.MOBILE_PAYMENT,
        }, format='multipart')
        self.assertEqual(duplicate.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(duplicate.data['code'], 'duplicate_transaction')

    def test_verify_receipt_validation_edges_and_timeout(self):
        auth_client(self.client, self.manager)
        missing_image = self.client.post('/api/v1/payments/receipts/verify/', {
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
        }, format='multipart')
        self.assertEqual(missing_image.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('image', missing_image.data['details'])

        invalid_mime = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': SimpleUploadedFile('receipt.txt', b'not an image', content_type='text/plain'),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
        }, format='multipart')
        self.assertEqual(invalid_mime.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(invalid_mime.data['code'], 'unsupported_receipt_type')

        corrupt_image = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': SimpleUploadedFile('receipt.png', b'not an image', content_type='image/png'),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
        }, format='multipart')
        self.assertEqual(corrupt_image.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        settings = SystemSettings.get()
        settings.ocr_max_file_mb = 1
        settings.save()
        too_large = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': SimpleUploadedFile('big.png', b'x' * (1024 * 1024 + 1), content_type='image/png'),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
        }, format='multipart')
        self.assertEqual(too_large.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        with patch('core.services.vepay.time.sleep', return_value=None), patch(
            'core.services.vepay.requests.post',
            side_effect=requests.Timeout(),
        ):
            timeout_response = self.client.post('/api/v1/payments/receipts/verify/', {
                'image': png_upload(),
                'payment_method': Payment.MOBILE_PAYMENT,
                'expected_amount_usd': '10.00',
            }, format='multipart')
        self.assertEqual(timeout_response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(timeout_response.data['code'], 'timeout')

    @responses.activate
    def test_receipt_ocr_disabled_method_disabled_incomplete_and_healthz(self):
        auth_client(self.client, self.manager)
        settings = SystemSettings.get()
        settings.ocr_enabled = False
        settings.save()
        disabled = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': png_upload(),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
        }, format='multipart')
        self.assertEqual(disabled.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(disabled.data['code'], 'ocr_disabled')

        settings.ocr_enabled = True
        settings.ocr_enabled_methods = []
        settings.save()
        method_disabled = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': png_upload(),
            'payment_method': Payment.MOBILE_PAYMENT,
            'expected_amount_usd': '10.00',
        }, format='multipart')
        self.assertEqual(method_disabled.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(method_disabled.data['code'], 'ocr_method_disabled')

        settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT]
        settings.save()
        responses.add(
            responses.POST,
            self.provider_url,
            json=vepay_payload(complete=False, transaction_key='tx-incomplete'),
            status=200,
        )
        incomplete = self.client.post('/api/v1/payments/receipts/verify/', {
            'image': png_upload(),
            'sales_order': self.order.pk,
            'payment_method': Payment.MOBILE_PAYMENT,
        }, format='multipart')
        self.assertEqual(incomplete.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(incomplete.data['code'], 'incomplete_receipt')

        responses.add(responses.GET, self.health_url, json={'status': 'ready'}, status=200)
        health = self.client.get('/api/v1/payments/receipts/healthz/')
        self.assertEqual(health.status_code, status.HTTP_200_OK)
        self.assertTrue(health.data['ok'])

        auth_client(self.client, self.staff)
        forbidden = self.client.get('/api/v1/payments/receipts/healthz/')
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)
