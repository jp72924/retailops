from decimal import Decimal
from io import BytesIO
from time import perf_counter

from asgiref.sync import async_to_sync
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import mixins, status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from PIL import Image, UnidentifiedImageError

from core.models import OcrCallLog, Payment, SalesOrder, SystemSettings
from core.services.receipt_matching import compare_receipt_fields
from core.services.vepay import (
    ORIGIN_BANK_PATH,
    ORIGIN_PHONE_PATH,
    PAYMENT_BANK_APP_PATH,
    RECIPIENT_BANK_PATH,
    TRANSACTION_KEY_PATH,
    VALIDATION_IS_COMPLETE_PATH,
    VALIDATION_MISSING_FIELDS_PATH,
    VEPayClient,
    VEPayError,
    get_receipt_value,
)
from api.filters import PaymentFilter
from api.kiosk.authentication import KioskTokenAuthentication
from api.permissions import IsManagerOrAdmin
from api.serializers.payment import PaymentSerializer, ReceiptVerifySerializer
from api.throttling import OcrVerifyRateThrottle


ALLOWED_RECEIPT_MIME_TYPES = {'image/jpeg', 'image/png', 'image/heic', 'image/heif'}
HEIF_RECEIPT_MIME_TYPES = {'image/heic', 'image/heif'}
MAX_RECEIPT_IMAGE_SIDE = 1600
MONEY_QUANT = Decimal('0.01')


class IsManagerOrAdminOrKiosk(BasePermission):
    """Allow manager/admin users or authenticated kiosk stations."""

    def has_permission(self, request, view):
        if getattr(request, 'kiosk_station', None) is not None:
            return True
        return IsManagerOrAdmin().has_permission(request, view)


class IsNotKioskStation(BasePermission):
    """Keep kiosk API keys scoped to kiosk-specific payment actions."""

    def has_permission(self, request, view):
        return getattr(request, 'kiosk_station', None) is None


class PaymentViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    GenericViewSet,
):
    """
    Payment recording.

    GET  /api/v1/payments/          paginated list (any auth user)
    POST /api/v1/payments/          record a payment (any auth user)
    GET  /api/v1/payments/<id>/     retrieve (any auth user)

    Payments cannot be updated or deleted (immutable financial records).

    Creating a payment against a Confirmed order automatically transitions
    the order to Paid once total_paid >= order.total_amount.

    Filtering:
      ?sales_order=<id>
      ?payment_method=cash|mobile_payment|bank_transfer|card|check|other
      ?method=cash|mobile_payment|bank_transfer|card|check|other
      ?status=confirmed|pending_review
      ?has_receipt=true|false
      ?bank=<partial bank name>
      ?date_from=YYYY-MM-DD
      ?date_to=YYYY-MM-DD

    Ordering:
      ?ordering=created_at (default: -created_at, newest first)
    """
    authentication_classes = [
        TokenAuthentication, SessionAuthentication, KioskTokenAuthentication,
    ]
    serializer_class = PaymentSerializer
    filterset_class  = PaymentFilter
    ordering_fields  = ['created_at', 'amount']
    ordering         = ['-created_at']

    def get_permissions(self):
        if getattr(self, 'action', None) == 'verify_receipt':
            return [IsAuthenticated(), IsManagerOrAdminOrKiosk()]
        if getattr(self, 'action', None) == 'receipt_healthz':
            return [IsAuthenticated(), IsManagerOrAdmin()]
        return [IsAuthenticated(), IsNotKioskStation()]

    def get_throttles(self):
        if getattr(self, 'action', None) == 'verify_receipt':
            return [OcrVerifyRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        return (
            Payment.objects
            .select_related('sales_order', 'sales_order__customer', 'recorded_by')
            .order_by('-created_at')
        )

    @transaction.atomic
    def perform_create(self, serializer):
        """
        Save the payment then check whether the order should auto-transition
        to Paid.  Both writes happen inside a single transaction so the order
        status can never be left inconsistent if the payment save fails.
        """
        payment = serializer.save(recorded_by=self.request.user)
        order   = payment.sales_order

        # Re-read total_paid inside the same transaction (with a lock) so
        # concurrent payment requests don't both trigger the transition.
        if payment.status == Payment.CONFIRMED:
            total_paid = (
                Payment.objects
                .filter(sales_order=order, status=Payment.CONFIRMED)
                .select_for_update()
                .aggregate(total=Sum('amount'))
            )['total'] or Decimal('0.00')

            if total_paid >= order.total_amount:
                order.status  = SalesOrder.PAID
                order.paid_at = timezone.now()
                order.save(update_fields=['status', 'paid_at', 'updated_at'])

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        # Re-read from DB to get the generated payment_number
        instance = Payment.objects.select_related(
            'sales_order', 'sales_order__customer', 'recorded_by'
        ).get(pk=serializer.instance.pk)
        return Response(
            PaymentSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=False,
        methods=['get'],
        url_path='receipts/healthz',
    )
    def receipt_healthz(self, request):
        """
        GET /api/v1/payments/receipts/healthz/

        Server-side VEPay connectivity check for the back-office settings UI.
        """
        settings = SystemSettings.get()

        try:
            result = async_to_sync(VEPayClient().healthz)()
        except VEPayError as exc:
            return Response({
                'ok': False,
                'provider': settings.ocr_provider,
                'base_url': settings.ocr_base_url,
                'code': exc.code,
                'error': exc.message,
                'retryable': exc.is_retryable,
            }, status=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.is_retryable else status.HTTP_422_UNPROCESSABLE_ENTITY
            ))

        return Response({
            'ok': True,
            'provider': settings.ocr_provider,
            'base_url': settings.ocr_base_url,
            'healthz': result,
        })

    @action(
        detail=False,
        methods=['post'],
        url_path='receipts/verify',
        parser_classes=[MultiPartParser, FormParser],
    )
    def verify_receipt(self, request):
        """
        POST /api/v1/payments/receipts/verify/

        Parse a receipt screenshot through VEPay and run server-side checks
        without creating a Payment record.
        """
        started_at = perf_counter()
        bytes_sent = 0
        serializer = ReceiptVerifySerializer(data=request.data)
        if not serializer.is_valid():
            sales_order = None
            order_id = request.data.get('sales_order')
            if order_id:
                try:
                    sales_order = SalesOrder.objects.filter(pk=order_id).first()
                except (TypeError, ValueError):
                    sales_order = None
            self._write_ocr_log(request, sales_order, 'validation_error', started_at, bytes_sent)
            serializer.is_valid(raise_exception=True)
        image = serializer.validated_data['image']
        sales_order = serializer.validated_data.get('sales_order')
        payment_method = serializer.validated_data['payment_method']
        expected_fields = self._expected_fields_from_verify(serializer.validated_data)
        field_match_enforced = bool(expected_fields)
        settings = SystemSettings.get()

        if not settings.ocr_enabled:
            self._write_ocr_log(request, sales_order, 'ocr_disabled', started_at, bytes_sent)
            return self._receipt_response(
                valid=False,
                checks={'ocr_enabled': False},
                error='OCR receipt verification is disabled.',
                code='ocr_disabled',
                http_status=status.HTTP_409_CONFLICT,
            )

        enabled_methods = settings.ocr_enabled_methods or []
        if payment_method not in enabled_methods:
            self._write_ocr_log(request, sales_order, 'ocr_method_disabled', started_at, bytes_sent)
            return self._receipt_response(
                valid=False,
                checks={
                    'ocr_enabled': True,
                    'payment_method_enabled': False,
                },
                error='OCR receipt verification is not enabled for this payment method.',
                code='ocr_method_disabled',
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        image_error = self._validate_receipt_image(image, settings)
        if image_error is not None:
            self._write_ocr_log(
                request,
                sales_order,
                image_error.data.get('code', 'invalid_image'),
                started_at,
                bytes_sent,
            )
            return image_error

        prepared = self._prepare_receipt_image(image.read(), image, settings)
        if isinstance(prepared, Response):
            self._write_ocr_log(
                request,
                sales_order,
                prepared.data.get('code', 'invalid_image'),
                started_at,
                bytes_sent,
            )
            return prepared

        image_bytes, filename, content_type = prepared
        bytes_sent = len(image_bytes)

        try:
            vepay_data = async_to_sync(VEPayClient().parse_receipt)(
                image_bytes,
                filename,
                content_type,
            )
        except VEPayError as exc:
            self._write_ocr_log(request, sales_order, exc.code, started_at, bytes_sent)
            return self._receipt_response(
                valid=False,
                checks={'ocr_provider_reachable': False},
                error=exc.message,
                code=exc.code,
                warnings=[{
                    'code': 'retryable_provider_error',
                    'message': 'The receipt can be entered manually if OCR remains unavailable.',
                }] if exc.is_retryable else [],
                http_status=(
                    status.HTTP_503_SERVICE_UNAVAILABLE
                    if exc.is_retryable else status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
            )

        checks, warnings = self._build_receipt_checks(
            vepay_data,
            sales_order,
            settings,
            expected_fields=expected_fields,
        )
        request_id = self._request_id_from_vepay(vepay_data)

        if checks['duplicate']:
            self._write_ocr_log(
                request, sales_order, 'duplicate_transaction', started_at,
                bytes_sent, request_id,
            )
            return self._receipt_response(
                valid=False,
                vepay=vepay_data,
                checks=checks,
                warnings=warnings,
                error='This receipt transaction has already been recorded.',
                code='duplicate_transaction',
                http_status=status.HTTP_409_CONFLICT,
            )

        if field_match_enforced and checks['mismatches']:
            self._write_ocr_log(
                request, sales_order, 'receipt_field_mismatch', started_at,
                bytes_sent, request_id,
            )
            return self._receipt_response(
                valid=False,
                vepay=vepay_data,
                checks=checks,
                warnings=warnings,
                error='Receipt fields do not match the expected payment details.',
                code='receipt_field_mismatch',
                details={'mismatches': checks['mismatches']},
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if settings.ocr_require_complete and not checks['complete']:
            self._write_ocr_log(
                request, sales_order, 'incomplete_receipt', started_at,
                bytes_sent, request_id,
            )
            return self._receipt_response(
                valid=False,
                vepay=vepay_data,
                checks=checks,
                warnings=warnings,
                error='Receipt OCR result is incomplete.',
                code='incomplete_receipt',
                details={'missing_fields': checks['missing_fields']},
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if settings.ocr_strict_amount and not checks['amount_matches']:
            self._write_ocr_log(
                request, sales_order, 'amount_mismatch', started_at,
                bytes_sent, request_id,
            )
            return self._receipt_response(
                valid=False,
                vepay=vepay_data,
                checks=checks,
                warnings=warnings,
                error='Receipt amount does not match the order outstanding amount.',
                code='amount_mismatch',
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        self._write_ocr_log(
            request, sales_order, 'success', started_at, bytes_sent, request_id,
        )
        return self._receipt_response(
            valid=True,
            vepay=vepay_data,
            checks=checks,
            warnings=warnings,
            http_status=status.HTTP_200_OK,
        )

    def _expected_fields_from_verify(self, attrs):
        fields = {}
        if attrs.get('expected_amount_usd') is not None:
            fields['amount_usd'] = attrs['expected_amount_usd']
        if attrs.get('expected_reference') not in (None, ''):
            fields['reference'] = attrs['expected_reference']
        if attrs.get('expected_paid_on') is not None:
            fields['paid_on'] = attrs['expected_paid_on']
        if attrs.get('expected_origin_bank') not in (None, ''):
            fields['origin_bank'] = attrs['expected_origin_bank']
        return fields

    def _validate_receipt_image(self, image, settings):
        max_bytes = settings.ocr_max_file_mb * 1024 * 1024
        if image.size > max_bytes:
            return self._receipt_response(
                valid=False,
                checks={'file_size_ok': False},
                error=f'Receipt image must be {settings.ocr_max_file_mb} MB or smaller.',
                code='receipt_too_large',
                http_status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        content_type = (getattr(image, 'content_type', '') or '').lower()
        if content_type not in ALLOWED_RECEIPT_MIME_TYPES:
            return self._receipt_response(
                valid=False,
                checks={'mime_type_ok': False},
                error='Receipt image must be a JPEG, PNG, HEIC, or HEIF file.',
                code='unsupported_receipt_type',
                http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        return None

    def _prepare_receipt_image(self, image_bytes, image, settings):
        content_type = (getattr(image, 'content_type', '') or '').lower()
        filename = getattr(image, 'name', 'receipt') or 'receipt'

        try:
            with Image.open(BytesIO(image_bytes)) as img:
                img.load()
                prepared = img.copy()
        except UnidentifiedImageError:
            if content_type in HEIF_RECEIPT_MIME_TYPES:
                return self._receipt_response(
                    valid=False,
                    checks={'heif_supported': False},
                    error='HEIC/HEIF receipt images are not supported by this server. Please upload JPEG or PNG.',
                    code='unsupported_heif',
                    http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                )
            return self._receipt_response(
                valid=False,
                checks={'image_readable': False},
                error='Receipt image could not be read.',
                code='invalid_receipt_image',
                http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )
        except OSError:
            return self._receipt_response(
                valid=False,
                checks={'image_readable': False},
                error='Receipt image could not be processed.',
                code='invalid_receipt_image',
                http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        if max(prepared.size) > MAX_RECEIPT_IMAGE_SIDE:
            resampling = getattr(Image, 'Resampling', Image).LANCZOS
            prepared.thumbnail((MAX_RECEIPT_IMAGE_SIDE, MAX_RECEIPT_IMAGE_SIDE), resampling)

        output_format = 'PNG' if content_type == 'image/png' else 'JPEG'
        output_type = 'image/png' if output_format == 'PNG' else 'image/jpeg'
        output_name = self._receipt_filename(filename, 'png' if output_format == 'PNG' else 'jpg')

        if output_format == 'JPEG' and prepared.mode not in ('RGB', 'L'):
            prepared = prepared.convert('RGB')
        elif output_format == 'PNG' and prepared.mode == 'CMYK':
            prepared = prepared.convert('RGB')

        out = BytesIO()
        save_kwargs = {'format': output_format}
        if output_format == 'JPEG':
            save_kwargs.update({'quality': 88, 'optimize': True})
        prepared.save(out, **save_kwargs)
        return out.getvalue(), output_name, output_type

    def _receipt_filename(self, filename, ext):
        stem = (filename.rsplit('.', 1)[0] if '.' in filename else filename).strip() or 'receipt'
        safe_stem = ''.join(
            ch if ch.isalnum() or ch in {'-', '_'} else '-'
            for ch in stem
        )[:80] or 'receipt'
        return f'{safe_stem}.{ext}'

    def _build_receipt_checks(
        self,
        receipt_data,
        sales_order,
        settings,
        expected_fields=None,
    ):
        expected_fields = dict(expected_fields or {})
        if 'amount_usd' not in expected_fields and sales_order is not None:
            expected_fields['amount_usd'] = sales_order.amount_outstanding

        comparison = compare_receipt_fields(receipt_data, expected_fields, settings)
        transaction_key = (get_receipt_value(receipt_data, TRANSACTION_KEY_PATH, '') or '').strip()
        is_complete = self._as_bool(
            get_receipt_value(receipt_data, VALIDATION_IS_COMPLETE_PATH, False)
        )
        missing_fields = get_receipt_value(receipt_data, VALIDATION_MISSING_FIELDS_PATH, []) or []
        receipt_fields = comparison['receipt_fields']
        expected = comparison['expected_fields']
        amount_matches = comparison['field_matches'].get('amount_usd', True)
        amount_normalized = receipt_fields.get('amount_usd') or None
        expected_amount = expected.get('amount_usd') or None
        duplicate = bool(
            transaction_key
            and Payment.objects.filter(transaction_key=transaction_key).exists()
        )

        warnings = []
        if not transaction_key:
            warnings.append({
                'code': 'missing_transaction_key',
                'message': 'VEPay did not return a transaction key; payment will require manual review.',
            })
        if not is_complete:
            warnings.append({
                'code': 'incomplete_receipt',
                'message': 'VEPay marked the receipt as incomplete.',
            })
        if expected_amount and not amount_normalized:
            warnings.append({
                'code': 'missing_amount',
                'message': 'VEPay did not return a parseable payment amount.',
            })
        elif not amount_matches:
            warnings.append({
                'code': 'amount_mismatch',
                'message': 'Receipt amount does not match the order outstanding amount.',
            })

        bank_app = get_receipt_value(receipt_data, PAYMENT_BANK_APP_PATH, '') or ''
        if not bank_app:
            warnings.append({
                'code': 'missing_bank_app',
                'message': 'VEPay did not identify the bank application.',
            })
        if comparison['mismatches']:
            warnings.append({
                'code': 'receipt_field_mismatch',
                'message': 'One or more receipt fields do not match the expected payment details.',
            })

        checks = {
            'amount_matches': amount_matches,
            'duplicate': duplicate,
            'complete': is_complete,
            'missing_fields': missing_fields,
            'transaction_key': transaction_key,
            'bank_app': bank_app,
            'origin_phone': get_receipt_value(receipt_data, ORIGIN_PHONE_PATH, '') or '',
            'origin_bank': get_receipt_value(receipt_data, ORIGIN_BANK_PATH, '') or '',
            'recipient_bank': get_receipt_value(receipt_data, RECIPIENT_BANK_PATH, '') or '',
            'payment_reference': receipt_fields.get('reference', ''),
            'paid_on': receipt_fields.get('paid_on', ''),
            'amount_normalized_usd': amount_normalized,
            'order_amount_outstanding': (
                str(self._money(sales_order.amount_outstanding))
                if sales_order is not None else None
            ),
            'field_matches': comparison['field_matches'],
            'receipt_fields': receipt_fields,
            'expected_fields': expected,
            'mismatches': comparison['mismatches'],
        }
        return checks, warnings

    def _money(self, value):
        return Decimal(str(value)).quantize(MONEY_QUANT)

    def _as_bool(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'y'}
        return bool(value)

    def _request_id_from_vepay(self, receipt_data):
        for path in (
            ('request_id',),
            ('id',),
            ('metadata', 'request_id'),
            ('meta', 'request_id'),
        ):
            value = get_receipt_value(receipt_data, path)
            if value:
                return str(value)[:128]
        return ''

    def _write_ocr_log(
        self,
        request,
        sales_order,
        status_value,
        started_at,
        bytes_sent,
        request_id='',
    ):
        OcrCallLog.objects.create(
            kiosk_station=getattr(request, 'kiosk_station', None),
            sales_order=sales_order,
            request_id=request_id or '',
            status=str(status_value or 'unknown')[:50],
            latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
            bytes_sent=max(0, int(bytes_sent or 0)),
        )

    def _receipt_response(
        self,
        *,
        valid,
        http_status,
        vepay=None,
        checks=None,
        warnings=None,
        error=None,
        code=None,
        details=None,
    ):
        payload = {
            'valid': valid,
            'vepay': vepay,
            'checks': checks or {},
            'warnings': warnings or [],
        }
        if error:
            payload['error'] = error
        if code:
            payload['code'] = code
        if details:
            payload['details'] = details
        return Response(payload, status=http_status)
