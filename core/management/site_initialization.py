"""Shared site initialization helpers for RetailOps management commands."""

import getpass
import os
import sys
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction

from api.kiosk.provisioning import create_kiosk_station
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


DEFAULT_DEMO_USERS = [
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

DEMO_PASSWORDS = {spec['password'] for spec in DEFAULT_DEMO_USERS}
BUSINESS_DATA_MODELS = (Customer, Product, SalesOrder, Payment, InventoryMovement)


def add_store_argument(parser, *, default=None):
    parser.add_argument('--store', default=default, help='Store identifier for optional Kiosk station provisioning.')


def add_operational_arguments(parser, *, include_store=True):
    parser.add_argument('--admin-email', help='Email for the first operational admin user.')
    parser.add_argument('--admin-first-name', default='Store', help='Admin first name.')
    parser.add_argument('--admin-last-name', default='Owner', help='Admin last name.')
    parser.add_argument(
        '--admin-password-env',
        default='RETAILOPS_INITIAL_ADMIN_PASSWORD',
        help='Environment variable containing the initial admin password.',
    )
    parser.add_argument(
        '--update-admin-profile',
        action='store_true',
        help='Update names and admin privileges if the admin email already exists.',
    )
    parser.add_argument('--no-input', action='store_true', help='Disable interactive prompts.')
    parser.add_argument('--yes', action='store_true', help='Confirm initialization non-interactively.')

    parser.add_argument('--currency-code', help='Initial primary currency code.')
    parser.add_argument('--currency-symbol', help='Initial primary currency symbol.')
    parser.add_argument('--decimal-places', type=int, help='Initial primary currency decimal places.')
    parser.add_argument(
        '--enable-secondary-currency',
        action='store_true',
        help='Enable secondary currency display during initialization.',
    )
    parser.add_argument('--secondary-currency-code', help='Secondary currency code.')
    parser.add_argument('--secondary-currency-symbol', help='Secondary currency symbol.')
    parser.add_argument('--secondary-decimal-places', type=int, help='Secondary currency decimal places.')
    parser.add_argument('--secondary-exchange-rate', help='Secondary exchange rate.')
    parser.add_argument('--ocr-enabled', action='store_true', help='Enable OCR at initialization.')
    parser.add_argument(
        '--ocr-enabled-methods',
        help='Comma-separated payment methods where OCR should be enabled.',
    )
    parser.add_argument('--receipt-retention-days', type=int, help='Days to retain receipt images.')

    if include_store:
        add_store_argument(parser)
    parser.add_argument('--station-count', default=0, type=int, help='Number of Kiosk stations to create.')
    parser.add_argument('--station-start', default=1, type=int, help='First Kiosk station number.')
    parser.add_argument(
        '--kiosk-label-prefix',
        default='Kiosk station',
        help='Label prefix for created Kiosk stations.',
    )


def add_demo_arguments(parser, *, include_store=True):
    parser.add_argument(
        '--seed',
        action='store_true',
        help='Populate sample catalog, customers, orders, payments, and inventory. Requires --demo on init.',
    )
    parser.add_argument(
        '--force-seed',
        action='store_true',
        help='Clear existing sample business data before seeding. Requires --demo on init.',
    )
    parser.add_argument(
        '--reset-passwords',
        action='store_true',
        help='Reset demo user passwords to the documented local defaults.',
    )
    parser.add_argument(
        '--provision-kiosk',
        action='store_true',
        help='Provision a local demo Kiosk station and print its API key once.',
    )
    if include_store:
        add_store_argument(parser, default='DEV-LOCAL')
    parser.add_argument('--station', default=1, type=int, help='Demo Kiosk station number.')
    parser.add_argument('--kiosk-label', default='Local development kiosk')


def ensure_roles():
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


def ensure_demo_users(roles, *, reset_passwords=False):
    for spec in DEFAULT_DEMO_USERS:
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


def print_demo_credentials(command):
    command.stdout.write('')
    command.stdout.write('Local demo accounts:')
    for spec in DEFAULT_DEMO_USERS:
        command.stdout.write(f'  {spec["email"]} / {spec["password"]}')


def run_demo_initialization(command, options):
    if not settings.DEBUG:
        raise CommandError('Demo initialization is only available when DEBUG=True.')

    with transaction.atomic():
        roles = ensure_roles()
        ensure_demo_users(roles, reset_passwords=options['reset_passwords'])
        SystemSettings.get()

    command.stdout.write(command.style.SUCCESS('Local demo roles, users, and settings are ready.'))
    print_demo_credentials(command)

    if options['seed'] or options['force_seed']:
        seed_args = ['--force'] if options['force_seed'] else []
        call_command('seed', *seed_args)

    if options['provision_kiosk']:
        provision_demo_kiosk(
            command,
            store=options.get('store') or 'DEV-LOCAL',
            station_number=options['station'],
            label=options['kiosk_label'],
        )


def provision_demo_kiosk(command, *, store, station_number, label):
    existing = KioskStation.objects.filter(
        store_identifier=store,
        station_number=station_number,
    ).first()
    if existing:
        command.stdout.write(command.style.WARNING(
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

    command.stdout.write('')
    command.stdout.write(command.style.SUCCESS(f'Provisioned external Kiosk station: {station}'))
    command.stdout.write(command.style.WARNING('KIOSK_API_KEY (shown only once):'))
    command.stdout.write(f'    {raw_key}')
    command.stdout.write('Configure the external Kiosk project with:')
    command.stdout.write('    BASE_URL=http://127.0.0.1:8000')
    command.stdout.write('    API_PATH=/api/v1')
    command.stdout.write(f'    KIOSK_API_KEY={raw_key}')


class OperationalSiteInitializer:
    def __init__(self, command, options):
        self.command = command
        self.options = options

    def run(self):
        self._validate_options()

        admin_email = self._resolve_admin_email()
        password = self._resolve_admin_password(admin_email)

        if not self.options['yes']:
            self._confirm()

        with transaction.atomic():
            roles = ensure_roles()
            settings_created, settings_changes = self._ensure_settings()
            admin, admin_created, admin_changes = self._ensure_admin(
                roles=roles,
                email=admin_email,
                first_name=self.options['admin_first_name'],
                last_name=self.options['admin_last_name'],
                password=password,
                update_profile=self.options['update_admin_profile'],
            )
            created_stations, existing_stations = self._ensure_kiosk_stations(
                admin=admin,
                store=self.options['store'],
                station_count=self.options['station_count'],
                station_start=self.options['station_start'],
                label_prefix=self.options['kiosk_label_prefix'],
            )

        self._print_summary(
            admin=admin,
            admin_created=admin_created,
            admin_changes=admin_changes,
            settings_created=settings_created,
            settings_changes=settings_changes,
            created_stations=created_stations,
            existing_stations=existing_stations,
        )

    def _validate_options(self):
        if self.options['station_count'] < 0:
            raise CommandError('--station-count cannot be negative.')
        if self.options['station_start'] < 1:
            raise CommandError('--station-start must be greater than zero.')
        if self.options['station_count'] > 0 and not (self.options['store'] or '').strip():
            raise CommandError('--store is required when --station-count is greater than zero.')
        if self.options['no_input'] and not self.options['yes']:
            raise CommandError('--no-input requires --yes so initialization is explicit.')

    def _resolve_admin_email(self):
        email = (self.options['admin_email'] or '').strip()
        if email:
            return email
        if self.options['no_input']:
            raise CommandError('--admin-email is required with --no-input.')
        return self._prompt('Admin email', required=True)

    def _resolve_admin_password(self, admin_email):
        existing = User.objects.filter(email=admin_email).first()
        if existing:
            return None

        env_name = (self.options['admin_password_env'] or '').strip()
        password = os.environ.get(env_name, '') if env_name else ''
        if not password and self.options['no_input']:
            raise CommandError(
                f'Environment variable {env_name or "<empty>"} must contain the initial '
                'admin password with --no-input.'
            )
        if not password:
            password = self._prompt_password()

        self._validate_admin_password(password, admin_email)
        return password

    def _prompt(self, label, required=False):
        try:
            value = input(f'{label}: ').strip()
        except EOFError as exc:
            raise CommandError(f'{label} is required, but stdin is not interactive.') from exc
        if required and not value:
            raise CommandError(f'{label} is required.')
        return value

    def _prompt_password(self):
        if not sys.stdin.isatty():
            raise CommandError(
                'Initial admin password is required. Set RETAILOPS_INITIAL_ADMIN_PASSWORD '
                'or pass --admin-password-env.'
            )
        password = getpass.getpass('Initial admin password: ')
        confirmation = getpass.getpass('Confirm initial admin password: ')
        if password != confirmation:
            raise CommandError('Passwords do not match.')
        return password

    def _confirm(self):
        if self.options['no_input']:
            raise CommandError('--yes is required with --no-input.')
        try:
            answer = input('Initialize this RetailOps site now? Type "yes" to continue: ')
        except EOFError as exc:
            raise CommandError('Use --yes for non-interactive initialization.') from exc
        if answer.strip().lower() != 'yes':
            raise CommandError('Initialization cancelled.')

    def _validate_admin_password(self, password, admin_email):
        if not password:
            raise CommandError('Initial admin password cannot be empty.')
        if password in DEMO_PASSWORDS:
            raise CommandError('Initial admin password cannot use a documented demo password.')
        candidate = User(email=admin_email, first_name='Store', last_name='Owner')
        try:
            validate_password(password, user=candidate)
        except ValidationError as exc:
            raise CommandError('Initial admin password is not strong enough: ' + '; '.join(exc.messages)) from exc

    def _ensure_settings(self):
        settings_obj, created = SystemSettings.objects.get_or_create(pk=1)
        changes = []

        field_values = {
            'currency_code': self.options['currency_code'],
            'currency_symbol': self.options['currency_symbol'],
            'decimal_places': self.options['decimal_places'],
            'secondary_currency_code': self.options['secondary_currency_code'],
            'secondary_currency_symbol': self.options['secondary_currency_symbol'],
            'secondary_decimal_places': self.options['secondary_decimal_places'],
            'delete_receipt_image_after_days': self.options['receipt_retention_days'],
        }
        for field, value in field_values.items():
            if value is not None and getattr(settings_obj, field) != value:
                setattr(settings_obj, field, value)
                changes.append(field)

        if self.options['enable_secondary_currency'] and not settings_obj.secondary_currency_enabled:
            settings_obj.secondary_currency_enabled = True
            changes.append('secondary_currency_enabled')

        if self.options['secondary_exchange_rate'] is not None:
            try:
                exchange_rate = Decimal(self.options['secondary_exchange_rate'])
            except InvalidOperation as exc:
                raise CommandError('--secondary-exchange-rate must be a decimal value.') from exc
            if settings_obj.secondary_exchange_rate != exchange_rate:
                settings_obj.secondary_exchange_rate = exchange_rate
                changes.append('secondary_exchange_rate')

        if self.options['ocr_enabled'] and not settings_obj.ocr_enabled:
            settings_obj.ocr_enabled = True
            changes.append('ocr_enabled')

        if self.options['ocr_enabled_methods'] is not None:
            methods = [
                method.strip()
                for method in self.options['ocr_enabled_methods'].split(',')
                if method.strip()
            ]
            if settings_obj.ocr_enabled_methods != methods:
                settings_obj.ocr_enabled_methods = methods
                changes.append('ocr_enabled_methods')

        if changes:
            try:
                settings_obj.full_clean()
            except ValidationError as exc:
                raise CommandError(f'Invalid system settings: {exc.message_dict}') from exc
            settings_obj.save(update_fields=sorted(set(changes)))

        return created, sorted(set(changes))

    def _ensure_admin(self, roles, email, first_name, last_name, password, update_profile):
        admin_role = roles[Role.ADMIN]
        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create_user(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                role=admin_role,
                is_staff=True,
                is_superuser=True,
                is_active=True,
            )
            return user, True, ['created']

        required_admin_state = (
            user.is_active
            and user.is_staff
            and user.is_superuser
            and user.role_id == admin_role.id
        )
        if not required_admin_state and not update_profile:
            raise CommandError(
                f'User {email} already exists but is not an active Admin. '
                'Rerun with --update-admin-profile if this account should become the site admin.'
            )

        changes = []
        if update_profile:
            for field, value in (
                ('first_name', first_name),
                ('last_name', last_name),
                ('is_staff', True),
                ('is_superuser', True),
                ('is_active', True),
            ):
                if getattr(user, field) != value:
                    setattr(user, field, value)
                    changes.append(field)
            if user.role_id != admin_role.id:
                user.role = admin_role
                changes.append('role')
            if changes:
                user.save(update_fields=changes)

        return user, False, changes

    def _ensure_kiosk_stations(self, admin, store, station_count, station_start, label_prefix):
        created_stations = []
        existing_stations = []
        if station_count <= 0:
            return created_stations, existing_stations

        for station_number in range(station_start, station_start + station_count):
            existing = KioskStation.objects.filter(
                store_identifier=store,
                station_number=station_number,
            ).first()
            if existing:
                existing_stations.append(existing)
                continue

            label = f'{label_prefix} {station_number}'.strip()
            station, raw_key = create_kiosk_station(
                store_identifier=store,
                station_number=station_number,
                created_by=admin,
                label=label,
            )
            created_stations.append((station, raw_key))
        return created_stations, existing_stations

    def _print_summary(
        self,
        admin,
        admin_created,
        admin_changes,
        settings_created,
        settings_changes,
        created_stations,
        existing_stations,
    ):
        self.command.stdout.write(self.command.style.SUCCESS('RetailOps site initialization complete.'))
        self.command.stdout.write('')
        self.command.stdout.write(f'Admin user: {admin.email} ({"created" if admin_created else "existing"})')
        if admin_changes and not admin_created:
            self.command.stdout.write(f'Admin updates: {", ".join(admin_changes)}')
        self.command.stdout.write(f'System settings: {"created" if settings_created else "existing"}')
        if settings_changes:
            self.command.stdout.write(f'Settings updates: {", ".join(settings_changes)}')

        if created_stations:
            self.command.stdout.write('')
            self.command.stdout.write(self.command.style.WARNING('Kiosk API keys are shown only once:'))
            for station, raw_key in created_stations:
                self.command.stdout.write(f'  {station.store_identifier} / Station {station.station_number}: {raw_key}')
        if existing_stations:
            self.command.stdout.write('')
            for station in existing_stations:
                self.command.stdout.write(self.command.style.WARNING(
                    f'Kiosk station {station.store_identifier}/#{station.station_number} already exists; '
                    'its API key cannot be recovered from the database.'
                ))

        if demo_data_exists():
            self.command.stdout.write('')
            self.command.stdout.write(self.command.style.WARNING(
                'Existing business records were detected. init did not create or remove them.'
            ))


def demo_data_exists():
    return any(model.objects.exists() for model in BUSINESS_DATA_MODELS)
