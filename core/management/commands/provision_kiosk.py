"""Provision a new kiosk station and print its API key once.

    python manage.py provision_kiosk --store LAS-MERCEDES-01 --station 3
"""
from django.core.management.base import BaseCommand, CommandError

from api.kiosk.provisioning import create_kiosk_station
from core.models import KioskStation, User


class Command(BaseCommand):
    help = 'Provision a new kiosk station (creates service user + API key).'

    def add_arguments(self, parser):
        parser.add_argument('--store',   required=True, help='Store identifier, e.g. LAS-MERCEDES-01')
        parser.add_argument('--station', required=True, type=int, help='Station number (1-8)')
        parser.add_argument('--label',   default='', help='Human-friendly label')
        parser.add_argument('--by',      default=None, help='Email of the admin user provisioning this station')

    def handle(self, *args, **opts):
        store = opts['store']
        station_number = opts['station']

        if KioskStation.objects.filter(store_identifier=store, station_number=station_number).exists():
            raise CommandError(f'Station {store}/#{station_number} already exists.')

        if opts['by']:
            try:
                created_by = User.objects.get(email=opts['by'])
            except User.DoesNotExist as exc:
                raise CommandError(f'No user with email {opts["by"]}') from exc
        else:
            created_by = User.objects.filter(is_staff=True, is_active=True).order_by('pk').first()
            if created_by is None:
                raise CommandError('No staff user found to attribute provisioning to; pass --by <email>.')

        station, raw_key = create_kiosk_station(
            store_identifier=store,
            station_number=station_number,
            created_by=created_by,
            label=opts['label'],
        )

        self.stdout.write(self.style.SUCCESS(f'Provisioned kiosk station: {station}'))
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('API KEY (shown only once — copy now):'))
        self.stdout.write('')
        self.stdout.write(f'    {raw_key}')
        self.stdout.write('')
        self.stdout.write(f'Service user: {station.service_user.email}')
        self.stdout.write(f'Prefix:       {station.api_key_prefix}')
