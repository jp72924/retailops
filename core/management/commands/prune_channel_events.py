"""Delete old channel events and their finished deliveries.

    python manage.py prune_channel_events --days 30
    python manage.py prune_channel_events --days 30 --dry-run

An event is kept for `--days` so a store that was away can still catch up from the feed; after that
it is deleted, with its deliveries. An event that still has a delivery waiting to be retried is kept
whatever its age. Meant for cron, daily.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef
from django.utils import timezone

from core.models import ChannelEvent, WebhookDelivery


class Command(BaseCommand):
    help = 'Delete channel events older than --days, and their deliveries.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30, help='How long an event is kept. Default 30.')
        parser.add_argument('--dry-run', action='store_true', help='Only count what would be deleted.')

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(days=opts['days'])
        waiting = WebhookDelivery.objects.filter(event=OuterRef('pk'), state=WebhookDelivery.PENDING)
        old = ChannelEvent.objects.filter(created_at__lt=cutoff).filter(~Exists(waiting))
        count = old.count()
        if opts['dry_run']:
            self.stdout.write(f'{count} event(s) would be deleted.')
            return
        old.delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} event(s) older than {opts["days"]} days.'))
