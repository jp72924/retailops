from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from core.models import SystemSettings
from core.services.receipt_matching import (
    REQUIRED_FIELD_KEYS,
    compare_receipt_fields,
    match_recipient_profile,
    normalize_document_id,
    normalize_phone,
)


class ReceiptFieldMatchingTests(TestCase):
    def setUp(self):
        self.settings = SystemSettings.get()
        self.settings.secondary_currency_enabled = True
        self.settings.secondary_currency_code = 'VES'
        self.settings.secondary_exchange_rate = Decimal('50')
        self.settings.save()
        self.expected = {
            'amount_usd': Decimal('19.28'),
            'reference': '000123',
            'paid_on': '2026-05-03',
            'origin_bank': 'BDV',
        }

    def test_all_fields_match_with_normalization(self):
        result = self._compare(self._receipt_payload())

        self.assertTrue(result['matches'])
        self.assertEqual(result['field_matches'], {
            'amount_usd': True,
            'reference': True,
            'paid_on': True,
            'origin_bank': True,
        })
        self.assertEqual(result['mismatches'], {})

    def test_amount_mismatch_is_reported(self):
        receipt = self._receipt_payload()
        receipt['payment']['amount']['value'] = '100.00'

        result = self._compare(receipt)

        self.assertFalse(result['matches'])
        self.assertEqual(result['field_matches']['amount_usd'], False)
        self.assertEqual(result['mismatches']['amount_usd']['code'], 'receipt_field_mismatch')

    def test_reference_mismatch_is_reported(self):
        receipt = self._receipt_payload()
        receipt['payment']['reference'] = '999999'

        result = self._compare(receipt)

        self.assertFalse(result['matches'])
        self.assertEqual(result['field_matches']['reference'], False)

    def test_date_mismatch_is_reported(self):
        receipt = self._receipt_payload()
        receipt['payment']['date_time']['iso'] = '2026-05-04T15:42:00-04:00'

        result = self._compare(receipt)

        self.assertFalse(result['matches'])
        self.assertEqual(result['field_matches']['paid_on'], False)

    def test_bank_mismatch_is_reported(self):
        receipt = self._receipt_payload()
        receipt['payment']['bank_app'] = 'Mercantil'
        receipt['origin'].pop('bank')

        result = self._compare(receipt)

        self.assertFalse(result['matches'])
        self.assertEqual(result['field_matches']['origin_bank'], False)

    def test_missing_ocr_fields_are_reported(self):
        receipt = self._receipt_payload()
        receipt['payment'].pop('reference')
        receipt['payment'].pop('bank_app')
        receipt['origin'].pop('bank')

        result = self._compare(receipt)

        self.assertFalse(result['matches'])
        self.assertEqual(result['mismatches']['reference']['code'], 'missing_receipt_field')
        self.assertEqual(result['mismatches']['origin_bank']['code'], 'missing_receipt_field')

    def test_bank_app_fallback_and_aliases_match(self):
        receipt = self._receipt_payload()
        receipt['origin'].pop('bank')
        receipt['payment']['bank_app'] = 'Banco de Venezuela'

        result = self._compare(receipt)

        self.assertTrue(result['matches'])

    def test_source_account_prefix_overrides_misread_bank_app(self):
        receipt = self._receipt_payload()
        receipt['origin'] = {'account': '0102****3488'}
        receipt['payment']['bank_app'] = 'bancamiga'

        result = self._compare(receipt)

        self.assertTrue(result['field_matches']['origin_bank'])
        self.assertEqual(result['receipt_fields']['origin_bank'], 'BDV')

    def test_observed_vepay_envelope_is_not_a_flat_receipt(self):
        result = compare_receipt_fields(
            self._observed_bdv_envelope(),
            self.expected,
            self.settings,
            REQUIRED_FIELD_KEYS,
        )

        self.assertFalse(result['matches'])
        self.assertEqual(set(result['mismatches']), {
            'amount_usd',
            'reference',
            'paid_on',
            'origin_bank',
        })

    def test_observed_bdv_payload_matches_reference_date_and_bank_only(self):
        result = self._compare(self._observed_bdv_receipt())

        self.assertFalse(result['matches'])
        self.assertEqual(result['field_matches']['reference'], True)
        self.assertEqual(result['field_matches']['paid_on'], True)
        self.assertEqual(result['field_matches']['origin_bank'], True)
        self.assertEqual(result['field_matches']['amount_usd'], False)
        self.assertEqual(result['mismatches']['amount_usd']['code'], 'missing_receipt_field')

    def test_observed_bdv_bank_app_without_source_account_mismatches(self):
        receipt = self._observed_bdv_receipt()
        receipt['origin'].pop('account')

        result = self._compare(receipt)

        self.assertFalse(result['field_matches']['origin_bank'])
        self.assertEqual(result['mismatches']['origin_bank']['actual'], 'bancamiga')

    def test_corrected_observed_bdv_payload_matches(self):
        receipt = self._observed_bdv_receipt()
        receipt['payment']['amount']['value'] = '963.89'
        receipt['payment']['bank_app'] = 'BDV'

        result = self._compare(receipt)

        self.assertTrue(result['matches'])
        self.assertEqual(result['mismatches'], {})

    def _compare(self, receipt):
        return compare_receipt_fields(
            receipt,
            self.expected,
            self.settings,
            REQUIRED_FIELD_KEYS,
        )

    def _receipt_payload(self):
        return {
            'payment': {
                'bank_app': 'BDV',
                'reference': '000-123',
                'amount': {
                    'value': '963.89',
                    'currency': 'VES',
                },
                'date_time': {
                    'iso': '2026-05-03T15:42:00-04:00',
                },
            },
            'origin': {
                'bank': 'Banco de Venezuela',
            },
        }

    def _observed_bdv_envelope(self):
        return {
            'request_id': '82b61dc84de74e0a8682c8feb491229d',
            'errors': [],
            'receipts': [self._observed_bdv_receipt()],
            'summary': {'total': 1, 'complete': 0, 'incomplete': 1, 'errors': 0},
        }

    def _observed_bdv_receipt(self):
        return {
            'request_id': '82b61dc84de74e0a8682c8feb491229d',
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


class NormalizePhoneTests(TestCase):
    def test_various_formats_normalize_equal(self):
        expected = '4141234567'
        self.assertEqual(normalize_phone('0414-1234567'), expected)
        self.assertEqual(normalize_phone('+58 414 1234567'), expected)
        self.assertEqual(normalize_phone('58-414-1234567'), expected)
        self.assertEqual(normalize_phone('4141234567'), expected)

    def test_empty_and_none_are_empty_string(self):
        self.assertEqual(normalize_phone(''), '')
        self.assertEqual(normalize_phone(None), '')


class NormalizeDocumentIdTests(TestCase):
    def test_strips_punctuation_and_uppercases(self):
        self.assertEqual(normalize_document_id('v-12.345.678'), 'V12345678')
        self.assertEqual(normalize_document_id('V12345678'), 'V12345678')

    def test_empty_and_none_are_empty_string(self):
        self.assertEqual(normalize_document_id(''), '')
        self.assertEqual(normalize_document_id(None), '')


class RecipientProfileMatchingTests(TestCase):
    def setUp(self):
        self.profiles = [
            SimpleNamespace(pk=1, phone='4141234567', bank='Banco de Venezuela', document_id='V-12.345.678'),
            SimpleNamespace(pk=2, phone='04249999999', bank='Banesco', document_id='J987654321'),
        ]

    def _receipt(self, phone='0414-1234567', bank='BDV', document_id='v12345678'):
        return {'recipient': {'phone': phone, 'bank': bank, 'document_id': document_id}}

    def test_matching_receipt_finds_the_right_profile(self):
        result = match_recipient_profile(self._receipt(), self.profiles)

        self.assertTrue(result['matched'])
        self.assertEqual(result['matched_profile_id'], 1)
        self.assertEqual(result['checked_profiles_count'], 2)

    def test_mismatched_bank_does_not_match(self):
        result = match_recipient_profile(
            self._receipt(bank='Banesco'), self.profiles,
        )

        self.assertFalse(result['matched'])
        self.assertIsNone(result['matched_profile_id'])

    def test_unrelated_receipt_does_not_match_any_profile(self):
        result = match_recipient_profile(
            self._receipt(phone='0424-0000000', bank='Banesco', document_id='V00000000'),
            self.profiles,
        )

        self.assertFalse(result['matched'])

    def test_missing_recipient_field_is_treated_as_no_match(self):
        receipt = {'recipient': {'phone': '0414-1234567', 'bank': 'BDV'}}

        result = match_recipient_profile(receipt, self.profiles)

        self.assertFalse(result['matched'])
        self.assertEqual(result['receipt_fields']['document_id'], '')

    def test_empty_profile_list_never_matches(self):
        result = match_recipient_profile(self._receipt(), [])

        self.assertFalse(result['matched'])
        self.assertEqual(result['checked_profiles_count'], 0)
