import base64
import binascii
import uuid
from decimal import Decimal, InvalidOperation

from asgiref.sync import async_to_sync
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (
    Customer, InventoryMovement, KioskStation, Payment, Product,
    SalesOrder, SalesOrderItem, SystemSettings,
)
from core.services.receipt_matching import REQUIRED_FIELD_KEYS, compare_receipt_fields
from core.services.vepay import TRANSACTION_KEY_PATH, VEPayClient, VEPayError, get_receipt_value

from .authentication import KioskTokenAuthentication
from .permissions import IsKioskStation
from .serializers import (
    KioskCheckoutSerializer,
    KioskIdentifyResponseSerializer,
    KioskIdentifySerializer,
    KioskProductSerializer,
    KioskReceiptItemSerializer,
    KioskReceiptSerializer,
    KioskRegisterSerializer,
)
from .throttling import (
    KioskCheckoutThrottle,
    KioskIdentifyThrottle,
    KioskPollThrottle,
    KioskScanThrottle,
)


RECEIPT_PAYMENT_METHODS = {Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER}
ALLOWED_RECEIPT_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/heic', 'image/heif'}
MONEY_QUANT = Decimal('0.01')


# ── Mixin ────────────────────────────────────────────────────────────────────

class KioskAPIMixin:
    """Shared defaults for all kiosk-facing endpoints."""
    authentication_classes = [KioskTokenAuthentication]
    permission_classes = [IsKioskStation]


# ── Customer identification ──────────────────────────────────────────────────

class KioskIdentifyView(KioskAPIMixin, APIView):
    """POST /api/v1/kiosk/identify/ — look up customer by national ID."""
    throttle_classes = [KioskIdentifyThrottle]

    def post(self, request):
        serializer = KioskIdentifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            customer = Customer.objects.get(
                national_id=serializer.validated_data['national_id'],
            )
        except Customer.DoesNotExist:
            return Response(
                {'error': 'Customer not found', 'code': 'not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(KioskIdentifyResponseSerializer({
            'customer_id': customer.pk,
            'first_name': customer.first_name,
            'last_name': customer.last_name,
        }).data)


class KioskRegisterView(KioskAPIMixin, APIView):
    """POST /api/v1/kiosk/register/ — register a new kiosk customer."""
    throttle_classes = [KioskIdentifyThrottle]

    def post(self, request):
        serializer = KioskRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = serializer.save()
        return Response(
            KioskIdentifyResponseSerializer({
                'customer_id': customer.pk,
                'first_name': customer.first_name,
                'last_name': customer.last_name,
            }).data,
            status=status.HTTP_201_CREATED,
        )


# ── Product search ────────────────────────────────────────────────────────────

class KioskProductSearchView(KioskAPIMixin, APIView):
    """
    GET /api/v1/kiosk/products/           — return up to 6 active products
    GET /api/v1/kiosk/products/?search=q  — filter by name or SKU (case-insensitive)
    """
    throttle_classes = [KioskScanThrottle]

    def get(self, request):
        q = request.query_params.get('search', '').strip()
        qs = Product.objects.filter(is_active=True)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))
        products = qs.order_by('name')[:6]
        serializer = KioskProductSerializer(
            products,
            many=True,
            context={'request': request},
        )
        return Response({'results': serializer.data})


