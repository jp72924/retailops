from django.core.management.base import BaseCommand, CommandError

from core.models import SystemSettings
from core.services.bcv import BCVRateError, fetch_rate, update_secondary_exchange_rate


class Command(BaseCommand):
    help = (
        'Fetch the secondary-currency exchange rate (e.g. BCV via DolarApi) from '
        'the configured source and update SystemSettings.secondary_exchange_rate.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Fetch and print the rate without saving it.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Run even when automatic rate update is disabled in settings.',
        )

    def handle(self, *args, **options):
        settings = SystemSettings.get()

        if not settings.secondary_rate_auto_update_enabled and not options['force']:
            raise CommandError(
                'Automatic rate update is disabled. Enable it in System Settings '
                'or pass --force.'
            )

        try:
            if options['dry_run']:
                rate = fetch_rate(settings)
                self.stdout.write(self.style.WARNING(
                    f'Fetched rate {rate} (dry run, not saved).'
                ))
                return
            rate = update_secondary_exchange_rate(settings)
        except BCVRateError as exc:
            raise CommandError(f'Rate update failed [{exc.code}]: {exc.message}')

        self.stdout.write(self.style.SUCCESS(
            f'Secondary exchange rate updated to {rate}.'
        ))
