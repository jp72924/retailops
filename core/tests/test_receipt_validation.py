"""
The receipt-validation policies.

KIOSK_POLICY must keep the kiosk's behaviour exactly; the kiosk checkout suites
are its regression net. These pin what CHANNEL_POLICY does differently: an
internet channel never gets to vouch for its own receipt.
"""
import base64
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from core.models import Payment
from core.services.currency import CurrencyContext
from core.services.receipt_validation import (
    CHANNEL_POLICY,
    KIOSK_POLICY,
    InvalidReceiptImageError,
    ReceiptValidationError,
    validate_receipt,
)
from core.services.vepay import VEPayError
from api.tests.helpers import set_currency, vepay_payload

ORDER_TOTAL = Decimal('10.00')  # 500 VES at the 50 Bs/USD set_currency() default


def _image():
    return 'data:image/png;base64,' + base64.b64encode(b'test receipt image bytes').decode()


def _receipt(**overrides):
    receipt = {
        'reference': 'REF123',
        'paid_on': '2026-05-03',
        'origin_bank': 'BDV',
        'receipt_image_base64': _image(),
    }
    receipt.update(overrides)
    return receipt


def _ocr_returns(payload):
    return patch(
        'core.services.receipt_validation.async_to_sync',
        lambda _fn: (lambda *args, **kwargs: payload),
    )


def _ocr_raises(error):
    def fail(*args, **kwargs):
        raise error
    return patch('core.services.receipt_validation.async_to_sync', lambda _fn: fail)


class ReceiptPolicyTests(TestCase):
    def setUp(self):
        self.settings = set_currency()
        self.settings.ocr_enabled = True
        self.settings.ocr_base_url = 'https://vepay.test'
        self.settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER]
        self.settings.save()

    def _validate(self, receipt, policy):
        return validate_receipt(
            receipt,
            Payment.MOBILE_PAYMENT,
            ORDER_TOTAL,
            settings=self.settings,
            currency=CurrencyContext.from_settings(self.settings),
            policy=policy,
        )

    def test_a_channel_takes_the_transaction_key_from_ocr_not_the_request(self):
        with _ocr_returns(vepay_payload(amount='500.00', transaction_key='ocr-key')):
            result = self._validate(_receipt(transaction_key='client-key'), CHANNEL_POLICY)

        self.assertEqual(result['status'], Payment.CONFIRMED)
        self.assertEqual(result['transaction_key'], 'ocr-key')

    def test_a_channel_cannot_vouch_for_its_own_receipt_when_ocr_is_off(self):
        self.settings.ocr_enabled = False
        self.settings.save()
        forged = _receipt(transaction_key='forged-key', ocr_receipt_data={'payment': {}})

        result = self._validate(forged, CHANNEL_POLICY)

        self.assertEqual(result['status'], Payment.PENDING_REVIEW)
        self.assertEqual(result['transaction_key'], '')
        self.assertIsNone(result['ocr_receipt_data'])

    def test_the_kiosk_still_refuses_a_receipt_when_ocr_is_off(self):
        self.settings.ocr_enabled = False
        self.settings.save()
        with self.assertRaises(ReceiptValidationError) as caught:
            self._validate(_receipt(), KIOSK_POLICY)
        self.assertEqual(caught.exception.code, 'ocr_disabled')

    def test_a_vepay_outage_leaves_a_channel_payment_for_review(self):
        outage = VEPayError('timeout', 'VEPay timed out.', is_retryable=True)
        with _ocr_raises(outage):
            result = self._validate(_receipt(transaction_key='client-key'), CHANNEL_POLICY)

        self.assertEqual(result['status'], Payment.PENDING_REVIEW)
        self.assertEqual(result['transaction_key'], '')

    def test_an_unreadable_receipt_is_still_refused_for_a_channel(self):
        # Not an outage: the customer should retake the photo, not wait.
        unreadable = VEPayError('receipt_parse_error', 'Could not read it.', is_retryable=False)
        with _ocr_raises(unreadable), self.assertRaises(ReceiptValidationError) as caught:
            self._validate(_receipt(), CHANNEL_POLICY)
        self.assertEqual(caught.exception.http_status, 422)

    def test_a_receipt_that_does_not_match_is_refused_for_a_channel(self):
        with _ocr_returns(vepay_payload(amount='400.00')), \
                self.assertRaises(ReceiptValidationError) as caught:
            self._validate(_receipt(), CHANNEL_POLICY)
        self.assertEqual(caught.exception.code, 'receipt_field_mismatch')


class ImageRequirementTests(TestCase):
    def setUp(self):
        self.settings = set_currency()
        self.settings.receipt_image_required_for_receipt_methods = False
        self.settings.save()

    def _validate(self, receipt, policy):
        return validate_receipt(
            receipt,
            Payment.MOBILE_PAYMENT,
            ORDER_TOTAL,
            settings=self.settings,
            currency=CurrencyContext.from_settings(self.settings),
            policy=policy,
        )

    def test_a_channel_always_needs_an_image(self):
        with self.assertRaises(InvalidReceiptImageError) as caught:
            self._validate({'reference': 'REF123', 'transaction_key': 'client-key'}, CHANNEL_POLICY)
        self.assertEqual(caught.exception.code, 'receipt_image_required')

    def test_the_kiosk_keeps_trusting_its_own_key_without_an_image(self):
        # The existing kiosk behaviour, pinned so the extraction cannot change
        # it silently. It is acceptable only because a kiosk is a provisioned
        # device; CHANNEL_POLICY never does this.
        result = self._validate({'transaction_key': 'kiosk-key'}, KIOSK_POLICY)
        self.assertEqual(result['status'], Payment.CONFIRMED)
        self.assertEqual(result['transaction_key'], 'kiosk-key')
