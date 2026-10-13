"""
Encrypting a secret the server must read back later.

Licence keys and webhook signing secrets share one rule: they are stored
encrypted (Fernet, keys from ENTITLEMENT_ENCRYPTION_KEYS) and decrypted only at
the moment they are used. The first key encrypts and every key decrypts, so a
key is retired by putting the new one first, running
`manage.py rotate_entitlement_keys`, and only then dropping the old one.

This module knows nothing about what it protects; core.services.entitlements
and core.services.channel_webhooks wrap it in the errors and the vocabulary of
their own domain.
"""
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


class SecretBoxError(Exception):
    pass


class NotConfigured(SecretBoxError):
    """ENTITLEMENT_ENCRYPTION_KEYS is empty: nothing can be sealed or opened."""


class Undecryptable(SecretBoxError):
    """The token was sealed with a key that is no longer listed."""


def keys():
    configured = settings.ENTITLEMENT_ENCRYPTION_KEYS
    if not configured:
        raise NotConfigured('ENTITLEMENT_ENCRYPTION_KEYS is not set.')
    return configured


def seal(value):
    """Encrypt `value` with the primary encryption key."""
    return MultiFernet([Fernet(key) for key in keys()]).encrypt(value.encode('utf-8')).decode('ascii')


def unseal(token):
    """Decrypt a token sealed with any listed key."""
    try:
        return MultiFernet([Fernet(key) for key in keys()]).decrypt(token.encode('ascii')).decode('utf-8')
    except InvalidToken as exc:
        raise Undecryptable('The token was sealed with a key that is no longer listed.') from exc


def reseal(token):
    """
    Re-encrypt `token` with the primary key.

    Returns the new token, or None when it is already under the primary key
    (nothing to do). Raises Undecryptable if no listed key opens it.
    """
    all_keys = keys()
    try:
        Fernet(all_keys[0]).decrypt(token.encode('ascii'))
        return None
    except InvalidToken:
        pass
    try:
        return MultiFernet([Fernet(key) for key in all_keys]).rotate(token.encode('ascii')).decode('ascii')
    except InvalidToken as exc:
        raise Undecryptable('The token was sealed with a key that is no longer listed.') from exc
