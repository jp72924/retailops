"""Bootstrap a local RetailOps development database."""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.kiosk.provisioning import create_kiosk_station
from core.models import KioskStation, Role, SystemSettings, User


DEFAULT_USERS = [
    {
        'email': 'admin@retailops.local',
        'password': 'AdminPassword123!',
        'first_name': 'RetailOps',
        'last_name': 'Admin',
        'role': Role.ADMIN,
        'is_staff': True,
        'is_superuser': True,
    },
    {
        'email': 'manager@retailops.local',
        'password': 'ManagerPass123!',
        'first_name': 'RetailOps',
        'last_name': 'Manager',
        'role': Role.MANAGER,
        'is_staff': True,
        'is_superuser': False,
    },
    {
        'email': 'staff@retailops.local',
        'password': 'StaffPass123!',
        'first_name': 'RetailOps',
        'last_name': 'Staff',
        'role': Role.STAFF,
        'is_staff': False,
        'is_superuser': False,
    },
]


class Command(BaseCommand):
    help = 'Bootstrap roles, local demo users, settings, and optional kiosk station.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--seed',
            action='store_true',
            help='Populate sample catalog, customers, orders, payments, and inventory.',
        )
        parser.add_argument(
            '--force-seed',
            action='store_true',
            help='Clear existing sample business data before seeding.',
        )
        parser.add_argument(
            '--reset-passwords',
            action='store_true',
            help='Reset demo user passwords to the documented local defaults.',
        )
        parser.add_argument(
            '--provision-kiosk',
            action='store_true',
            help='Provision a local external Kiosk station and print its API key once.',
        )
        parser.add_argument('--store', default='DEV-LOCAL', help='Kiosk store identifier.')
        parser.add_argument('--station', default=1, type=int, help='Kiosk station number.')
        parser.add_argument('--kiosk-label', default='Local development kiosk')

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('bootstrap_local is only available when DEBUG=True.')

        with transaction.atomic():
            roles = self._ensure_roles()
            self._ensure_users(roles, reset_passwords=options['reset_passwords'])
            SystemSettings.get()

        self.stdout.write(self.style.SUCCESS('Local roles, users, and settings are ready.'))
        self._print_demo_credentials()

        if options['seed'] or options['force_seed']:
            seed_args = ['--force'] if options['force_seed'] else []
            call_command('seed', *seed_args)

        if options['provision_kiosk']:
            self._provision_kiosk(
                store=options['store'],
                station_number=options['station'],
                label=options['kiosk_label'],
            )

    def _ensure_roles(self):
        descriptions = {
            Role.ADMIN: 'Full system administration access.',
            Role.MANAGER: 'Operations management access.',
            Role.STAFF: 'Staff order and customer access.',
            Role.KIOSK: 'Service role for external RetailOps Kiosk stations.',
        }
        roles = {}
        for name, description in descriptions.items():
            role, _ = Role.objects.get_or_create(
                name=name,
                defaults={'description': description},
            )
            roles[name] = role
        return roles

    def _ensure_users(self, roles, reset_passwords=False):
        for spec in DEFAULT_USERS:
            user, created = User.objects.get_or_create(
                email=spec['email'],
                defaults={
                    'first_name': spec['first_name'],
                    'last_name': spec['last_name'],
                    'role': roles[spec['role']],
                    'is_staff': spec['is_staff'],
                    'is_superuser': spec['is_superuser'],
                    'is_active': True,
                },
            )
            changed_fields = []
            for field in ('first_name', 'last_name', 'is_staff', 'is_superuser'):
                if getattr(user, field) != spec[field]:
                    setattr(user, field, spec[field])
                    changed_fields.append(field)
            if user.role_id != roles[spec['role']].id:
                user.role = roles[spec['role']]
                changed_fields.append('role')
            if not user.is_active:
                user.is_active = True
                changed_fields.append('is_active')
            if created or reset_passwords:
                user.set_password(spec['password'])
                changed_fields.append('password')
            if changed_fields:
                user.save(update_fields=changed_fields)

    def _provision_kiosk(self, store, station_number, label):
        existing = KioskStation.objects.filter(
            store_identifier=store,
            station_number=station_number,
        ).first()
        if existing:
            self.stdout.write(self.style.WARNING(
                f'Kiosk station {store}/#{station_number} already exists. '
                'Its API key cannot be recovered; rotate or create another station if needed.'
            ))
            return

        admin = User.objects.get(email='admin@retailops.local')
        station, raw_key = create_kiosk_station(
            store_identifier=store,
            station_number=station_number,
            created_by=admin,
            label=label,
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Provisioned external Kiosk station: {station}'))
        self.stdout.write(self.style.WARNING('KIOSK_API_KEY (shown only once):'))
        self.stdout.write(f'    {raw_key}')
        self.stdout.write('Configure the external Kiosk project with:')
        self.stdout.write('    BASE_URL=http://127.0.0.1:8000')
        self.stdout.write('    API_PATH=/api/v1')
        self.stdout.write(f'    KIOSK_API_KEY={raw_key}')

    def _print_demo_credentials(self):
        self.stdout.write('')
        self.stdout.write('Local demo accounts:')
        for spec in DEFAULT_USERS:
            self.stdout.write(f'  {spec["email"]} / {spec["password"]}')
