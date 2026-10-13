"""
Paying for a channel order with a receipt.

What matters: the receipt is judged against the quote the customer was given
and not today's rate, the channel can never vouch for its own receipt, OCR
runs before any lock is held, a payment that cannot be decided waits for a
person instead of failing the customer, and nothing about the receipt but how
it reads ever goes back to the store.
"""
import json

import responses
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch

from api.tests.helpers import (
    channel_auth,
    make_category,
    make_channel,
    make_listing,
    make_product,
    make_user,
    png_data_url,
    set_currency,
    vepay_payload,
)
from core.models import (
    ChannelOrder,
    OcrCallLog,
    Payment,
    RecipientProfile,
    Role,
    SalesOrder,
    SystemSettings,
)
from core.services import channel_payments
from core.services.channel_payments import register_channel_payment
from core.services import payments as payment_service
from core.services.payments import approve_payment, reject_payment
from core.services.vepay import VEPayError

ORDERS = '/api/v1/storefront/orders/'
PROVIDER = 'https://vepay.test/v1/receipts/parse'

PAYMENT_KEYS = {'payment_number', 'payment_method', 'reference_number', 'amount', 'review_status', 'created_at'}


def _ocr_raises(error):
    def fail(*args, **kwargs):
        raise error
    return patch('core.services.receipt_validation.async_to_sync', lambda _fn: fail)


class PaymentFixture(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        set_currency()  # 50 Bs per USD: the 10.00 order is Bs 500.00
        self.admin = make_user(Role.ADMIN, is_staff=True)
        self.channel, raw_key = make_channel(admin=self.admin)
        self.auth = channel_auth(raw_key)
        self.shelf = make_product(
            sku='BOX-1', unit_price='10.00', stock=10, category=make_category('Software'),
        )
        make_listing(self.shelf, slug='box')
        self.enable_ocr()
        self.order_number = self.place_order().data['order_number']

    def enable_ocr(self, enabled=True):
        settings = SystemSettings.get()
        settings.ocr_enabled = enabled
        settings.ocr_base_url = 'https://vepay.test'
        settings.ocr_api_key = 'test-key'
        settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER]
        settings.save()

    def place_order(self, reference='WEB-1', quantity=1):
        return self.client.post(ORDERS, {
            'client_reference': reference,
            'customer': {
                'external_ref': 'user-42', 'email': 'ana@example.com', 'email_verified': False,
                'first_name': 'Ana', 'last_name': 'Pérez',
            },
            'items': [{'sku': 'BOX-1', 'quantity': quantity}],
            'fulfillment': 'pickup',
        }, format='json', **self.auth)

    def receipt(self, **overrides):
        receipt = {
            'reference': 'REF123', 'paid_on': '2026-05-03', 'origin_bank': 'BDV',
            'receipt_image_base64': png_data_url(),
        }
        receipt.update(overrides)
        return receipt

    def pay(self, number=None, method=Payment.MOBILE_PAYMENT, receipt=None, auth=None):
        return self.client.post(
            f'{ORDERS}{number or self.order_number}/payments/',
            {'payment_method': method, 'receipt': receipt if receipt is not None else self.receipt()},
            format='json', **(auth or self.auth),
        )

    def provider_reads(self, **kwargs):
        kwargs.setdefault('transaction_key', 'ocr-key')
        responses.add(responses.POST, PROVIDER, json=vepay_payload(**kwargs), status=200)

    def order(self):
        return SalesOrder.objects.get(order_number=self.order_number)


