"""Re-encrypt every stored licence key with the primary encryption key.

    python manage.py rotate_entitlement_keys

To retire an encryption key: put the new one FIRST in ENTITLEMENT_ENCRYPTION_KEYS
(keeping the old one after it), deploy, run this, and only then drop the old
key. A key already under the primary key is left alone, so running it twice is
harmless.
"""
from django.core.management.base import BaseCommand, CommandError

from core.services.entitlements import EntitlementError, rotate_keys


class Command(BaseCommand):
    help = 'Re-encrypt stored licence keys with the primary ENTITLEMENT_ENCRYPTION_KEYS key.'

    def handle(self, *args, **opts):
        try:
            rotated = rotate_keys()
        except EntitlementError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Re-encrypted {rotated} key(s) with the primary key.'))
