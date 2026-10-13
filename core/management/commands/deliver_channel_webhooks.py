"""Send the events that are due to the stores' webhooks.

    python manage.py deliver_channel_webhooks --once
    python manage.py deliver_channel_webhooks --interval 5

`--once` sends what is due now and exits (for cron). `--interval N` keeps running and looks again every
N seconds (for a service). One worker is enough, and two are harmless: a delivery is claimed before it
is sent. A failed delivery is retried after 30 seconds, 2 minutes, 10 minutes, 1 hour and 6 hours, then
abandoned; an address that fails ten times in a row is switched off until an administrator switches it
back on (Django admin -> Channel webhooks).
"""
import time

from django.core.management.base import BaseCommand, CommandError

from core.services.channel_webhooks import deliver_due


class Command(BaseCommand):
    help = "Send the events that are due to the channels' webhooks."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--once', action='store_true', help='Send what is due now and exit.')
        mode.add_argument('--interval', type=float, help='Keep running, looking again every this many seconds.')
        parser.add_argument('--limit', type=int, default=50, help='Most deliveries to send in one pass.')

    def handle(self, *args, **opts):
        if opts['interval'] is not None and opts['interval'] <= 0:
            raise CommandError('--interval must be greater than zero.')

        if opts['once']:
            delivered, failed = deliver_due(limit=opts['limit'])
            self.stdout.write(self.style.SUCCESS(f'Delivered {delivered}; {failed} failed and will be retried.'))
            return

        self.stdout.write(f'Delivering webhooks every {opts["interval"]:g} s. Ctrl+C to stop.')
        try:
            while True:
                delivered, failed = deliver_due(limit=opts['limit'])
                if delivered or failed:
                    self.stdout.write(f'delivered {delivered}, failed {failed}')
                time.sleep(opts['interval'])
        except KeyboardInterrupt:
            self.stdout.write('Stopped.')
