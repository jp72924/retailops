import asyncio
import time

import requests

from core.models import SystemSettings


PAYMENT_BANK_APP_PATH = ('payment', 'bank_app')
PAYMENT_REFERENCE_PATH = ('payment', 'reference')
PAYMENT_AMOUNT_VALUE_PATH = ('payment', 'amount', 'value')
PAYMENT_DATETIME_ISO_PATH = ('payment', 'date_time', 'iso')
ORIGIN_PHONE_PATH = ('origin', 'phone')
ORIGIN_ACCOUNT_PATH = ('origin', 'account')
ORIGIN_BANK_PATH = ('origin', 'bank')
RECIPIENT_PHONE_PATH = ('recipient', 'phone')
RECIPIENT_DOCUMENT_ID_PATH = ('recipient', 'document_id')
RECIPIENT_BANK_PATH = ('recipient', 'bank')
TRANSACTION_KEY_PATH = ('transaction_key',)
VALIDATION_IS_COMPLETE_PATH = ('validation', 'is_complete')
VALIDATION_MISSING_FIELDS_PATH = ('validation', 'missing_fields')
RECEIPT_UPLOAD_FIELD = 'files'

RECEIPT_FIELD_PATHS = {
    'payment_bank_app': PAYMENT_BANK_APP_PATH,
    'payment_reference': PAYMENT_REFERENCE_PATH,
    'payment_amount_value': PAYMENT_AMOUNT_VALUE_PATH,
    'payment_datetime_iso': PAYMENT_DATETIME_ISO_PATH,
    'origin_phone': ORIGIN_PHONE_PATH,
    'origin_account': ORIGIN_ACCOUNT_PATH,
    'origin_bank': ORIGIN_BANK_PATH,
    'recipient_phone': RECIPIENT_PHONE_PATH,
    'recipient_document_id': RECIPIENT_DOCUMENT_ID_PATH,
    'recipient_bank': RECIPIENT_BANK_PATH,
    'transaction_key': TRANSACTION_KEY_PATH,
    'validation_is_complete': VALIDATION_IS_COMPLETE_PATH,
    'validation_missing_fields': VALIDATION_MISSING_FIELDS_PATH,
}


