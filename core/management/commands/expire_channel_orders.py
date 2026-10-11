"""Cancel the channel orders that lapsed unpaid and give their stock back.

    python manage.py expire_channel_orders
    python manage.py expire_channel_orders --dry-run

Meant for cron, every few minutes. An unpaid order holds stock until its
`expires_at`; this is what releases it. An order with any payment, even one
waiting for review, is never cancelled here. Safe to run twice.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.services.channel_orders import expire_channel_orders, lapsed_channel_orders


class Command(BaseCommand):
    help = 'Cancel channel orders that lapsed unpaid and restore their stock.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only count what would be cancelled.')
        parser.add_argument('--limit', type=int, default=500, help='Most orders to cancel in one run.')

    def handle(self, *args, **opts):
        now = timezone.now()
        if opts['dry_run']:
            due = min(lapsed_channel_orders(now).count(), opts['limit'])
            self.stdout.write(f'{due} order(s) would be cancelled.')
            return

        result = expire_channel_orders(now=now, limit=opts['limit'])
        self.stdout.write(self.style.SUCCESS(
            f'Cancelled {result.expired} lapsed order(s); skipped {result.skipped}.'
        ))
