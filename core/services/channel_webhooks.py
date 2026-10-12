"""
Sending a channel's events to its store, signed.

A webhook is a convenience, not the source of truth: the store re-reads the order when it is told,
and reads the feed (GET /storefront/events/) to catch up on anything it missed. So delivery is
"at least once, probably in order", and what matters is that it is safe:

* **Every delivery is signed, at the moment it is sent.** The signature covers a timestamp and the
  exact body (``HMAC-SHA256(secret, "<timestamp>.<body>")``), and the timestamp is the time of
  *this attempt*, so a retry an hour later carries a fresh one and a receiver can reject anything
  older than five minutes. While a secret is being rotated both the new and the previous one sign.
* **A webhook cannot be pointed somewhere it should not go.** An address is checked every time it
  is used, not only when it was registered: https only (outside development), no credentials in
  the URL, every address the name resolves to must be a public one, the connection that is
  actually made is checked again before anything is sent (a name can resolve differently the
  second time), redirects are not followed, the connection is not routed through a proxy, and
  waiting is bounded. Without this a webhook is a way to make RetailOps fetch http://169.254.169.254/.
* **A dead receiver does not cost RetailOps anything.** A failed delivery is retried after 30
  seconds, 2 minutes, 10 minutes, 1 hour and 6 hours, then abandoned; after ten failures in a row
  the address is switched off until an administrator switches it back on. What the receiver said
  is kept, truncated to 300 characters.
* **Only one worker is needed**, and two are harmless: a delivery is claimed with a lease before it
  is sent, and a worker that dies leaves it to be claimed again.

Registering an address is an administrator's act (the admin, or manage.py register_channel_webhook).
It is not in the storefront API: a leaked channel key must not be able to name a destination.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
from datetime import timedelta
from urllib.parse import urlsplit

import requests
import urllib3
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from requests.adapters import HTTPAdapter

from core.models import ChannelWebhook, WebhookDelivery
from core.services import secret_box

logger = logging.getLogger(__name__)

SIGNATURE_VERSION = 'v1'
# What a receiver should tolerate between its clock and the timestamp it is sent.
TIMESTAMP_TOLERANCE_SECONDS = 300
# After the 1st failed attempt wait 30 s, after the 2nd 2 min, ... after the 5th 6 h; the 6th is the last.
RETRY_DELAYS = (
    timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10), timedelta(hours=1), timedelta(hours=6),
)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
CONNECT_TIMEOUT = 3
READ_TIMEOUT = 8
RESPONSE_EXCERPT = 300
# A delivery being sent is not claimed by another worker for this long.
LEASE = timedelta(seconds=60)
SECRET_PREFIX = 'whsec_'


def max_consecutive_failures():
    return getattr(settings, 'CHANNEL_WEBHOOK_MAX_FAILURES', 10)


def allow_insecure():
    """Development only: lets a webhook use http and point at this machine."""
    return bool(getattr(settings, 'CHANNEL_WEBHOOK_ALLOW_INSECURE', False))


class WebhookError(Exception):
    pass


class UnsafeTarget(WebhookError):
    """The address is one RetailOps will not send to."""


# ── Addresses ─────────────────────────────────────────────────────────────────

# IPv6 ranges that carry an IPv4 address inside (NAT64, 6to4, Teredo). Nobody serves a receiver from
# one, and through a gateway they reach private IPv4 hosts that look public to ``is_global``.
_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(network) for network in ('64:ff9b::/96', '64:ff9b:1::/48', '2002::/16', '2001::/32')
)


def _is_public(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    mapped = getattr(ip, 'ipv4_mapped', None)
    if mapped is not None:
        ip = mapped
    if ip.version == 6 and any(ip in network for network in _TRANSITION_NETWORKS):
        return False
    return ip.is_global and not ip.is_multicast


def check_url_shape(url):
    """What can be said about an address without looking it up. Returns (scheme, host, port)."""
    parts = urlsplit(url or '')
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeTarget('The address has an invalid port.') from exc
    allowed_schemes = {'https', 'http'} if allow_insecure() else {'https'}
    if parts.scheme not in allowed_schemes:
        raise UnsafeTarget('Only https addresses are allowed.')
    if parts.username or parts.password:
        raise UnsafeTarget('The address must not contain credentials.')
    if not parts.hostname:
        raise UnsafeTarget('The address has no host.')
    return parts.scheme, parts.hostname, port or (443 if parts.scheme == 'https' else 80)


def check_target(url):
    """
    Refuse an address RetailOps must not send to: a scheme it does not allow, credentials in the
    URL, or a name that resolves to anything but public addresses. Run before every delivery.
    """
    _scheme, host, port = check_url_shape(url)
    if allow_insecure():
        return  # development: the receiver is on this machine, which is the point
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeTarget('The address does not resolve.') from exc
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_is_public(address) for address in addresses):
        raise UnsafeTarget('The address does not resolve to a public address.')


class _PeerGuard:
    """
    Checks, right after the TCP connection is made and before anything is written to it, that the
    machine at the other end is a public one. The name was checked a moment ago, but a name can
    resolve to something else the second time (DNS rebinding).
    """

    def _new_conn(self):
        sock = super()._new_conn()
        if not allow_insecure():
            try:
                public = _is_public(sock.getpeername()[0])
            except (OSError, IndexError):
                public = False
            if not public:
                sock.close()
                raise UnsafeTarget('The connection is not to a public address.')
        return sock


class _GuardedHTTPConnection(_PeerGuard, urllib3.connection.HTTPConnection):
    pass


class _GuardedHTTPSConnection(_PeerGuard, urllib3.connection.HTTPSConnection):
    pass


class _GuardedHTTPPool(urllib3.HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSPool(urllib3.HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


class _GuardedAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {'http': _GuardedHTTPPool, 'https': _GuardedHTTPSPool}


def new_session():
    session = requests.Session()
    session.trust_env = False  # no proxy from the environment, no netrc
    adapter = _GuardedAdapter(max_retries=0)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


# ── Secrets and signatures ─────────────────────────────────────────────────────

def new_secret():
    return f'{SECRET_PREFIX}{secrets.token_urlsafe(32)}'


def signing_secrets(webhook):
    """The secrets that sign this webhook's deliveries: the current one, and the previous one while it is being retired."""
    found = [secret_box.unseal(webhook.secret)]
    if webhook.previous_secret:
        found.append(secret_box.unseal(webhook.previous_secret))
    return found