class VEPayError(Exception):
    def __init__(self, code, message, is_retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.is_retryable = is_retryable

    def __str__(self):
        return self.message


def get_receipt_value(receipt_data, path, default=None):
    current = receipt_data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


class VEPayClient:
    def __init__(self, base_url=None, api_key=None, timeout=None):
        settings = SystemSettings.get()
        self.base_url = (base_url if base_url is not None else settings.ocr_base_url).rstrip('/')
        self.api_key = api_key if api_key is not None else settings.ocr_api_key
        self.timeout = timeout if timeout is not None else settings.ocr_timeout_seconds

    async def parse_receipt(self, image_bytes, filename, content_type):
        return await asyncio.to_thread(
            self._parse_receipt_sync,
            image_bytes,
            filename,
            content_type,
        )

    async def healthz(self):
        return await asyncio.to_thread(self._healthz_sync)

    def _parse_receipt_sync(self, image_bytes, filename, content_type):
        url = f'{self.base_url}/v1/receipts/parse'
        files = {
            RECEIPT_UPLOAD_FIELD: (
                filename or 'receipt',
                image_bytes,
                content_type or 'application/octet-stream',
            ),
        }

        response = None
        last_exc = None
        for attempt in range(2):
            try:
                response = requests.post(
                    url,
                    files=files,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except requests.Timeout as exc:
                last_exc = VEPayError(
                    'timeout',
                    'VEPay receipt parsing timed out.',
                    is_retryable=True,
                )
            except requests.RequestException as exc:
                last_exc = VEPayError(
                    'network_error',
                    'Could not reach VEPay receipt parsing service.',
                    is_retryable=True,
                )
            else:
                if response.status_code < 500:
                    break
                last_exc = VEPayError(
                    f'http_{response.status_code}',
                    self._error_message(response),
                    is_retryable=True,
                )

            if attempt == 0:
                time.sleep(1)
                continue
            if last_exc:
                raise last_exc

        if response is None:
            raise VEPayError(
                'network_error',
                'Could not reach VEPay receipt parsing service.',
                is_retryable=True,
            )

        if response.status_code >= 400:
            raise VEPayError(
                f'http_{response.status_code}',
                self._error_message(response),
                is_retryable=response.status_code >= 500,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise VEPayError(
                'invalid_response',
                'VEPay returned a non-JSON response.',
                is_retryable=True,
            ) from exc

        if not isinstance(data, dict):
            raise VEPayError(
                'invalid_response',
                'VEPay returned an unexpected response shape.',
                is_retryable=True,
            )

        return self._adapt_parse_response(data)

    def _adapt_parse_response(self, data):
        """
        Normalize VEPay parse responses to the single-receipt schema RetailOps
        stores and validates.

        Older VEPay builds returned the receipt directly. Newer deployments
        return a batch envelope with a receipts list, even for one upload.
        """
        if 'receipts' not in data:
            return data

        receipts = data.get('receipts')
        if not isinstance(receipts, list):
            raise VEPayError(
                'invalid_response',
                'VEPay returned an invalid receipts envelope.',
                is_retryable=True,
            )

        errors = data.get('errors') or []
        if not receipts:
            if errors:
                raise VEPayError(
                    'receipt_parse_error',
                    self._format_envelope_errors(errors),
                    is_retryable=False,
                )
            raise VEPayError(
                'no_receipt',
                'VEPay did not return any parsed receipt.',
                is_retryable=False,
            )

        if len(receipts) > 1:
            raise VEPayError(
                'multiple_receipts',
                'VEPay returned multiple receipts for a single upload.',
                is_retryable=False,
            )

        receipt = receipts[0]
        if not isinstance(receipt, dict):
            raise VEPayError(
                'invalid_response',
                'VEPay returned an invalid receipt object.',
                is_retryable=True,
            )

        receipt = dict(receipt)
        if data.get('request_id') and not receipt.get('request_id'):
            receipt['request_id'] = data['request_id']
        if errors:
            receipt['envelope_errors'] = errors
        return receipt

    def _healthz_sync(self):
        response = None
        for suffix in ('/health', '/healthz'):
            url = f'{self.base_url}{suffix}'
            try:
                response = requests.get(
                    url,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except requests.Timeout as exc:
                raise VEPayError(
                    'timeout',
                    'VEPay health check timed out.',
                    is_retryable=True,
                ) from exc
            except requests.RequestException as exc:
                raise VEPayError(
                    'network_error',
                    'Could not reach VEPay health check endpoint.',
                    is_retryable=True,
                ) from exc

            if response.status_code != 404 or suffix == '/healthz':
                break

        if response.status_code >= 400:
            raise VEPayError(
                f'http_{response.status_code}',
                self._error_message(response),
                is_retryable=response.status_code >= 500,
            )

        try:
            data = response.json()
        except ValueError:
            data = {'status': response.text[:200] if response.text else 'ok'}

        return {
            'ok': True,
            'status_code': response.status_code,
            'data': data,
        }

    def _headers(self):
        return {'X-API-Key': self.api_key} if self.api_key else {}

    def _error_message(self, response):
        try:
            data = response.json()
        except ValueError:
            data = None

        if isinstance(data, dict):
            for key in ('detail', 'message', 'error'):
                value = data.get(key)
                if value:
                    return str(value)

        if response.text:
            return response.text[:500]

        return f'VEPay request failed with HTTP {response.status_code}.'

    def _format_envelope_errors(self, errors):
        if isinstance(errors, list):
            messages = []
            for error in errors:
                if isinstance(error, dict):
                    value = (
                        error.get('message') or error.get('detail') or
                        error.get('error') or error
                    )
                    messages.append(str(value))
                else:
                    messages.append(str(error))
            return '; '.join(messages) or 'VEPay could not parse the receipt.'
        return str(errors) or 'VEPay could not parse the receipt.'
