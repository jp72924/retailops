import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import (
    Customer,
    InventoryMovement,
    KioskStation,
    Payment,
    Product,
    Role,
    SalesOrder,
    SystemSettings,
    User,
)


STRONG_PASSWORD = 'ProdReadyAdmin987!'


@override_settings(DEBUG=False)
class InitializeSiteCommandTests(TestCase):
    def call_initialize(self, *args, password=STRONG_PASSWORD):
        output = StringIO()
        env = {}
        if password is not None:
            env['RETAILOPS_INITIAL_ADMIN_PASSWORD'] = password
        with patch.dict(os.environ, env, clear=False):
            call_command('initialize_site', *args, stdout=output)
        return output.getvalue()

    def test_creates_operational_minimum_without_demo_data(self):
        self.call_initialize(
            '--no-input',
            '--yes',
            '--admin-email',
            'owner@example.com',
        )

        for role_name in [Role.ADMIN, Role.MANAGER, Role.STAFF, Role.KIOSK]:
            self.assertTrue(Role.objects.filter(name=role_name).exists())

        admin = User.objects.get(email='owner@example.com')
        self.assertEqual(admin.role.name, Role.ADMIN)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.check_password(STRONG_PASSWORD))
        self.assertEqual(SystemSettings.get().pk, 1)

        for model in (Customer, Product, SalesOrder, Payment, InventoryMovement):
            self.assertEqual(model.objects.count(), 0)

    def test_command_is_idempotent_and_does_not_reset_existing_admin_password(self):
        self.call_initialize(
            '--no-input',
            '--yes',
            '--admin-email',
            'owner@example.com',
        )
        self.call_initialize(
            '--no-input',
            '--yes',
            '--admin-email',
            'owner@example.com',
            password='AnotherStrongPass987!',
        )

        self.assertEqual(User.objects.filter(email='owner@example.com').count(), 1)
        admin = User.objects.get(email='owner@example.com')
        self.assertTrue(admin.check_password(STRONG_PASSWORD))
        self.assertFalse(admin.check_password('AnotherStrongPass987!'))

    def test_no_input_requires_admin_email_and_password(self):
        with self.assertRaises(CommandError):
            self.call_initialize('--no-input', '--yes')

        with self.assertRaises(CommandError):
            self.call_initialize(
                '--no-input',
                '--yes',
                '--admin-email',
                'owner@example.com',
                password=None,
            )

    def test_rejects_weak_or_demo_passwords(self):
        with self.assertRaises(CommandError):
            self.call_initialize(
                '--no-input',
                '--yes',
                '--admin-email',
                'owner@example.com',
                password='password',
            )

        with self.assertRaises(CommandError):
            self.call_initialize(
                '--no-input',
                '--yes',
                '--admin-email',
                'owner@example.com',
                password='AdminPassword123!',
            )

    def test_existing_non_admin_requires_explicit_profile_update(self):
        User.objects.create_user(
            email='owner@example.com',
            password='ExistingPass987!',
            first_name='Existing',
            last_name='User',
            is_staff=False,
            is_superuser=False,
            is_active=True,
        )

        with self.assertRaises(CommandError):
            self.call_initialize(
                '--no-input',
                '--yes',
                '--admin-email',
                'owner@example.com',
                password=None,
            )

        self.call_initialize(
            '--no-input',
            '--yes',
            '--admin-email',
            'owner@example.com',
            '--admin-first-name',
            'Store',
            '--admin-last-name',
            'Owner',
            '--update-admin-profile',
            password=None,
        )
        user = User.objects.get(email='owner@example.com')
        self.assertEqual(user.role.name, Role.ADMIN)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.first_name, 'Store')

    def test_can_apply_initial_settings(self):
        self.call_initialize(
            '--no-input',
            '--yes',
            '--admin-email',
            'owner@example.com',
            '--currency-code',
            'USD',
            '--currency-symbol',
            '$',
            '--enable-secondary-currency',
            '--secondary-currency-code',
            'VES',
            '--secondary-currency-symbol',
            'Bs.',
            '--secondary-exchange-rate',
            '486.81313131',
            '--ocr-enabled-methods',
            'mobile_payment,bank_transfer',
            '--receipt-retention-days',
            '45',
        )

        settings = SystemSettings.get()
        self.assertEqual(settings.currency_code, 'USD')
        self.assertTrue(settings.secondary_currency_enabled)
        self.assertEqual(settings.secondary_currency_code, 'VES')
        self.assertEqual(settings.ocr_enabled_methods, ['mobile_payment', 'bank_transfer'])
        self.assertFalse(settings.ocr_enabled)
        self.assertEqual(settings.delete_receipt_image_after_days, 45)

    def test_can_provision_kiosk_stations_and_skip_existing_ones(self):
        output = self.call_initialize(
            '--no-input',
            '--yes',
            '--admin-email',
            'owner@example.com',
            '--store',
            'MAIN',
            '--station-start',
            '3',
            '--station-count',
            '2',
            '--kiosk-label-prefix',
            'Checkout',
        )

        self.assertIn('MAIN / Station 3', output)
        self.assertIn('MAIN / Station 4', output)
        self.assertEqual(KioskStation.objects.filter(store_identifier='MAIN').count(), 2)
        station = KioskStation.objects.get(store_identifier='MAIN', station_number=3)
        self.assertEqual(station.label, 'Checkout 3')
        self.assertEqual(station.service_user.role.name, Role.KIOSK)

        second_output = self.call_initialize(
            '--no-input',
            '--yes',
            '--admin-email',
            'owner@example.com',
            '--store',
            'MAIN',
            '--station-start',
            '3',
            '--station-count',
            '2',
            password=None,
        )
        self.assertIn('already exists', second_output)
        self.assertEqual(KioskStation.objects.filter(store_identifier='MAIN').count(), 2)