class MatchingReceiptTests(PaymentFixture):
    @responses.activate
    def test_a_receipt_that_matches_is_verified_and_pays_the_order(self):
        self.provider_reads()

        response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data['customer_status'], 'processing')
        self.assertEqual(response.data['amount_paid'], '10.00')
        self.assertEqual(response.data['amount_outstanding'], '0.00')
        [payment_view] = response.data['payments']
        self.assertEqual(set(payment_view), PAYMENT_KEYS)
        self.assertEqual(payment_view['review_status'], 'verified')
        self.assertEqual(payment_view['reference_number'], 'REF123')

        order = self.order()
        self.assertEqual(order.status, SalesOrder.PAID)
        self.assertIsNotNone(order.paid_at)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.CONFIRMED)
        self.assertEqual(payment.transaction_key, 'ocr-key')
        self.assertEqual(payment.recorded_by, self.channel.service_user)
        self.assertEqual(str(payment.amount), '10.00')
        self.assertIsNotNone(payment.verified_at)
        self.assertTrue(payment.receipt_image)
        self.assertEqual(payment.origin_bank, 'BDV')
        self.assertIn('Online payment - Trend Store', payment.notes)
        self.assertIn('Client reference: WEB-1', payment.notes)

    @responses.activate
    def test_the_response_carries_nothing_but_how_the_payment_reads(self):
        self.provider_reads()

        body = self.pay().content.decode()

        for private in ('ocr-key', 'transaction_key', 'ocr_receipt_data', 'receipt_image', 'recipient_account',
                        'review_notes', 'reviewed_by', 'recorded_by'):
            self.assertNotIn(private, body)

    @responses.activate
    def test_a_rate_change_between_the_order_and_the_payment_does_not_matter(self):
        self.provider_reads(amount='500.00')  # what the customer was quoted and paid
        set_currency(secondary_exchange_rate='60')

        response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(self.order().status, SalesOrder.PAID)
        payment = Payment.objects.get()
        # Recorded at the rate the customer was quoted, not today's.
        self.assertEqual(str(payment.currency_snapshot.secondary_exchange_rate), '50.00000000')
        self.assertEqual(payment.currency_snapshot, ChannelOrder.objects.get().currency_snapshot)

    @responses.activate
    def test_an_amount_at_the_new_rate_no_longer_matches_the_quote(self):
        self.provider_reads(amount='600.00')  # 10.00 at today's 60, not at the quoted 50
        set_currency(secondary_exchange_rate='60')

        response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'receipt_field_mismatch')
        self.assertIn('amount_usd', response.data['details']['mismatches'])
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(self.order().status, SalesOrder.CONFIRMED)

    @responses.activate
    def test_the_ocr_call_is_logged_against_the_channel(self):
        self.provider_reads()

        self.pay()

        log = OcrCallLog.objects.get()
        self.assertEqual((log.channel, log.sales_order, log.status), (self.channel, self.order(), 'ok'))
        self.assertGreater(log.bytes_sent, 0)
        self.assertIsNotNone(log.latency_ms)
        self.assertIsNone(log.kiosk_station)


