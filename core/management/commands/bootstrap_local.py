"""Compatibility alias for local demo RetailOps initialization."""

from django.core.management.base import BaseCommand

from core.management.site_initialization import (
    DEFAULT_DEMO_USERS,
    add_demo_arguments,
    run_demo_initialization,
)


DEFAULT_USERS = DEFAULT_DEMO_USERS


class Command(BaseCommand):
    help = 'Compatibility alias for "init --demo". Prefer: python manage.py init --demo.'

    def add_arguments(self, parser):
        add_demo_arguments(parser)

    def handle(self, *args, **options):
        run_demo_initialization(self, options)
