"""Kiosk station provisioning utilities.

The raw API key is returned to the caller exactly once and never persisted
in plaintext — only its SHA-256 hash and an 8-char lookup prefix are stored.
"""
import hashlib
import secrets

from django.db import transaction

from core.models import KioskStation, Role, User


def _generate_api_key() -> str:
    """Return a URL-safe 48-char random token (288 bits of entropy)."""
    return secrets.token_urlsafe(36)


def _hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()


@transaction.atomic
def create_kiosk_station(
    store_identifier: str,
    station_number: int,
    created_by: User,
    label: str = '',
) -> tuple[KioskStation, str]:
    """Provision a new kiosk station.

    Creates a service User with an unusable password and the Kiosk role,
    generates a fresh API key, stores its hash + prefix, and returns both
    the station record and the raw key.

    The raw key must be delivered to the kiosk operator immediately — it
    cannot be recovered from the database afterwards.
    """
    kiosk_role, _ = Role.objects.get_or_create(
        name=Role.KIOSK,
        defaults={'description': 'Auto-created role for kiosk service accounts'},
    )

    service_email = f'kiosk-{store_identifier.lower()}-{station_number}@station.internal'
    service_user = User.objects.create(
        email=service_email,
        first_name='Kiosk',
        last_name=f'{store_identifier} #{station_number}',
        role=kiosk_role,
        is_active=True,
    )
    service_user.set_unusable_password()
    service_user.save(update_fields=['password'])

    raw_key = _generate_api_key()

    station_kwargs = dict(
        store_identifier=store_identifier,
        station_number=station_number,
        label=label,
        api_key_prefix=raw_key[:8],
        api_key_hash=_hash_api_key(raw_key),
        service_user=service_user,
        created_by=created_by,
    )

    station = KioskStation.objects.create(**station_kwargs)
    return station, raw_key


@transaction.atomic
def rotate_api_key(station: KioskStation) -> str:
    """Generate a new key for an existing station, invalidating the old one."""
    raw_key = _generate_api_key()
    station.api_key_prefix = raw_key[:8]
    station.api_key_hash = _hash_api_key(raw_key)
    station.save(update_fields=['api_key_prefix', 'api_key_hash', 'updated_at'])
    return raw_key