class ChannelCannotVouchForItselfTests(PaymentFixture):
    @responses.activate
    def test_a_claimed_key_and_reading_never_count(self):
        self.provider_reads(transaction_key='ocr-key')

        response = self.pay(receipt=self.receipt(
            transaction_key='forged-key',
            ocr_receipt_data={'payment': {'amount': {'value': '500.00'}}},
            amount_usd='10.00',
        ))

        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get()
        self.assertEqual(payment.transaction_key, 'ocr-key')
        self.assertNotIn('forged-key', str(payment.ocr_receipt_data))

    def test_with_ocr_off_a_claimed_key_cannot_confirm_a_payment(self):
        self.enable_ocr(False)

        response = self.pay(receipt=self.receipt(
            transaction_key='forged-key', ocr_receipt_data={'payment': {}}, amount_usd='10.00',
        ))

        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.PENDING_REVIEW)
        self.assertEqual(payment.transaction_key, '')
        self.assertIsNone(payment.ocr_receipt_data)
        self.assertEqual(self.order().status, SalesOrder.CONFIRMED)
        self.assertEqual(response.data['customer_status'], 'payment_under_review')
        self.assertEqual(response.data['payments'][0]['review_status'], 'needs_review')
        self.assertFalse(OcrCallLog.objects.exists())  # OCR never ran

    def test_an_image_is_always_required(self):
        response = self.pay(receipt={'reference': 'REF123', 'paid_on': '2026-05-03', 'origin_bank': 'BDV'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'validation_error')
        self.assertEqual(Payment.objects.count(), 0)

    def test_receipt_details_are_required_so_a_reviewer_has_something_to_check(self):
        for missing in ('reference', 'paid_on', 'origin_bank'):
            receipt = self.receipt()
            del receipt[missing]
            self.assertEqual(self.pay(receipt=receipt).status_code, 400, missing)


class ServiceBoundaryTests(PaymentFixture):
    """The service does not rely on the serializer having dropped what a channel must not send."""

    def test_the_service_itself_ignores_claimed_evidence_and_a_claimed_amount(self):
        self.enable_ocr(False)
        channel_order = ChannelOrder.objects.get()

        payment = register_channel_payment(
            channel_order,
            payment_method=Payment.MOBILE_PAYMENT,
            # What is owed is the order's, not the caller's: a wrong amount in
            # the request neither changes the payment nor makes it fail.
            receipt=self.receipt(
                transaction_key='forged-key', amount_usd='999.00',
                ocr_receipt_data={'payment': {'amount': {'value': '500.00'}}},
            ),
        )

        self.assertEqual(payment.status, Payment.PENDING_REVIEW)
        self.assertEqual(payment.transaction_key, '')
        self.assertIsNone(payment.ocr_receipt_data)
        self.assertEqual(str(payment.amount), '10.00')


class EarlyRefusalTests(PaymentFixture):
    """What is already known is refused before an OCR call that costs money."""

    @responses.activate
    def test_a_payment_under_review_costs_no_ocr_call(self):
        self.enable_ocr(False)
        self.pay()
        self.enable_ocr(True)
        self.provider_reads()

        response = self.pay()

        self.assertEqual(response.data['code'], 'payment_under_review')
        self.assertEqual(len(responses.calls), 0)
        self.assertFalse(OcrCallLog.objects.exists())

    @responses.activate
    def test_an_order_that_cannot_take_a_payment_costs_no_ocr_call(self):
        SalesOrder.objects.filter(order_number=self.order_number).update(status=SalesOrder.CANCELLED)
        self.provider_reads()

        response = self.pay()

        self.assertEqual(response.data['code'], 'order_not_payable')
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_a_bad_image_costs_no_ocr_call(self):
        self.provider_reads()

        self.pay(receipt=self.receipt(receipt_image_base64='data:image/gif;base64,R0lGODlh'))

        self.assertEqual(len(responses.calls), 0)


class WhatChangedWhileOcrRanTests(PaymentFixture):
    @responses.activate
    def test_staff_recording_part_of_the_amount_meanwhile_stops_a_stale_receipt(self):
        self.provider_reads()  # the receipt says Bs 500.00: the whole 10.00
        real_validate = channel_payments.validate_receipt

        def staff_take_cash_meanwhile(*args, **kwargs):
            fields = real_validate(*args, **kwargs)
            Payment.objects.create(
                sales_order=self.order(), amount=4, payment_method=Payment.CASH, status=Payment.CONFIRMED,
                reference_number='', recorded_by=self.admin,
                currency_snapshot=ChannelOrder.objects.get().currency_snapshot,
            )
            return fields

        with patch('core.services.channel_payments.validate_receipt', side_effect=staff_take_cash_meanwhile):
            response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'amount_mismatch')
        self.assertEqual(Payment.objects.filter(payment_method=Payment.MOBILE_PAYMENT).count(), 0)
        self.assertEqual(self.order().status, SalesOrder.CONFIRMED)

    @responses.activate
    def test_the_same_receipt_spent_elsewhere_meanwhile_is_a_duplicate(self):
        self.provider_reads(transaction_key='ocr-key')
        other_order = self.place_order(reference='WEB-2').data['order_number']
        real_validate = channel_payments.validate_receipt

        def another_submission_spends_it(*args, **kwargs):
            fields = real_validate(*args, **kwargs)
            Payment.objects.create(
                sales_order=SalesOrder.objects.get(order_number=other_order), amount=10,
                payment_method=Payment.MOBILE_PAYMENT, status=Payment.CONFIRMED,
                reference_number='x', transaction_key='ocr-key', recorded_by=self.admin,
                currency_snapshot=ChannelOrder.objects.get(sales_order__order_number=other_order).currency_snapshot,
            )
            return fields

        with patch('core.services.channel_payments.validate_receipt', side_effect=another_submission_spends_it):
            response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'duplicate_transaction')
        self.assertFalse(Payment.objects.filter(sales_order=self.order()).exists())


