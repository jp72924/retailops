"""
Delivering what a customer paid for: licence keys, links, instructions.

An entitlement is created when an order becomes paid (never before, so a
cancelled or expired order leaves none) for each line whose product has a
ProductFulfillment. Fixed text is released at once; anything else waits,
`pending`, until a value is supplied for that one entitlement. There is no
pool of keys to draw from.

A licence key is a secret, so it is held to a stricter rule than anything else
here:

* It is stored encrypted (Fernet, keys from ENTITLEMENT_ENCRYPTION_KEYS).
* Everything that lists or displays an entitlement shows it masked, from a
  four-character hint kept in clear, and never decrypts it.
* The one way to read it back is `reveal()`, which stamps `revealed_at`. That
  first time is the evidence if the customer later disputes a refund.

Revealing is a state change, not a read, and is deliberately kept apart from
everything that merely lists.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Entitlement, ProductFulfillment, validate_entitlement_value
from core.services import secret_box

HINT_LENGTH = 4
# Below this a "last four characters" hint would give most of the key away.
MIN_LENGTH_FOR_HINT = 8


class EntitlementError(Exception):
    code = 'entitlement_error'


class EncryptionNotConfigured(EntitlementError):
    code = 'encryption_not_configured'

    def __init__(self):
        super().__init__(
            'ENTITLEMENT_ENCRYPTION_KEYS is not set, so a licence key cannot be stored or read.'
        )


class EntitlementDecryptionError(EntitlementError):
    code = 'decryption_failed'

    def __init__(self):
        super().__init__(
            'The stored key could not be decrypted: the key it was sealed with is no longer '
            'in ENTITLEMENT_ENCRYPTION_KEYS.'
        )


class InvalidEntitlementValue(EntitlementError):
    code = 'invalid_value'


class EntitlementStateError(EntitlementError):
    """The entitlement is not in a state that allows this."""

    def __init__(self, code, entitlement):
        self.code = code
        self.entitlement = entitlement
        super().__init__(f'Entitlement #{entitlement.pk} is {entitlement.get_status_display().lower()}.')


# ── Encryption ───────────────────────────────────────────────────────────────

def seal(value):
    """Encrypt a key with the primary encryption key."""
    try:
        return secret_box.seal(value)
    except secret_box.NotConfigured as exc:
        raise EncryptionNotConfigured() from exc


def unseal(token):
    try:
        return secret_box.unseal(token)
    except secret_box.NotConfigured as exc:
        raise EncryptionNotConfigured() from exc
    except secret_box.Undecryptable as exc:
        raise EntitlementDecryptionError() from exc


def hint_for(value):
    return value[-HINT_LENGTH:] if len(value) >= MIN_LENGTH_FOR_HINT else ''


def rotate_keys():
    """
    Re-encrypt every stored secret with the primary encryption key: the licence
    keys, and the signing secrets of the channels' webhooks.

    Run after putting a new key first in ENTITLEMENT_ENCRYPTION_KEYS and before
    dropping the old one. Returns how many were re-encrypted; secrets already
    under the primary key are left untouched.
    """
    from core.models import ChannelWebhook

    try:
        secret_box.keys()
    except secret_box.NotConfigured as exc:
        raise EncryptionNotConfigured() from exc

    def reseal(token):
        try:
            return secret_box.reseal(token)
        except secret_box.Undecryptable as exc:
            raise EntitlementDecryptionError() from exc

    rotated = 0
    for entitlement in Entitlement.objects.filter(kind=ProductFulfillment.KEY).exclude(value='').iterator():
        new = reseal(entitlement.value)
        if new is not None:
            entitlement.value = new
            entitlement.save(update_fields=['value'])
            rotated += 1
    for webhook in ChannelWebhook.objects.iterator():
        changed = []
        for field in ('secret', 'previous_secret'):
            token = getattr(webhook, field)
            if not token:
                continue
            new = reseal(token)
            if new is not None:
                setattr(webhook, field, new)
                changed.append(field)
        if changed:
            webhook.save(update_fields=changed)
            rotated += len(changed)
    return rotated


# ── Lifecycle ────────────────────────────────────────────────────────────────

def _activation(duration_days, now):
    return {
        'status': Entitlement.ACTIVE,
        'activated_at': now,
        'expires_at': now + timedelta(days=duration_days) if duration_days else None,
    }


def create_entitlements(order, *, now=None):
    """
    Create what a paid order's lines are owed. Idempotent.

    Called from the single place an order becomes paid, inside its
    transaction. A line whose product has no ProductFulfillment delivers
    nothing. Returns the entitlements created.
    """
    now = now or timezone.now()
    already = set(
        Entitlement.objects
        .filter(order_item__sales_order=order)
        .values_list('order_item_id', flat=True)
    )
    created = []
    for item in order.items.select_related('product__fulfillment'):
        fulfillment = getattr(item.product, 'fulfillment', None)
        if fulfillment is None or item.pk in already:
            continue
        copies = item.quantity if fulfillment.per_unit else 1
        seats = 1 if fulfillment.per_unit else item.quantity
        fixed = fulfillment.mode == ProductFulfillment.TEMPLATE
        for _ in range(copies):
            fields = dict(
                order_item=item,
                kind=fulfillment.entitlement_kind,
                seats=seats,
                duration_days=fulfillment.duration_days,
            )
            if fixed:
                fields.update(value=fulfillment.template, **_activation(fulfillment.duration_days, now))
            created.append(Entitlement(**fields))
    return Entitlement.objects.bulk_create(created)


def _publish_for(entitlement):
    """Tell the order's channel (if it has one) what this entitlement's change did."""
    from core.services.channel_events import publish_order_changes
    publish_order_changes(entitlement.order_item.sales_order_id)


def supply_value(entitlement_id, raw_value, *, replace=False, now=None):
    """
    Provide the value of an entitlement and activate it.

    A pending entitlement becomes active, and its term (`expires_at`) starts
    now. An active one is only changed with `replace=True`, as a reissue: the
    term does not restart, and the new value counts as not yet revealed. A
    revoked one is never changed. Raises EntitlementStateError,
    InvalidEntitlementValue and EncryptionNotConfigured; the first two before
    anything is written.
    """
    now = now or timezone.now()
    with transaction.atomic():
        entitlement = Entitlement.objects.select_for_update().get(pk=entitlement_id)
        if entitlement.status == Entitlement.REVOKED:
            raise EntitlementStateError('entitlement_revoked', entitlement)
        if entitlement.status == Entitlement.ACTIVE and not replace:
            raise EntitlementStateError('entitlement_not_pending', entitlement)

        try:
            value = validate_entitlement_value(entitlement.kind, raw_value)
        except ValidationError as exc:
            raise InvalidEntitlementValue(exc.messages[0]) from exc

        if entitlement.kind == ProductFulfillment.KEY:
            entitlement.value, entitlement.hint = seal(value), hint_for(value)
        else:
            entitlement.value, entitlement.hint = value, ''
        entitlement.revealed_at = None
        fields = ['value', 'hint', 'revealed_at']
        if entitlement.status == Entitlement.PENDING:
            for name, content in _activation(entitlement.duration_days, now).items():
                setattr(entitlement, name, content)
            fields += ['status', 'activated_at', 'expires_at']
        entitlement.save(update_fields=fields)
        _publish_for(entitlement)
    return entitlement


def reveal(entitlement_id, *, now=None):
    """
    Return ``(entitlement, value)`` with the value in clear, and stamp the first
    time it was revealed.

    Only an active entitlement can be revealed. Later reveals keep the first
    timestamp: it is the evidence, and a repeat must not overwrite it.
    """
    now = now or timezone.now()
    with transaction.atomic():
        entitlement = Entitlement.objects.select_for_update().get(pk=entitlement_id)
        if entitlement.status != Entitlement.ACTIVE:
            code = 'entitlement_revoked' if entitlement.status == Entitlement.REVOKED else 'entitlement_not_active'
            raise EntitlementStateError(code, entitlement)
        value = unseal(entitlement.value) if entitlement.kind == ProductFulfillment.KEY else entitlement.value
        if entitlement.revealed_at is None:
            entitlement.revealed_at = now
            entitlement.save(update_fields=['revealed_at'])
    return entitlement, value


def revoke(entitlement_id, *, now=None):
    """Revoke one entitlement. Returns False if it already was."""
    now = now or timezone.now()
    with transaction.atomic():
        entitlement = Entitlement.objects.select_for_update().get(pk=entitlement_id)
        if entitlement.status == Entitlement.REVOKED:
            return False
        entitlement.status = Entitlement.REVOKED
        entitlement.revoked_at = now
        entitlement.save(update_fields=['status', 'revoked_at'])
        _publish_for(entitlement)
    return True


def revoke_for_order(order, *, now=None):
    """Revoke everything delivered for an order, as a refund does. Returns how many."""
    return (
        Entitlement.objects
        .filter(order_item__sales_order=order)
        .exclude(status=Entitlement.REVOKED)
        .update(status=Entitlement.REVOKED, revoked_at=now or timezone.now())
    )


def delivery_complete(order):
    """
    True when the whole order has been delivered digitally: every line has
    entitlements and every one is active.

    A line with no entitlement (a physical product, or a product nothing is
    configured for) means the order is not finished by delivery alone. Reads
    `items__entitlements` as prefetched, so a list of orders costs no extra query.
    """
    items = list(order.items.all())
    if not items:
        return False
    for item in items:
        entitlements = list(item.entitlements.all())
        if not entitlements or any(e.status != Entitlement.ACTIVE for e in entitlements):
            return False
    return True
