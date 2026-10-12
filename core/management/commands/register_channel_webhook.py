"""Register the address a channel's events are sent to, and get its signing secret.

    python manage.py register_channel_webhook --channel trend-editorial \\
        --url https://shop.example/api/retailops/webhook
    python manage.py register_channel_webhook --channel trend-editorial \\
        --url http://127.0.0.1:3001/api/retailops/webhook --dev-vars ../trend-store/apps/editorial/.dev.vars

Registering an address is an operator's act, like provisioning a channel: it is not in the storefront
API, so a leaked channel key cannot name a destination. The signing secret is printed once, or written
as RETAILOPS_WEBHOOK_SECRET to `--dev-vars` (merged into the file, never printed). The store verifies
every delivery with it (see STOREFRONT_INTEGRATION.md).

It is idempotent: an address that is already registered is left alone, unless `--new-secret` asks
for a rotation (both secrets sign until `--drop-previous`) or `--dev-vars` names a file that no longer
holds the secret, which is replaced. An address must be https and resolve to public addresses; with
CHANNEL_WEBHOOK_ALLOW_INSECURE on (the demonstration settings) an http address on this machine is fine.
"""
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.management.commands.import_storefront_catalog import write_dev_vars
from core.models import ChannelWebhook, SalesChannel
from core.services import secret_box
from core.services.channel_webhooks import (
    UnsafeTarget,
    check_url_shape,
    clear_previous_secret,
    register_webhook,
    rotate_secret,
)

SECRET_VARIABLE = 'RETAILOPS_WEBHOOK_SECRET'


def has_variable(path, name):
    path = Path(path)
    if not path.exists():
        return False
    return any(
        re.match(rf'\s*{re.escape(name)}\s*=\s*\S', line) for line in path.read_text(encoding='utf-8').splitlines()
    )


class Command(BaseCommand):
    help = "Register the address a channel's events are sent to, and print (or save) its signing secret."

    def add_arguments(self, parser):
        parser.add_argument('--channel', required=True, help='Slug of the sales channel.')
        parser.add_argument('--url', required=True, help='Where to POST the events.')
        parser.add_argument('--label', default='', help='A note for whoever reads the admin.')
        parser.add_argument('--dev-vars', default=None, help=f'A .dev.vars file to write {SECRET_VARIABLE} to.')
        parser.add_argument('--new-secret', action='store_true', help='Rotate the secret of an address already registered.')
        parser.add_argument('--drop-previous', action='store_true', help='Stop signing with the previous secret.')

    def handle(self, *args, **opts):
        try:
            channel = SalesChannel.objects.get(slug=opts['channel'])
        except SalesChannel.DoesNotExist as exc:
            raise CommandError(f'No sales channel with slug {opts["channel"]!r}.') from exc
        try:
            check_url_shape(opts['url'])
            secret_box.keys()
        except UnsafeTarget as exc:
            raise CommandError(str(exc)) from exc
        except secret_box.NotConfigured as exc:
            raise CommandError('ENTITLEMENT_ENCRYPTION_KEYS is not set, so a signing secret cannot be stored.') from exc

        webhook = ChannelWebhook.objects.filter(channel=channel, url=opts['url']).first()
        secret = None
        if webhook is None:
            webhook, secret = register_webhook(channel, opts['url'], label=opts['label'])
            self.stdout.write(self.style.SUCCESS(f'Registered {webhook.url} for {channel.slug}.'))
        else:
            lost = opts['dev_vars'] and not has_variable(opts['dev_vars'], SECRET_VARIABLE)
            if opts['new_secret'] or lost:
                secret = rotate_secret(webhook)
                if lost:
                    clear_previous_secret(webhook)  # the old secret is gone with the file: nothing to keep signing for
                self.stdout.write(self.style.SUCCESS(f'New signing secret for {webhook.url}.'))
            else:
                self.stdout.write(f'{webhook.url} is already registered for {channel.slug}.')
            if not webhook.is_active:
                self.stdout.write(self.style.WARNING('It is switched off: switch it on in the admin (Channel webhooks).'))
        if opts['drop_previous']:
            clear_previous_secret(webhook)
            self.stdout.write('Dropped the previous secret.')

        if secret is None:
            return
        if opts['dev_vars']:
            write_dev_vars(opts['dev_vars'], {SECRET_VARIABLE: secret})
            self.stdout.write(f'  {SECRET_VARIABLE} written to {opts["dev_vars"]}')
        else:
            self.stdout.write(self.style.WARNING('Signing secret (copy it now, it is not shown again):'))
            self.stdout.write(f'  {secret}')
