"""Public RetailOps site initialization command."""

from django.core.management.base import BaseCommand, CommandError

from core.management.site_initialization import (
    OperationalSiteInitializer,
    add_demo_arguments,
    add_operational_arguments,
    add_store_argument,
    run_demo_initialization,
)


class Command(BaseCommand):
    help = 'Initialize RetailOps. Operational setup by default; use --demo for local sample data.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--demo',
            action='store_true',
            help='Use local demo initialization with documented users and optional sample data.',
        )
        add_store_argument(parser)
        add_operational_arguments(parser, include_store=False)
        add_demo_arguments(parser, include_store=False)

    def handle(self, *args, **options):
        if options['demo']:
            run_demo_initialization(self, options)
            return

        invalid_demo_flags = [
            flag
            for flag in ('seed', 'force_seed', 'reset_passwords', 'provision_kiosk')
            if options.get(flag)
        ]
        if invalid_demo_flags:
            flags = ', '.join('--' + flag.replace('_', '-') for flag in invalid_demo_flags)
            raise CommandError(f'{flags} require --demo. Use --station-count for operational Kiosk setup.')

        OperationalSiteInitializer(self, options).run()
