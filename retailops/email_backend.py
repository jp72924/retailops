"""
retailops/email_backend.py
--------------------------
Development email backend for RetailOps.

Identical to Django's built-in ConsoleEmailBackend except that it decodes
quoted-printable (QP) body encoding before printing to stdout.

Why this exists
---------------
Python's email library applies QP encoding to UTF-8 text and wraps lines at
76 characters using "=\\n" soft line-breaks (RFC 2045).  Password-reset URLs
are always longer than 76 characters, so the standard ConsoleEmailBackend
prints something like:

    http://localhost:8000/password-reset/confirm/MQ/abc123xyz=
    456def/

When a developer copies that URL literally the token is truncated or broken
and Django's PasswordResetConfirmView shows "invalid link".

This backend calls msg.get_payload(decode=True), which is Python's own QP
decoder, and prints the fully-reassembled body so the URL always appears on
one unbroken line:

    http://localhost:8000/password-reset/confirm/MQ/abc123xyz456def/

Usage
-----
This backend is set as the default in settings.py:

    EMAIL_BACKEND = 'retailops.email_backend.DecodedConsoleEmailBackend'

To switch to real SMTP delivery set DJANGO_EMAIL_BACKEND in the environment:

    DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
"""

from django.core.mail.backends.console import EmailBackend as _ConsoleBackend


class DecodedConsoleEmailBackend(_ConsoleBackend):
    """
    ConsoleEmailBackend that decodes QP body encoding before printing.

    All behaviour is identical to the parent class except that when the
    outgoing message uses Content-Transfer-Encoding: quoted-printable the
    body is decoded to plain text before being written to the stream.
    Multipart messages and non-QP encodings fall back to the default output.
    """

    def write_message(self, message):
        msg = message.message()

        if msg.get('Content-Transfer-Encoding', '').lower() == 'quoted-printable':
            # get_payload(decode=True) returns bytes with QP/base64 decoded.
            raw_bytes = msg.get_payload(decode=True)
            if raw_bytes is not None:
                charset = msg.get_content_charset('utf-8')
                decoded_body = raw_bytes.decode(charset, errors='replace')
                self._write_decoded(msg, decoded_body)
                return

        # Multipart or already-unencoded messages: fall back to default.
        super().write_message(message)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _write_decoded(self, msg, body):
        """Write MIME headers followed by the already-decoded body."""
        lines = []
        for key, value in msg.items():
            # Drop the transfer-encoding header — the body is now plain text.
            if key.lower() == 'content-transfer-encoding':
                continue
            lines.append(f'{key}: {value}')
        self.stream.write('\n'.join(lines))
        self.stream.write('\n\n')
        self.stream.write(body)
        self.stream.write('\n')
