from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import KioskStation, Role, SystemSettings, User


@override_settings(DEBUG=True)
class BootstrapLocalCommandTests(TestCase):
    def test_creates_roles_users_and_settings(self):
        call_command('bootstrap_local', stdout=StringIO())

        for role_name in [Role.ADMIN, Role.MANAGER, Role.STAFF, Role.KIOSK]:
            self.assertTrue(Role.objects.filter(name=role_name).exists())

        admin = User.objects.get(email='admin@retailops.local')
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertEqual(admin.role.name, Role.ADMIN)
        self.assertTrue(admin.check_password('AdminPassword123!'))
        self.assertEqual(SystemSettings.get().pk, 1)

    def test_can_provision_external_kiosk_station(self):
        call_command('bootstrap_local', '--provision-kiosk', stdout=StringIO())

        station = KioskStation.objects.get(store_identifier='DEV-LOCAL', station_number=1)
        self.assertTrue(station.is_active)
        self.assertEqual(station.service_user.role.name, Role.KIOSK)

    @override_settings(DEBUG=False)
    def test_refuses_to_run_outside_debug_mode(self):
        with self.assertRaises(CommandError):
            call_command('bootstrap_local')
