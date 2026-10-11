"""Provision a sales channel, or add a key to one, and print the key once.

    python manage.py provision_channel --name "Trend Store" --slug trend-store
    python manage.py provision_channel --slug trend-store --new-key

A channel can hold several active keys, so a storefront can switch to a new
one before the old one is revoked from the admin.
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from api.storefront.provisioning import create_channel, issue_key
from core.models import SalesChannel, User


class Command(BaseCommand):
    help = 'Provision a sales channel (service user + first key), or issue another key for one.'

    def add_arguments(self, parser):
        parser.add_argument('--slug', required=True, help='Short identifier, e.g. trend-store')
        parser.add_argument('--name', default=None, help='Display name; required for a new channel')
        parser.add_argument('--label', default='', help='Label for the key being issued')
        parser.add_argument('--new-key', action='store_true',
                            help='Issue another key for an existing channel instead of creating one')
        parser.add_argument('--by', default=None, help='Email of the admin provisioning the channel')

    def handle(self, *args, **opts):
        slug = opts['slug']
        existing = SalesChannel.objects.filter(slug=slug).first()

        if opts['new_key']:
            if existing is None:
                raise CommandError(f'No channel with slug {slug!r}.')
            key, raw_key = issue_key(existing, label=opts['label'])
            self.stdout.write(self.style.SUCCESS(f'Issued a key for channel: {existing}'))
            self._show_key(raw_key, key.prefix)
            return

        if existing is not None:
            raise CommandError(
                f'Channel {slug!r} already exists; pass --new-key to issue another key.'
            )
        if not opts['name']:
            raise CommandError('--name is required to create a channel.')
        if SalesChannel.objects.filter(name=opts['name']).exists():
            raise CommandError(f'A channel named {opts["name"]!r} already exists.')

        if opts['by']:
            try:
                created_by = User.objects.get(email=opts['by'])
            except User.DoesNotExist as exc:
                raise CommandError(f'No user with email {opts["by"]}') from exc
        else:
            created_by = User.objects.filter(is_staff=True, is_active=True).order_by('pk').first()
            if created_by is None:
                raise CommandError('No staff user found to attribute provisioning to; pass --by <email>.')

        try:
            channel, raw_key = create_channel(
                name=opts['name'], slug=slug, created_by=created_by, label=opts['label'],
            )
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc
        self.stdout.write(self.style.SUCCESS(f'Provisioned channel: {channel}'))
        self._show_key(raw_key, raw_key[:8])
        self.stdout.write(f'Service user: {channel.service_user.email}')

    def _show_key(self, raw_key, prefix):
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('CHANNEL KEY (shown only once — copy now):'))
        self.stdout.write('')
        self.stdout.write(f'    {raw_key}')
        self.stdout.write('')
        self.stdout.write(f'Prefix:       {prefix}')
