"""Sales channel provisioning utilities.

A raw key is returned to the caller exactly once and never persisted: only its
SHA-256 hash and an 8-character lookup prefix are stored.
"""
import hashlib
import secrets

from django.db import transaction
from django.utils import timezone

from core.models import ChannelKey, Role, SalesChannel, User


def _generate_key() -> str:
    """A URL-safe 48-character token (288 bits of entropy)."""
    return secrets.token_urlsafe(36)


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()


def _new_key(channel: SalesChannel, label: str) -> tuple[ChannelKey, str]:
    raw_key = _generate_key()
    key = ChannelKey.objects.create(
        channel=channel,
        prefix=raw_key[:8],
        key_hash=_hash_key(raw_key),
        label=label,
    )
    return key, raw_key


@transaction.atomic
def create_channel(
    name: str,
    slug: str,
    created_by: User,
    label: str = '',
) -> tuple[SalesChannel, str]:
    """Provision a new sales channel.

    Creates a service User with the Channel role and an unusable password,
    then the channel and its first key. Returns the channel and the raw key,
    which cannot be recovered afterwards. Raises ValidationError, before
    anything is written, for a name or slug the model would not accept: the
    slug becomes part of the service user's email.
    """
    SalesChannel(name=name, slug=slug).clean_fields(exclude=['service_user', 'created_by'])

    role, _ = Role.objects.get_or_create(
        name=Role.CHANNEL,
        defaults={'description': 'Service role for online sales channels'},
    )
    service_user = User.objects.create(
        email=f'channel-{slug}@channel.internal',
        first_name='Channel',
        last_name=name,
        role=role,
        is_active=True,
    )
    service_user.set_unusable_password()
    service_user.save(update_fields=['password'])

    channel = SalesChannel.objects.create(
        name=name, slug=slug, service_user=service_user, created_by=created_by,
    )
    _, raw_key = _new_key(channel, label)
    return channel, raw_key


@transaction.atomic
def issue_key(channel: SalesChannel, label: str = '') -> tuple[ChannelKey, str]:
    """Add a key to a channel without touching its others.

    A channel can hold several active keys, so a storefront can deploy the new
    one and only then have the old one revoked.
    """
    return _new_key(channel, label)


def revoke_key(key: ChannelKey) -> bool:
    """Revoke one key. Returns False if it was already revoked."""
    return bool(
        ChannelKey.objects
        .filter(pk=key.pk, revoked_at__isnull=True)
        .update(revoked_at=timezone.now())
    )


def revoke_all_keys(channel: SalesChannel) -> int:
    """Revoke every active key of a channel. Returns how many."""
    return (
        ChannelKey.objects
        .filter(channel=channel, revoked_at__isnull=True)
        .update(revoked_at=timezone.now())
    )
