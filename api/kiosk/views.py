from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import InsufficientStockError
from core.services.customers import find_customer_by_national_id
from core.services.inventory import assert_stock_available, lock_products
from core.models import (
    CurrencySnapshot, Customer, InventoryMovement, KioskStation, Payment, Product,
    RecipientProfile, SalesOrder, SalesOrderItem, SystemSettings,
)
from core.services.currency import CurrencyContext
from core.services.receipt_validation import (
    AmountMismatchError,
    DuplicateTransactionError,
    InvalidReceiptImageError,
    ReceiptValidationError,
    assert_receipt_total_unchanged,
    assert_transaction_key_unused,
    kiosk_policy,
    receipt_paid_date,
    validate_receipt,
)
from core.services.vepay import RECIPIENT_ACCOUNT_PATH, RECIPIENT_BANK_PATH, get_receipt_value
from api.serializers.currency import recorded_currency_payload, secondary_amount_payload

from .authentication import KioskTokenAuthentication
from .permissions import IsKioskStation
from .serializers import (
    KioskCheckoutSerializer,
    KioskIdentifyResponseSerializer,
    KioskIdentifySerializer,
    KioskProductSerializer,
    KioskReceiptItemSerializer,
    KioskReceiptSerializer,
    KioskRecipientProfileSerializer,
    KioskRegisterSerializer,
)
from .throttling import (
    KioskCheckoutThrottle,
    KioskIdentifyThrottle,
    KioskPollThrottle,
    KioskScanThrottle,
)


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

        # Shared resolver, not a bare .get(): it also reaches rows written
        # before IDs were normalized (a stored "V-12.345.678" for a shopper
        # typing "V12345678"), and it returns one row rather than raising
        # MultipleObjectsReturned on a legacy pair -- a 500 on the first screen.
        customer = find_customer_by_national_id(
            serializer.validated_data['national_id']
        )
        if customer is None:
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

def _kiosk_products():
    """
    Products a kiosk may show and sell: active, physical, and stock-tracked.

    Konteo Express reads a null current_stock as zero and would refuse to sell
    an untracked product, and a licence or a service has no place on a
    self-checkout shelf. Until the app handles both, the kiosk does not see
    them; its checkout refuses them as it does any unknown SKU.
    """
    return Product.objects.filter(is_active=True, kind=Product.PHYSICAL, track_inventory=True)


def _kiosk_sellable(product):
    return product.is_active and product.kind == Product.PHYSICAL and product.track_inventory


