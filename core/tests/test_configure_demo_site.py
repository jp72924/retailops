"""
Configuring a scratch RetailOps for the storefront demonstration.

What matters: it sets exactly what the demo flow needs (currencies, OCR for both receipt methods, one
account to pay into per method with recipient validation on, the kiosk no longer trusting client
evidence), running it twice changes nothing, and it refuses the default development database.
"""
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from api.tests.helpers import make_user
from core.models import Payment, RecipientProfile, Role, SystemSettings


def configure(**options):
    out = StringIO()
    call_command('configure_demo_site', stdout=out, **options)
    return out.getvalue()


class ConfigureDemoSiteTests(TestCase):
    def setUp(self):
        self.admin = make_user(Role.ADMIN, is_staff=True)

    def test_it_sets_the_currencies(self):
        configure(rate='36.50')

        settings = SystemSettings.get()
        self.assertEqual((settings.currency_code, settings.currency_symbol), ('USD', '$'))
        self.assertTrue(settings.secondary_currency_enabled)
        self.assertEqual((settings.secondary_currency_code, settings.secondary_currency_symbol), ('VES', 'Bs.'))
        self.assertEqual(settings.secondary_exchange_rate, Decimal('36.50000000'))
        self.assertFalse(settings.secondary_rate_auto_update_enabled)  # the demo must not reach the network

    def test_it_turns_ocr_on_for_both_receipt_methods(self):
        configure(ocr_url='http://127.0.0.1:9100')

        settings = SystemSettings.get()
        self.assertTrue(settings.ocr_enabled)
        self.assertEqual(settings.ocr_base_url, 'http://127.0.0.1:9100')
        self.assertEqual(sorted(settings.ocr_enabled_methods), [Payment.BANK_TRANSFER, Payment.MOBILE_PAYMENT])
        self.assertTrue(settings.receipt_image_required_for_receipt_methods)

    def test_it_creates_one_primary_account_per_method_and_validates_recipients(self):
        configure()

        primaries = RecipientProfile.objects.filter(is_primary=True, is_active=True)
        self.assertEqual(
            sorted(primaries.values_list('payment_method', flat=True)),
            [Payment.BANK_TRANSFER, Payment.MOBILE_PAYMENT],
        )
        self.assertTrue(SystemSettings.get().recipient_validation_enabled)

    def test_the_kiosk_no_longer_trusts_what_its_app_claims(self):
        self.assertTrue(SystemSettings.get().kiosk_trust_client_evidence)

        configure()

        self.assertFalse(SystemSettings.get().kiosk_trust_client_evidence)

    def test_running_it_twice_changes_nothing(self):
        configure()
        before = (RecipientProfile.objects.count(), SystemSettings.objects.count())

        configure()

        self.assertEqual((RecipientProfile.objects.count(), SystemSettings.objects.count()), before)

    def test_it_needs_a_staff_user_to_attribute_the_accounts_to(self):
        self.admin.delete()

        with self.assertRaises(CommandError):
            configure()

    def test_a_rate_that_is_not_a_number_is_a_clear_error(self):
        with self.assertRaises(CommandError):
            configure(rate='cheap')

        self.assertFalse(RecipientProfile.objects.exists())

    def test_it_refuses_the_default_development_database(self):
        with patch('core.management.commands.configure_demo_site.refuses_default_database', return_value=True):
            with self.assertRaises(CommandError) as raised:
                configure()

        self.assertIn('db.sqlite3', str(raised.exception))
        self.assertFalse(RecipientProfile.objects.exists())
        self.assertFalse(SystemSettings.get().ocr_enabled)

    def test_the_override_is_explicit(self):
        with patch('core.management.commands.configure_demo_site.refuses_default_database', return_value=True):
            configure(allow_default_db=True)

        self.assertTrue(SystemSettings.get().ocr_enabled)