class KioskProductDetailView(KioskAPIMixin, APIView):
    """GET /api/v1/kiosk/products/<id>/ — fetch one active product by PK."""
    throttle_classes = [KioskScanThrottle]

    def get(self, request, pk):
        try:
            product = Product.objects.get(pk=pk, is_active=True)
        except Product.DoesNotExist:
            return Response(
                {'error': 'Product not found', 'code': 'not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(KioskProductSerializer(product, context={'request': request}).data)


# ── Product lookup (barcode scan) ────────────────────────────────────────────

class KioskProductLookupView(KioskAPIMixin, APIView):
    """GET /api/v1/kiosk/product/<sku>/ — barcode scan → product details."""
    throttle_classes = [KioskScanThrottle]

    def get(self, request, sku):
        try:
            product = Product.objects.get(sku=sku, is_active=True)
        except Product.DoesNotExist:
            return Response(
                {'error': 'Product not found', 'code': 'not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(KioskProductSerializer(product, context={'request': request}).data)


# ── Atomic checkout ──────────────────────────────────────────────────────────

class KioskCheckoutView(KioskAPIMixin, APIView):
    """
    POST /api/v1/kiosk/checkout/

    Atomic self-checkout: validates stock with row-level locking, creates the
    order, deducts inventory, records payment, and immediately marks the order
    DELIVERED — all in a single database transaction.
    """
    throttle_classes = [KioskCheckoutThrottle]

    def post(self, request):
        serializer = KioskCheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        station = request.kiosk_station
        service_user = request.user

        # ── Validate customer ────────────────────────────────────────────
        try:
            customer = Customer.objects.get(pk=data['customer_id'])
        except Customer.DoesNotExist:
            return Response(
                {'error': 'Customer not found', 'code': 'not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── Atomic checkout ──────────────────────────────────────────────
        try:
            result = self._execute_checkout(
                data, station, service_user, customer,
            )
        except _InsufficientStockError as exc:
            return Response(
                {
                    'error': 'Insufficient stock',
                    'code': 'insufficient_stock',
                    'insufficient': exc.details,
                },
                status=status.HTTP_409_CONFLICT,
            )
        except _InvalidProductError as exc:
            return Response(
                {'error': str(exc), 'code': 'invalid_product'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except _DuplicateTransactionError as exc:
            return Response(
                {
                    'error': 'This receipt transaction has already been recorded.',
                    'code': 'duplicate_transaction',
                    'details': {'transaction_key': exc.transaction_key},
                },
                status=status.HTTP_409_CONFLICT,
            )
        except _AmountMismatchError as exc:
            return Response(
                {
                    'error': 'Receipt amount does not match the order total.',
                    'code': 'amount_mismatch',
                    'details': {
                        'receipt_amount': str(exc.receipt_amount),
                        'order_total': str(exc.order_total),
                    },
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except _ReceiptValidationError as exc:
            payload = {
                'error': str(exc),
                'code': exc.code,
            }
            if exc.details:
                payload['details'] = exc.details
            return Response(payload, status=exc.http_status)
        except _InvalidReceiptImageError as exc:
            return Response(
                {'error': str(exc), 'code': exc.code},
                status=exc.http_status,
            )

        return Response(result, status=status.HTTP_201_CREATED)

    @staticmethod
    def _execute_checkout(data, station, service_user, customer):
        """Run the full checkout inside a single atomic transaction."""
        now = timezone.now()

        with transaction.atomic():
            # ── Resolve products and acquire row-level locks ──────────
            products = {}
            for item in data['items']:
                try:
                    product = (
                        Product.objects
                        .select_for_update()
                        .get(sku=item['sku'], is_active=True)
                    )
                except Product.DoesNotExist:
                    raise _InvalidProductError(
                        f"Product not found or inactive: {item['sku']}"
                    )
                products[item['sku']] = product

            # ── Validate stock (inside lock — eliminates TOCTOU) ─────
            insufficient = []
            stock_levels = {}
            for item in data['items']:
                product = products[item['sku']]
                current = (
                    product.inventory_movements
                    .aggregate(total=Sum('quantity'))['total'] or 0
                )
                stock_levels[item['sku']] = current
                if current < item['quantity']:
                    insufficient.append({
                        'sku': item['sku'],
                        'requested': item['quantity'],
                        'available': current,
                    })
            if insufficient:
                raise _InsufficientStockError(insufficient)

            # ── Create SalesOrder ────────────────────────────────────
            order = SalesOrder(
                customer=customer,
                status=SalesOrder.CONFIRMED,
                created_by=service_user,
                confirmed_by=service_user,
                confirmed_at=now,
                notes=f'Kiosk checkout — {station}',
            )
            order.save()  # triggers order_number generation

            # ── Create line items and compute totals ─────────────────
            subtotal = Decimal('0.00')
            order_items = []
            for item in data['items']:
                product = products[item['sku']]
                line_total = product.unit_price * item['quantity']
                subtotal += line_total
                order_items.append(SalesOrderItem(
                    sales_order=order,
                    product=product,
                    quantity=item['quantity'],
                    unit_price=product.unit_price,
                    line_total=line_total,
                ))
            SalesOrderItem.objects.bulk_create(order_items)

            order.subtotal = subtotal
            order.total_amount = subtotal  # tax/discount = 0 for kiosk
            order.save(update_fields=['subtotal', 'total_amount', 'updated_at'])

            # ── Deduct stock ─────────────────────────────────────────
            movements = [
                InventoryMovement(
                    product=products[item['sku']],
                    movement_type=InventoryMovement.SALE,
                    quantity=-item['quantity'],
                    reference_type=InventoryMovement.SALES_ORDER,
                    reference_id=order.pk,
                    notes=f'Kiosk sale — {order.order_number}',
                    created_by=service_user,
                )
                for item in data['items']
            ]
            InventoryMovement.objects.bulk_create(movements)

            # ── Record payment ───────────────────────────────────────
            payment_method = data.get('payment_method') or Payment.CARD
            receipt_data = data.get('receipt') or {}
            receipt_fields = _payment_receipt_fields(
                receipt_data,
                order,
                payment_method,
                now,
            )
            reference_number = (
                receipt_data.get('reference') or data['payment_reference']
            ).strip()

            payment = Payment(
                sales_order=order,
                amount=order.total_amount,
                payment_method=payment_method,
                status=receipt_fields['status'],
                reference_number=reference_number,
                recorded_by=service_user,
                notes=_payment_notes(station, receipt_data, receipt_fields['status']),
                transaction_key=receipt_fields['transaction_key'],
                origin_phone=(receipt_data.get('origin_phone') or '').strip(),
                origin_bank=(receipt_data.get('origin_bank') or '').strip(),
                recipient_bank=(receipt_data.get('recipient_bank') or '').strip(),
                recipient_account=(receipt_data.get('recipient_account') or '').strip(),
                ocr_receipt_data=receipt_fields['ocr_receipt_data'],
                verified_at=receipt_fields['verified_at'],
            )
            if receipt_fields['image'] is not None:
                name, content = receipt_fields['image']
                payment.receipt_image.save(name, content, save=False)
            payment.save()  # triggers payment_number generation

            # Preserve the existing kiosk behavior: checkout completes
            # immediately, while manual receipt methods remain reviewable.
            order.status = SalesOrder.DELIVERED
            if payment.status == Payment.CONFIRMED:
                order.paid_at = now
            order.save(update_fields=['status', 'paid_at', 'updated_at'])

        # ── Build receipt ────────────────────────────────────────────
        receipt_items = [
            {
                'product_name': oi.product.name,
                'sku': oi.product.sku,
                'quantity': oi.quantity,
                'unit_price': oi.unit_price,
                'line_total': oi.line_total,
            }
            for oi in order.items.select_related('product').all()
        ]

        receipt = {
            'order_number': order.order_number,
            'customer_name': customer.get_full_name(),
            'items': receipt_items,
            'subtotal': order.subtotal,
            'tax_amount': order.tax_amount,
            'total_amount': order.total_amount,
            'payment_method': payment.get_payment_method_display(),
            'payment_number': payment.payment_number,
            'payment_reference': payment.reference_number,
            'station_number': station.station_number,
            'store_identifier': station.store_identifier,
            'created_at': order.created_at,
        }

        return {
            'order_id': order.pk,
            'order_number': order.order_number,
            'payment_number': payment.payment_number,
            'payment_status': payment.status,
            'total_amount': str(order.total_amount),
            'receipt': receipt,
        }


def _payment_receipt_fields(receipt_data, order, payment_method, now):
    """Validate receipt metadata and map it onto Payment fields."""
    if not isinstance(receipt_data, dict):
        receipt_data = {}

    settings = SystemSettings.get()
    is_receipt_method = payment_method in RECEIPT_PAYMENT_METHODS
    image_present = _receipt_image_present(receipt_data)
    if (
        is_receipt_method and
        settings.receipt_image_required_for_receipt_methods and
        not image_present
    ):
        raise _InvalidReceiptImageError(
            'Receipt image is required for this payment method.',
            'receipt_image_required',
            status.HTTP_400_BAD_REQUEST,
        )

    image_data = (
        _decode_receipt_image_data(receipt_data, order.order_number, settings)
        if image_present else None
    )
    receipt_match_required = (
        is_receipt_method and
        (settings.receipt_image_required_for_receipt_methods or image_data is not None)
    )
    vepay_data = None
    transaction_key = (receipt_data.get('transaction_key') or '').strip()
    if receipt_match_required:
        if image_data is None:
            raise _InvalidReceiptImageError(
                'Receipt image is required for this payment method.',
                'receipt_image_required',
                status.HTTP_400_BAD_REQUEST,
            )
        if not settings.ocr_enabled:
            raise _ReceiptValidationError(
                'Receipt OCR validation is disabled. Ask an associate for assistance.',
                'ocr_disabled',
                status.HTTP_409_CONFLICT,
            )
        enabled_methods = settings.ocr_enabled_methods or []
        if payment_method not in enabled_methods:
            raise _ReceiptValidationError(
                'OCR receipt verification is not enabled for this payment method.',
                'ocr_method_disabled',
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        try:
            vepay_data = async_to_sync(VEPayClient().parse_receipt)(
                image_data['raw'],
                image_data['name'],
                image_data['content_type'],
            )
        except VEPayError as exc:
            raise _ReceiptValidationError(
                exc.message,
                exc.code,
                (
                    status.HTTP_503_SERVICE_UNAVAILABLE
                    if exc.is_retryable else status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                details={'retryable': exc.is_retryable},
            )

        comparison = compare_receipt_fields(
            vepay_data,
            _expected_receipt_fields(receipt_data, order),
            settings,
            REQUIRED_FIELD_KEYS,
        )
        if not comparison['matches']:
            raise _ReceiptValidationError(
                'Receipt fields do not match the submitted payment details.',
                'receipt_field_mismatch',
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                details={
                    'field_matches': comparison['field_matches'],
                    'receipt_fields': comparison['receipt_fields'],
                    'expected_fields': comparison['expected_fields'],
                    'mismatches': comparison['mismatches'],
                },
            )

        transaction_key = (
            get_receipt_value(vepay_data, TRANSACTION_KEY_PATH, '') or transaction_key
        ).strip()

    if transaction_key and Payment.objects.filter(transaction_key=transaction_key).exists():
        raise _DuplicateTransactionError(transaction_key)

    receipt_amount = receipt_data.get('amount_usd')
    if receipt_amount not in (None, ''):
        try:
            receipt_total = Decimal(str(receipt_amount)).quantize(MONEY_QUANT)
        except (InvalidOperation, ValueError, TypeError):
            raise _AmountMismatchError(receipt_amount, order.total_amount)
        order_total = Decimal(str(order.total_amount)).quantize(MONEY_QUANT)
        if receipt_total != order_total:
            raise _AmountMismatchError(receipt_total, order_total)

    ocr_receipt_data = vepay_data or receipt_data.get('ocr_receipt_data') or None
    verified_at = now if ocr_receipt_data and transaction_key else None
    status_value = Payment.CONFIRMED
    if is_receipt_method and not transaction_key:
        status_value = Payment.PENDING_REVIEW

    return {
        'status': status_value,
        'transaction_key': transaction_key,
        'ocr_receipt_data': ocr_receipt_data,
        'verified_at': verified_at,
        'image': _receipt_content_file(image_data),
    }


def _expected_receipt_fields(receipt_data, order):
    return {
        'amount_usd': order.total_amount,
        'reference': receipt_data.get('reference'),
        'paid_on': receipt_data.get('paid_on') or receipt_data.get('paid_at'),
        'origin_bank': receipt_data.get('origin_bank'),
    }


def _receipt_image_present(receipt_data):
    data_url = (receipt_data.get('receipt_image_base64') or '').strip()
    if not data_url:
        return False
    if data_url.startswith('data:') and ',' in data_url:
        return bool(data_url.split(',', 1)[1].strip())
    return True


def _decode_receipt_image_data(receipt_data, order_number, settings=None):
    data_url = receipt_data.get('receipt_image_base64') or ''
    if not data_url:
        return None

    content_type = (receipt_data.get('receipt_image_content_type') or '').lower()
    payload = data_url
    if data_url.startswith('data:') and ',' in data_url:
        header, payload = data_url.split(',', 1)
        content_type = header[5:].split(';', 1)[0].lower()
    payload = payload.strip()

    if content_type not in ALLOWED_RECEIPT_IMAGE_TYPES:
        raise _InvalidReceiptImageError(
            'Receipt image must be a JPEG, PNG, HEIC, or HEIF file.',
            'unsupported_receipt_type',
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise _InvalidReceiptImageError(
            'Receipt image could not be decoded.',
            'invalid_receipt_image',
            status.HTTP_400_BAD_REQUEST,
        )
    if not raw:
        raise _InvalidReceiptImageError(
            'Receipt image could not be decoded.',
            'invalid_receipt_image',
            status.HTTP_400_BAD_REQUEST,
        )

    settings = settings or SystemSettings.get()
    max_bytes = settings.ocr_max_file_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise _InvalidReceiptImageError(
            f'Receipt image must be {settings.ocr_max_file_mb} MB or smaller.',
            'receipt_too_large',
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    ext = {
        'image/jpeg': 'jpg',
        'image/png': 'png',
        'image/heic': 'heic',
        'image/heif': 'heif',
    }.get(content_type, 'jpg')
    name = f'kiosk-{order_number}-{uuid.uuid4().hex}.{ext}'
    return {
        'name': name,
        'raw': raw,
        'content_type': content_type,
    }


def _receipt_content_file(image_data):
    if image_data is None:
        return None
    return image_data['name'], ContentFile(image_data['raw'])


def _payment_notes(station, receipt_data, payment_status):
    notes = [f'Kiosk payment - {station}']
    if payment_status == Payment.PENDING_REVIEW:
        notes.append('Receipt pending manual review.')
    if receipt_data.get('origin_document'):
        notes.append(f"Origin document: {receipt_data['origin_document']}")
    paid_date = _receipt_paid_date(receipt_data)
    if paid_date:
        notes.append(f'Paid date: {paid_date}')
    return '\n'.join(notes)


def _receipt_paid_date(receipt_data):
    raw = (receipt_data.get('paid_on') or receipt_data.get('paid_at') or '').strip()
    if not raw:
        return ''

    value = parse_date(raw)
    if value:
        return value.isoformat()

    value = parse_datetime(raw)
    if value:
        return value.date().isoformat()

    if len(raw) >= 10:
        value = parse_date(raw[:10])
        if value:
            return value.isoformat()

    return ''


class _InsufficientStockError(Exception):
    def __init__(self, details):
        self.details = details
        super().__init__('Insufficient stock')


class _InvalidProductError(Exception):
    pass


class _DuplicateTransactionError(Exception):
    def __init__(self, transaction_key):
        self.transaction_key = transaction_key
        super().__init__('Duplicate transaction')


class _AmountMismatchError(Exception):
    def __init__(self, receipt_amount, order_total):
        self.receipt_amount = receipt_amount
        self.order_total = order_total
        super().__init__('Amount mismatch')


class _InvalidReceiptImageError(Exception):
    def __init__(self, message, code, http_status):
        self.code = code
        self.http_status = http_status
        super().__init__(message)


class _ReceiptValidationError(Exception):
    def __init__(self, message, code, http_status, details=None):
        self.code = code
        self.http_status = http_status
        self.details = details or {}
        super().__init__(message)


# ── Receipt retrieval ────────────────────────────────────────────────────────

class KioskReceiptView(KioskAPIMixin, APIView):
    """GET /api/v1/kiosk/receipt/<order_id>/ — retrieve receipt for own order."""
    throttle_classes = [KioskPollThrottle]

    def get(self, request, order_id):
        try:
            order = (
                SalesOrder.objects
                .select_related('customer')
                .prefetch_related('items__product', 'payments')
                .get(pk=order_id, created_by=request.user)
            )
        except SalesOrder.DoesNotExist:
            return Response(
                {'error': 'Order not found', 'code': 'not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        payment = order.payments.first()
        station = request.kiosk_station

        receipt = KioskReceiptSerializer({
            'order_number': order.order_number,
            'customer_name': order.customer.get_full_name(),
            'items': [
                {
                    'product_name': item.product.name,
                    'sku': item.product.sku,
                    'quantity': item.quantity,
                    'unit_price': item.unit_price,
                    'line_total': item.line_total,
                }
                for item in order.items.select_related('product').all()
            ],
            'subtotal': order.subtotal,
            'tax_amount': order.tax_amount,
            'total_amount': order.total_amount,
            'payment_method': payment.get_payment_method_display() if payment else '',
            'payment_number': payment.payment_number if payment else '',
            'payment_reference': payment.reference_number if payment else '',
            'station_number': station.station_number,
            'store_identifier': station.store_identifier,
            'created_at': order.created_at,
        }).data

        return Response(receipt)


# ── Heartbeat ────────────────────────────────────────────────────────────────

class KioskHeartbeatView(KioskAPIMixin, APIView):
    """POST /api/v1/kiosk/heartbeat/ — station health check."""

    def post(self, request):
        station = request.kiosk_station
        return Response({
            'station': str(station),
            'is_active': station.is_active,
        })
