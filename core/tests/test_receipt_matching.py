from decimal import Decimal

from django.test import TestCase

from core.models import SystemSettings
from core.services.receipt_matching import REQUIRED_FIELD_KEYS, compare_receipt_fields


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