class KioskProductSearchView(KioskAPIMixin, APIView):
    """
    GET /api/v1/kiosk/products/           — return up to 6 active products
    GET /api/v1/kiosk/products/?search=q  — filter by name or SKU (case-insensitive)
    """
    throttle_classes = [KioskScanThrottle]

    def get(self, request):
        q = request.query_params.get('search', '').strip()
        qs = _kiosk_products()
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
            product = _kiosk_products().get(pk=pk)
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
            product = _kiosk_products().get(sku=sku)
        except Product.DoesNotExist:
            return Response(
                {'error': 'Product not found', 'code': 'not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(KioskProductSerializer(product, context={'request': request}).data)


# ── Payment recipient (where the customer sends the money) ───────────────────

class KioskRecipientProfileView(KioskAPIMixin, APIView):
    """
    GET /api/v1/kiosk/recipient-profiles/ — the primary recipient profile for
    each payment method, so the kiosk can show the customer where to pay.

    Returns at most one entry per payment method (mobile_payment /
    bank_transfer), and an empty list when none is configured. Serving this
    instead of a destination hardcoded in the kiosk keeps the screen and the
    allowlist that checkout validates receipts against in step, so a customer
    can't be shown an account their own payment would then be rejected for.
    """
    throttle_classes = [KioskScanThrottle]

    def get(self, request):
        profiles = RecipientProfile.primary_for_customers()
        return Response(
            {'results': KioskRecipientProfileSerializer(profiles, many=True).data}
        )


# ── Atomic checkout ──────────────────────────────────────────────────────────

class KioskCheckoutView(KioskAPIMixin, APIView):
    """
    POST /api/v1/kiosk/checkout/

    Self-checkout: validates the receipt (including the OCR round-trip to
    VEPay), then creates the order, deducts inventory, records payment and
    immediately marks the order DELIVERED — the whole write in a single
    database transaction, with row-level locking on the products it touches.
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
        except InsufficientStockError as exc:
            return Response(
                {
                    'error': 'Insufficient stock',
                    'code': 'insufficient_stock',
                    'insufficient': exc.shortfalls,
                },
                status=status.HTTP_409_CONFLICT,
            )
        except _InvalidProductError as exc:
            return Response(
                {'error': str(exc), 'code': 'invalid_product'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except DuplicateTransactionError as exc:
            return Response(
                {
                    'error': 'This receipt transaction has already been recorded.',
                    'code': 'duplicate_transaction',
                    'details': {'transaction_key': exc.transaction_key},
                },
                status=status.HTTP_409_CONFLICT,
            )
        except AmountMismatchError as exc:
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
        except ReceiptValidationError as exc:
            payload = {
                'error': str(exc),
                'code': exc.code,
            }
            if exc.details:
                payload['details'] = exc.details
            return Response(payload, status=exc.http_status)
        except InvalidReceiptImageError as exc:
            return Response(
                {'error': str(exc), 'code': exc.code},
                status=exc.http_status,
            )

        return Response(result, status=status.HTTP_201_CREATED)

    @staticmethod
    def _execute_checkout(data, station, service_user, customer):
        """
        Validate the receipt first, then write the order in one transaction.

        The VEPay OCR round-trip deliberately runs *before* the transaction
        opens. `order.save()` draws its number from SequenceCounter, which
        takes `SELECT FOR UPDATE` on the row for the `SO-{today}` prefix; the
        `transaction.atomic()` inside next_value degrades to a savepoint under
        an outer block, so that row stays locked until the *outer* transaction
        commits. With the OCR call inside, a single kiosk waiting on VEPay
        (two attempts against a 30s timeout) held the one counter row every
        order created that day has to pass through — serialising all of them
        behind a third-party HTTP call.

        Nothing read before the transaction is trusted once inside it: the
        write block re-resolves the products under row locks, re-checks stock,
        re-checks the total the receipt was validated against, and re-checks
        the transaction key.

        The one thing that *is* carried across is the currency context. The
        receipt is converted to the primary currency at that context's rate,
        and the payment is recorded with that same context — so the rate that
        validated the receipt is provably the rate stored, even if the BCV rate
        is refreshed while the OCR call is in flight.
        """
        items = data['items']
        payment_method = data.get('payment_method') or Payment.CARD
        receipt_data = data.get('receipt') or {}
        settings = SystemSettings.get()
        currency = CurrencyContext.from_settings(settings)

        # ── Validate before taking any lock ──────────────────────────────
        # Same order as the write block below, so which side of the
        # transaction a check runs on never changes the error a client sees.
        products = _resolve_products(items)
        _assert_stock_available(products, items)
        receipt_fields = validate_receipt(
            receipt_data,
            payment_method,
            _order_subtotal(products, items),
            settings=settings,
            currency=currency,
            policy=kiosk_policy(settings),
        )

        # Interned after the receipt passes, so a receipt rejection leaves no
        # row (one rejected inside the write block does; it is reused later),
        # and in autocommit, so its unique-index entry is never held under locks.
        currency_snapshot = CurrencySnapshot.objects.intern(currency)

        now = timezone.now()

        with transaction.atomic():
            # ── Re-resolve products and acquire row-level locks ───────
            products = _resolve_products(items, lock=True)

            # ── Validate stock (inside lock — eliminates TOCTOU) ─────
            _assert_stock_available(products, items)

            subtotal = _order_subtotal(products, items)
            # A unit price that moved while the receipt was in OCR would leave
            # the order total different from the one the receipt was accepted
            # against, so the receipt no longer proves payment for this order.
            assert_receipt_total_unchanged(receipt_fields, subtotal)
            # Two checkouts can clear the pre-transaction duplicate check with
            # the same key concurrently; this is where one of them loses.
            assert_transaction_key_unused(receipt_fields['transaction_key'])

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

            # ── Create line items ────────────────────────────────────
            order_items = []
            for item in items:
                product = products[item['sku']]
                order_items.append(SalesOrderItem(
                    sales_order=order,
                    product=product,
                    quantity=item['quantity'],
                    unit_price=product.unit_price,
                    line_total=product.unit_price * item['quantity'],
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
                for item in items
            ]
            InventoryMovement.objects.bulk_create(movements)

            # ── Record payment ───────────────────────────────────────
            # Strip each candidate before the `or`, not after it. Stripping the
            # result let a whitespace-only receipt reference win the `or` (it is
            # truthy) and then collapse to '', storing a blank reference for a
            # payment method the API requires one for. str() guards the other
            # half: a non-string JSON value, e.g. {"reference": 12345}, used to
            # raise AttributeError inside this atomic block — a 500, not a 400.
            reference_number = (
                str(receipt_data.get('reference') or '').strip()
                or data['payment_reference'].strip()
            )
            verified_at = (
                now
                if receipt_fields['ocr_receipt_data'] and receipt_fields['transaction_key']
                else None
            )

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
                recipient_bank=(
                    get_receipt_value(receipt_fields['ocr_receipt_data'], RECIPIENT_BANK_PATH, '')
                    or receipt_data.get('recipient_bank') or ''
                ).strip(),
                recipient_account=(
                    get_receipt_value(receipt_fields['ocr_receipt_data'], RECIPIENT_ACCOUNT_PATH, '')
                    or receipt_data.get('recipient_account') or ''
                ).strip(),
                ocr_receipt_data=receipt_fields['ocr_receipt_data'],
                verified_at=verified_at,
                currency_snapshot=currency_snapshot,
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
            'currency': recorded_currency_payload(currency_snapshot),
            'amount_secondary': secondary_amount_payload(payment.amount, currency_snapshot),
        }

        return {
            'order_id': order.pk,
            'order_number': order.order_number,
            'payment_number': payment.payment_number,
            'payment_status': payment.status,
            'total_amount': str(order.total_amount),
            'receipt': receipt,
        }


def _resolve_products(items, *, lock=False):
    """
    Map each requested SKU to its active Product, optionally locking rows.

    Locks go through lock_products(), which takes them in primary-key order —
    the order every other stock writer uses. Locking SKU by SKU in cart order
    let two concurrent checkouts holding the same lines in a different order
    deadlock; PostgreSQL then aborted one of them with a 500, after its
    receipt had already been accepted.
    """
    found = {
        product.sku: product
        for product in _kiosk_products().filter(sku__in=[item['sku'] for item in items])
    }
    # Report the first unavailable SKU in cart order, as before.
    for item in items:
        if item['sku'] not in found:
            raise _InvalidProductError(
                f"Product not found or inactive: {item['sku']}"
            )
    if not lock:
        return found

    locked = lock_products(product.pk for product in found.values())
    products = {}
    for item in items:
        product = locked.get(found[item['sku']].pk)
        # Changed between the read above and the lock: no longer for sale here.
        if product is None or not _kiosk_sellable(product):
            raise _InvalidProductError(
                f"Product not found or inactive: {item['sku']}"
            )
        products[item['sku']] = product
    return products


def _assert_stock_available(products, items):
    """
    Defer to the shared stock rule in core.services.inventory.

    The kiosk keys its lines by SKU while the rule works on Product rows, so
    all this does is bridge the two. Keeping the arithmetic in one place means
    a kiosk checkout and a back-office confirmation can never disagree about
    whether a product is in stock.
    """
    assert_stock_available(
        (products[item['sku']], item['quantity']) for item in items
    )


def _order_subtotal(products, items):
    subtotal = Decimal('0.00')
    for item in items:
        subtotal += products[item['sku']].unit_price * item['quantity']
    return subtotal


def _payment_notes(station, receipt_data, payment_status):
    notes = [f'Kiosk payment - {station}']
    if payment_status == Payment.PENDING_REVIEW:
        notes.append('Receipt pending manual review.')
    if receipt_data.get('origin_document'):
        notes.append(f"Origin document: {receipt_data['origin_document']}")
    paid_date = receipt_paid_date(receipt_data)
    if paid_date:
        notes.append(f'Paid date: {paid_date}')
    return '\n'.join(notes)


class _InvalidProductError(Exception):
    pass


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
            'currency': (
                recorded_currency_payload(payment.currency_snapshot) if payment else None
            ),
            'amount_secondary': (
                secondary_amount_payload(payment.amount, payment.currency_snapshot)
                if payment else None
            ),
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