def sign(secret, timestamp, body):
    """HMAC-SHA256 over "<timestamp>.<body>", hex. What a receiver recomputes from the raw body."""
    message = f'{int(timestamp)}.'.encode('ascii') + body
    return hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()


def signature_header(secret_list, timestamp, body):
    return ','.join(f'{SIGNATURE_VERSION}={sign(secret, timestamp, body)}' for secret in secret_list)


def payload_for(event):
    """The exact bytes sent for an event. Sorted and compact, so it is the same on every attempt."""
    document = {
        'sequence': event.sequence,
        'type': event.event_type,
        'created_at': event.created_at.isoformat().replace('+00:00', 'Z'),
        'channel': event.channel.slug,
        'data': event.data,
    }
    return json.dumps(document, separators=(',', ':'), sort_keys=True, ensure_ascii=True).encode('utf-8')


# ── Registering and maintaining addresses ──────────────────────────────────────

def register_webhook(channel, url, *, label='', created_by=None):
    """
    Register an address for a channel. Returns ``(webhook, secret)``: the secret is the only time it
    is ever shown, because only an encrypted copy is kept.
    """
    check_url_shape(url)
    secret = new_secret()
    webhook = ChannelWebhook.objects.create(
        channel=channel, url=url, label=label, secret=secret_box.seal(secret), created_by=created_by,
    )
    return webhook, secret


def rotate_secret(webhook):
    """
    Start rotating the signing secret. Both the new and the old one sign until the old one is
    cleared, so the store can switch without missing a delivery. Returns the new secret (shown once).
    """
    secret = new_secret()
    webhook.previous_secret = webhook.secret
    webhook.secret = secret_box.seal(secret)
    webhook.save(update_fields=['secret', 'previous_secret', 'updated_at'])
    return secret


def clear_previous_secret(webhook):
    webhook.previous_secret = ''
    webhook.save(update_fields=['previous_secret', 'updated_at'])


def reactivate(webhook, *, now=None):
    """Switch an address back on after it was disabled, and let what waited go out at once."""
    now = now or timezone.now()
    webhook.is_active = True
    webhook.consecutive_failures = 0
    webhook.disabled_at = None
    webhook.disabled_reason = ''
    webhook.save(update_fields=['is_active', 'consecutive_failures', 'disabled_at', 'disabled_reason', 'updated_at'])
    return webhook.deliveries.filter(state=WebhookDelivery.PENDING).update(next_attempt_at=now)


# ── Sending ────────────────────────────────────────────────────────────────────