class WhenOcrCannotDecideTests(PaymentFixture):
    def test_a_vepay_outage_leaves_the_payment_for_review(self):
        outage = VEPayError('timeout', 'VEPay timed out.', is_retryable=True)
        with _ocr_raises(outage):
            response = self.pay()

        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get()
        self.assertEqual((payment.status, payment.transaction_key), (Payment.PENDING_REVIEW, ''))
        log = OcrCallLog.objects.get()
        self.assertEqual((log.status, log.channel), ('timeout', self.channel))

    def test_an_unreadable_receipt_is_refused_but_the_call_is_still_logged(self):
        unreadable = VEPayError('receipt_parse_error', 'Could not read it.', is_retryable=False)
        with _ocr_raises(unreadable):
            response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'receipt_parse_error')
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(OcrCallLog.objects.get().status, 'receipt_parse_error')

    @responses.activate
    def test_ocr_without_a_transaction_key_waits_for_review(self):
        self.provider_reads(transaction_key='')

        self.pay()

        self.assertEqual(Payment.objects.get().status, Payment.PENDING_REVIEW)

    @responses.activate
    def test_a_receipt_that_does_not_match_the_details_given_is_refused(self):
        self.provider_reads(reference='999999')

        response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'receipt_field_mismatch')
        self.assertEqual(Payment.objects.count(), 0)

    @responses.activate
    def test_a_refusal_never_repeats_what_the_ocr_read(self):
        """
        The 422 answer says *which* of the store's fields did not match, never what the receipt showed:
        a store (or someone behind it) could otherwise tune a forged picture against the reading.
        """
        self.provider_reads(reference='OCR-READ-999', amount='12.34', transaction_key='OCR-BANK-KEY')

        mismatch = self.pay()

        self.assertEqual(mismatch.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(mismatch.data['code'], 'receipt_field_mismatch')
        self.assertEqual(set(mismatch.data['details']), {'mismatches'})
        for field, entry in mismatch.data['details']['mismatches'].items():
            self.assertEqual(set(entry), {'code'}, field)
        for leaked in ('OCR-READ-999', '12.34', 'OCR-BANK-KEY', 'expected', 'actual', 'receipt_fields'):
            self.assertNotIn(leaked, str(mismatch.data), leaked)

    @responses.activate
    def test_a_duplicate_receipt_does_not_name_the_bank_transaction(self):
        self.provider_reads(transaction_key='OCR-BANK-KEY')
        self.pay()
        other = self.place_order(reference='WEB-2').data['order_number']
        self.provider_reads(transaction_key='OCR-BANK-KEY')

        duplicate = self.pay(number=other)

        self.assertEqual(duplicate.data['code'], 'duplicate_transaction')
        self.assertNotIn('OCR-BANK-KEY', str(duplicate.data))
        self.assertNotIn('details', duplicate.data)

    @responses.activate
    def test_a_receipt_already_spent_is_refused(self):
        self.provider_reads()
        self.pay()
        other = self.place_order(reference='WEB-2').data['order_number']
        self.provider_reads()

        response = self.pay(number=other)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'duplicate_transaction')
        self.assertEqual(Payment.objects.count(), 1)

    @responses.activate
    def test_the_recipient_is_checked_when_the_store_requires_it(self):
        settings = SystemSettings.get()
        settings.recipient_validation_enabled = True
        settings.save()
        RecipientProfile.objects.create(
            payment_method=Payment.MOBILE_PAYMENT, phone='0414-9999999', bank='Banesco',
            document_id='J-1', is_primary=True, created_by=self.admin,
        )
        self.provider_reads()  # the receipt names Banesco but another phone

        response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data['code'], 'recipient_mismatch')


