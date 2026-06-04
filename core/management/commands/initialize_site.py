"""Compatibility alias for operational RetailOps site initialization."""

from django.core.management.base import BaseCommand

from core.management.site_initialization import (
    OperationalSiteInitializer,
    add_operational_arguments,
)


class Command(BaseCommand):
    help = 'Compatibility alias for "init" operational setup. Prefer: python manage.py init.'

    def add_arguments(self, parser):
        add_operational_arguments(parser)

    def handle(self, *args, **options):
        OperationalSiteInitializer(self, options).run()
