import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import Customer, Product, Role, SalesOrder, SystemSettings, User


STRONG_PASSWORD = 'ProdReadyAdmin987!'


class InitCommandTests(TestCase):
    def call_init(self, *args, password=STRONG_PASSWORD):
        output = StringIO()
        env = {}
        if password is not None:
            env['RETAILOPS_INITIAL_ADMIN_PASSWORD'] = password
        with patch.dict(os.environ, env, clear=False):
            call_command('init', *args, stdout=output)
        return output.getvalue()

    @override_settings(DEBUG=False)
    def test_default_is_operational_setup_without_demo_data(self):
        self.call_init('--no-input', '--yes', '--admin-email', 'owner@example.com')

        admin = User.objects.get(email='owner@example.com')
        self.assertEqual(admin.role.name, Role.ADMIN)
        self.assertTrue(admin.check_password(STRONG_PASSWORD))
        self.assertEqual(SystemSettings.get().pk, 1)
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(SalesOrder.objects.count(), 0)

    @override_settings(DEBUG=True)
    def test_demo_mode_creates_documented_demo_users(self):
        self.call_init('--demo', password=None)

        admin = User.objects.get(email='admin@retailops.local')
        self.assertTrue(admin.check_password('AdminPassword123!'))
        self.assertEqual(admin.role.name, Role.ADMIN)

    @override_settings(DEBUG=True)
    def test_demo_seed_loads_sample_business_data(self):
        self.call_init('--demo', '--seed', password=None)

        self.assertGreater(Customer.objects.count(), 0)
        self.assertGreater(Product.objects.count(), 0)
        self.assertGreater(SalesOrder.objects.count(), 0)

    @override_settings(DEBUG=False)
    def test_demo_mode_refuses_outside_debug(self):
        with self.assertRaises(CommandError):
            self.call_init('--demo', password=None)

    @override_settings(DEBUG=False)
    def test_demo_flags_require_demo_mode(self):
        for flag in ('--seed', '--force-seed', '--reset-passwords', '--provision-kiosk'):
            with self.subTest(flag=flag):
                with self.assertRaises(CommandError):
                    self.call_init(
                        flag,
                        '--no-input',
                        '--yes',
                        '--admin-email',
                        'owner@example.com',
                    )