class WhenAnOrderCannotTakeAPaymentTests(PaymentFixture):
    @responses.activate
    def test_a_payment_under_review_blocks_another_until_it_is_settled(self):
        self.enable_ocr(False)
        first = self.pay()
        again = self.pay()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(again.data['code'], 'payment_under_review')
        self.assertEqual(Payment.objects.count(), 1)

    @responses.activate
    def test_after_a_rejection_the_customer_can_pay_again(self):
        self.enable_ocr(False)
        self.pay()
        reject_payment(Payment.objects.get(), user=self.admin, notes='Blurry receipt')

        status_after_rejection = self.client.get(f'{ORDERS}{self.order_number}/', **self.auth).data
        self.enable_ocr(True)
        self.provider_reads(transaction_key='second-key')
        retry = self.pay()

        self.assertEqual(status_after_rejection['customer_status'], 'awaiting_payment')
        self.assertEqual(status_after_rejection['payments'][0]['review_status'], 'rejected')
        self.assertNotIn('Blurry', str(status_after_rejection))  # the reviewer's note stays inside
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(
            [p['review_status'] for p in retry.data['payments']], ['rejected', 'verified'],
        )
        self.assertEqual(retry.data['customer_status'], 'processing')

    def test_when_staff_approve_the_order_is_paid_and_the_customer_sees_it(self):
        self.enable_ocr(False)
        self.pay()

        approve_payment(Payment.objects.get(), user=self.admin, transaction_key='bank-key')
        data = self.client.get(f'{ORDERS}{self.order_number}/', **self.auth).data

        self.assertEqual(self.order().status, SalesOrder.PAID)
        self.assertEqual(data['customer_status'], 'processing')
        self.assertEqual(data['payments'][0]['review_status'], 'verified')

    @responses.activate
    def test_a_paid_or_cancelled_order_takes_no_payment(self):
        self.provider_reads()
        self.pay()
        paid = self.pay()

        SalesOrder.objects.filter(order_number=self.order_number).update(status=SalesOrder.CANCELLED)
        cancelled = self.pay()

        self.assertEqual(paid.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual((paid.data['code'], paid.data['customer_status']), ('order_not_payable', 'processing'))
        self.assertEqual(cancelled.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(cancelled.data['customer_status'], 'cancelled')
        self.assertEqual(Payment.objects.count(), 1)

    def test_another_channels_order_is_a_404(self):
        _other, other_key = make_channel('other-shop', admin=self.admin)

        response = self.pay(auth=channel_auth(other_key))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Payment.objects.count(), 0)

    def test_only_receipt_methods_are_accepted(self):
        for method in ('card', 'cash', 'check', 'other', 'nonsense'):
            response = self.pay(method=method)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, method)

    def test_a_bad_image_is_refused_with_its_own_code(self):
        unsupported = self.pay(receipt=self.receipt(receipt_image_base64='data:image/gif;base64,R0lGODlh'))
        undecodable = self.pay(receipt=self.receipt(receipt_image_base64='data:image/png;base64,@@not base64@@'))

        self.assertEqual((unsupported.status_code, unsupported.data['code']), (415, 'unsupported_receipt_type'))
        self.assertEqual((undecodable.status_code, undecodable.data['code']), (400, 'invalid_receipt_image'))
        self.assertEqual(Payment.objects.count(), 0)


class LockingTests(PaymentFixture):
    @responses.activate
    def test_ocr_runs_before_the_order_is_locked(self):
        sequence = []
        real_lock = payment_service.lock_payable_order

        def spy(order_id, allowed):
            sequence.append('lock')
            return real_lock(order_id, allowed)

        def provider(request):
            sequence.append('ocr')
            return 200, {}, json.dumps(vepay_payload(transaction_key='ocr-key'))

        responses.add_callback(responses.POST, PROVIDER, callback=provider, content_type='application/json')
        with patch('core.services.channel_payments.lock_payable_order', side_effect=spy):
            response = self.pay()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(sequence, ['ocr', 'lock'])

    @responses.activate
    def test_an_order_cancelled_while_ocr_ran_takes_no_payment(self):
        self.provider_reads()
        real_lock = payment_service.lock_payable_order

        def cancel_then_lock(order_id, allowed):
            SalesOrder.objects.filter(pk=order_id).update(status=SalesOrder.CANCELLED)
            return real_lock(order_id, allowed)

        with patch('core.services.channel_payments.lock_payable_order', side_effect=cancel_then_lock):
            response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'order_not_payable')
        self.assertEqual(Payment.objects.count(), 0)

    def test_a_payment_that_arrived_while_ocr_ran_blocks_a_second(self):
        self.enable_ocr(False)
        real_validate = channel_payments.validate_receipt

        def validate_while_another_request_pays(*args, **kwargs):
            fields = real_validate(*args, **kwargs)
            # The competing submission commits while this one is still reading its receipt.
            Payment.objects.create(
                sales_order=self.order(), amount=10, payment_method=Payment.MOBILE_PAYMENT,
                status=Payment.PENDING_REVIEW, reference_number='first',
                recorded_by=self.channel.service_user,
                currency_snapshot=ChannelOrder.objects.get().currency_snapshot,
            )
            return fields

        with patch('core.services.channel_payments.validate_receipt',
                   side_effect=validate_while_another_request_pays):
            response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'payment_under_review')
        self.assertEqual(Payment.objects.count(), 1)
