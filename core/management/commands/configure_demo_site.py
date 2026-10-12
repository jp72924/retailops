"""Configure a scratch RetailOps so a storefront demo works end to end.

    python manage.py configure_demo_site --ocr-url http://127.0.0.1:9100 --rate 36.50

It sets what a demonstration of the storefront flow needs and nothing else: United States dollars as
the primary currency and bolivares as the secondary one at a fixed rate; receipt OCR on for both
receipt methods, pointing at the VEPay stand-in; an account to pay into for each method, with
recipient validation on so a receipt paid to someone else is refused; and `kiosk_trust_client_evidence`
off, as it should be anywhere a person could forge a request. It is idempotent.

It refuses to run against the default development database (db.sqlite3): these are demonstration
settings, not what a real shop wants.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.management.scratch import SCRATCH_DB_ADVICE, refuses_default_database
from core.models import Payment, RecipientProfile, SystemSettings, User
from core.services.currency import RATE_SOURCE_MANUAL

DEMO_RECIPIENTS = [
    {
        'payment_method': Payment.MOBILE_PAYMENT, 'label': 'Demo mobile payment',
        'bank': 'Banco de Venezuela', 'phone': '0414-1234567', 'account_number': '',
        'document_id': 'J-12345678-9',
    },
    {
        'payment_method': Payment.BANK_TRANSFER, 'label': 'Demo bank transfer',
        'bank': 'Banesco', 'phone': '', 'account_number': '01340000000000001234',
        'document_id': 'J-12345678-9',
    },
]


class Command(BaseCommand):
    help = 'Configure a scratch database for the storefront demonstration (currency, OCR, recipients).'

    def add_arguments(self, parser):
        parser.add_argument('--ocr-url', default='http://127.0.0.1:9100', help='Base URL of the VEPay stand-in.')
        parser.add_argument('--rate', default='36.50', help='Bolivares per dollar.')
        parser.add_argument('--by', default=None, help='Email of the staff user the recipient profiles are attributed to.')
        parser.add_argument('--allow-default-db', action='store_true',
                            help='Run against the default development database (db.sqlite3) anyway.')

    def handle(self, *args, **opts):
        if refuses_default_database() and not opts['allow_default_db']:
            raise CommandError(SCRATCH_DB_ADVICE)
        try:
            rate = Decimal(opts['rate'])
        except InvalidOperation as exc:
            raise CommandError(f'{opts["rate"]!r} is not a rate.') from exc

        actor = self._actor(opts['by'])
        try:
            with transaction.atomic():
                for spec in DEMO_RECIPIENTS:
                    RecipientProfile.objects.update_or_create(
                        payment_method=spec['payment_method'], is_primary=True,
                        defaults={**spec, 'is_active': True, 'created_by': actor},
                    )

                settings = SystemSettings.get()
                settings.currency_code, settings.currency_symbol, settings.decimal_places = 'USD', '$', 2
                settings.secondary_currency_enabled = True
                settings.secondary_currency_code, settings.secondary_currency_symbol = 'VES', 'Bs.'
                settings.secondary_decimal_places = 2
                settings.secondary_exchange_rate = rate
                settings.secondary_rate_auto_update_enabled = False
                settings.secondary_rate_source = RATE_SOURCE_MANUAL
                settings.ocr_enabled = True
                settings.ocr_base_url = opts['ocr_url']
                settings.ocr_api_key = 'demo-key'
                settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER]
                settings.receipt_image_required_for_receipt_methods = True
                settings.recipient_validation_enabled = True
                settings.kiosk_trust_client_evidence = False
                settings.full_clean()
                settings.save()
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc

        self.stdout.write(self.style.SUCCESS('Demo site configured.'))
        self.stdout.write(f'  currency: USD primary, VES secondary at {rate}')
        self.stdout.write(f'  OCR: on for mobile payment and bank transfer, at {opts["ocr_url"]}')
        self.stdout.write('  recipients: one primary account per method; recipient validation on')
        self.stdout.write('  kiosk_trust_client_evidence: off')

    def _actor(self, email):
        if email:
            actor = User.objects.filter(email=email).first()
            if actor is None:
                raise CommandError(f'No user with email {email}.')
            return actor
        actor = User.objects.filter(is_staff=True, is_active=True).order_by('pk').first()
        if actor is None:
            raise CommandError('No staff user found; run `manage.py init --demo` first, or pass --by <email>.')
        return actor