def _record(delivery, *, now, ok, status_code, error):
    webhook = delivery.webhook
    delivery.attempts += 1
    delivery.last_attempt_at = now
    delivery.last_status_code = status_code
    fields = ['attempts', 'last_attempt_at', 'last_status_code', 'last_error', 'state', 'next_attempt_at', 'delivered_at']

    if ok:
        delivery.state = WebhookDelivery.DELIVERED
        delivery.delivered_at = now
        delivery.last_error = ''
        if webhook.consecutive_failures:
            webhook.consecutive_failures = 0
            webhook.save(update_fields=['consecutive_failures', 'updated_at'])
    else:
        delivery.last_error = (error or '')[:300]
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.state = WebhookDelivery.FAILED
        else:
            delivery.next_attempt_at = now + RETRY_DELAYS[delivery.attempts - 1]
        webhook.consecutive_failures += 1
        update = ['consecutive_failures', 'updated_at']
        if webhook.is_active and webhook.consecutive_failures >= max_consecutive_failures():
            webhook.is_active = False
            webhook.disabled_at = now
            webhook.disabled_reason = f'{webhook.consecutive_failures} failed deliveries in a row.'
            update += ['is_active', 'disabled_at', 'disabled_reason']
            logger.warning('Webhook %s disabled after %s failed deliveries in a row.', webhook.pk, webhook.consecutive_failures)
        webhook.save(update_fields=update)
    delivery.save(update_fields=fields)


def deliver(delivery, *, now=None, session=None):
    """
    Make one attempt to deliver. Returns True when the receiver answered 2xx.

    The signature and its timestamp are made here, from the time of this attempt, never when the
    event was recorded.
    """
    now = now or timezone.now()
    webhook = delivery.webhook
    event = delivery.event
    status_code = None
    excerpt = ''
    error = ''
    try:
        secret_list = signing_secrets(webhook)
        body = payload_for(event)
        timestamp = int(now.timestamp())
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'RetailOps-Webhooks/1',
            'X-RetailOps-Event': event.event_type,
            'X-RetailOps-Delivery': str(delivery.pk),
            'X-RetailOps-Timestamp': str(timestamp),
            'X-RetailOps-Signature': signature_header(secret_list, timestamp, body),
        }
        check_target(webhook.url)
        response = (session or new_session()).post(
            webhook.url, data=body, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            allow_redirects=False, stream=True,
        )
        try:
            status_code = response.status_code
            excerpt = response.raw.read(RESPONSE_EXCERPT, decode_content=True).decode('utf-8', 'replace')
        finally:
            response.close()
    except UnsafeTarget as exc:
        error = f'Not sent: {exc}'
    except secret_box.SecretBoxError:
        error = 'Not sent: the signing secret could not be read (check ENTITLEMENT_ENCRYPTION_KEYS).'
    except requests.RequestException as exc:
        error = f'{type(exc).__name__}: {exc}'[:300]
    except OSError as exc:
        error = f'{type(exc).__name__}: {exc}'[:300]

    ok = status_code is not None and 200 <= status_code < 300 and not error
    if not ok and not error:
        if 300 <= (status_code or 0) < 400:
            error = f'Redirects are not followed (HTTP {status_code}).'
        else:
            error = f'HTTP {status_code}: {excerpt}'.strip()
    _record(delivery, now=now, ok=ok, status_code=status_code, error=error)
    return ok


def due_deliveries(now):
    return (
        WebhookDelivery.objects
        .filter(state=WebhookDelivery.PENDING, next_attempt_at__lte=now, webhook__is_active=True)
        .order_by('next_attempt_at', 'pk')
    )


def deliver_due(*, now=None, limit=50, session=None):
    """
    Send what is due. Returns ``(delivered, failed)``.

    Each delivery is claimed first (its next attempt is pushed a minute away, atomically), so a
    second worker never sends the same one, and one that dies mid-send is simply picked up again.
    """
    now = now or timezone.now()
    delivered = failed = 0
    for pk, due_at in list(due_deliveries(now).values_list('pk', 'next_attempt_at')[:limit]):
        with transaction.atomic():
            claimed = WebhookDelivery.objects.filter(
                pk=pk, state=WebhookDelivery.PENDING, next_attempt_at=due_at, webhook__is_active=True,
            ).update(next_attempt_at=now + LEASE)
        if not claimed:
            continue
        delivery = WebhookDelivery.objects.select_related('webhook', 'event__channel').get(pk=pk)
        if deliver(delivery, now=now, session=session):
            delivered += 1
        else:
            failed += 1
    return delivered, failed
