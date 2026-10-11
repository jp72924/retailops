"""
What a sales channel reads and does.

Every view here authenticates with a channel key and nothing else: a staff
token or a kiosk key is not accepted, and a channel key is accepted nowhere
outside this namespace. Catalogue responses carry the live `currency` block so
a storefront never needs the staff-only settings endpoint.

A channel only ever reaches its own orders: another channel's order number is a
404, indistinguishable from one that does not exist.
"""
import logging

from django.utils.cache import patch_cache_control, patch_vary_headers
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from api.kiosk.serializers import KioskRecipientProfileSerializer
from api.pagination import CappedPageNumberPagination
from core.exceptions import InsufficientStockError
from core.models import Entitlement, RecipientProfile
from core.services.channel_orders import (
    ClientReferenceConflict,
    InvalidOrderItems,
    channel_orders,
    create_channel_order,
    customer_status,
)
from core.services.channel_payments import PaymentUnderReview, register_channel_payment
from core.services.customer_links import CustomerEmailConflict, CustomerProfile
from core.services.entitlements import (
    EncryptionNotConfigured,
    EntitlementDecryptionError,
    EntitlementStateError,
    InvalidEntitlementValue,
    delivery_complete,
    reveal,
    supply_value,
)
from core.services.listings import category_tree, published_listings
from core.services.payments import OrderNotPayableError
from core.services.receipt_validation import (
    AmountMismatchError,
    DuplicateTransactionError,
    InvalidReceiptImageError,
    ReceiptValidationError,
)

from .authentication import ChannelKeyAuthentication
from .filters import ListingFilter
from .order_serializers import (
    StorefrontEntitlementSerializer,
    StorefrontEntitlementValueSerializer,
    StorefrontOrderCreatedSerializer,
    StorefrontOrderCreateSerializer,
    StorefrontOrderSerializer,
    StorefrontPaymentCreateSerializer,
)
from .pagination import StorefrontPagination
from .permissions import IsSalesChannel
from .serializers import (
    CategoryTreeResponseSerializer,
    PaymentInstructionsResponseSerializer,
    StorefrontListingDetailSerializer,
    StorefrontListingSerializer,
    live_currency_payload,
)
from .throttling import (
    StorefrontEntitlementThrottle,
    StorefrontOrderThrottle,
    StorefrontPaymentThrottle,
    StorefrontReadThrottle,
)

logger = logging.getLogger(__name__)

TAGS = ['storefront']


class StorefrontAPIMixin:
    """Shared defaults for every channel-facing endpoint."""
    authentication_classes = [ChannelKeyAuthentication]
    permission_classes = [IsSalesChannel]
    throttle_classes = [StorefrontReadThrottle]

    # How long the storefront's own cache may reuse a successful response.
    # None means do not store it. The responses are authenticated, so a shared
    # cache will not keep them regardless; this is a hint for the storefront.
    # Every response also varies on the credential, so a cache that does keep
    # one can never hand it to a caller that presented a different key, or none.
    cache_max_age = 60

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        patch_vary_headers(response, ('Authorization',))
        if response.status_code == 200:
            if self.cache_max_age:
                patch_cache_control(response, max_age=self.cache_max_age)
            else:
                patch_cache_control(response, no_store=True)
        return response


@extend_schema_view(get=extend_schema(
    tags=TAGS,
    summary='List published listings',
    description=(
        'Published listings with at least one variant on sale, in `sort` order. '
        '`category` matches the category and everything beneath it. '
        '`availability` is a label, never a stock count.'
    ),
    parameters=[
        OpenApiParameter('category', int, description='Category id; includes subcategories.'),
        OpenApiParameter('display_type', str, description='Exact match, ignoring case.'),
        OpenApiParameter('search', str, description='Matches title, summary, slug and variant product names.'),
        OpenApiParameter(
            'ordering', str,
            description='`sort` (default), `title`, `created_at` or `from_price`; prefix `-` to reverse.',
        ),
    ],
))
class ListingListView(StorefrontAPIMixin, ListAPIView):
    serializer_class = StorefrontListingSerializer
    pagination_class = StorefrontPagination
    filterset_class = ListingFilter
    search_fields = ['title', 'summary', 'slug', 'variants__product__name']
    ordering_fields = ['sort', 'title', 'created_at', 'from_price']
    ordering = ['sort', 'pk']

    def get_queryset(self):
        return published_listings()


@extend_schema_view(get=extend_schema(
    tags=TAGS,
    summary='Retrieve a listing',
    description='One published listing with its variants. 404 if it is unpublished or has none on sale.',
))
class ListingDetailView(StorefrontAPIMixin, RetrieveAPIView):
    serializer_class = StorefrontListingDetailSerializer
    lookup_field = 'slug'

    def get_queryset(self):
        return published_listings()


@extend_schema_view(get=extend_schema(
    tags=TAGS,
    summary='Category tree',
    description=(
        'Categories that lead to at least one published listing, nested. '
        'Empty branches are left out.'
    ),
    responses=CategoryTreeResponseSerializer,
))
class CategoryTreeView(StorefrontAPIMixin, APIView):
    def get(self, request):
        return Response({'results': category_tree(), 'currency': live_currency_payload()})


@extend_schema_view(get=extend_schema(
    tags=TAGS,
    summary='Where a customer pays',
    description=(
        'The primary active recipient profile for each payment method: at most one '
        'per method, and an empty list when none is configured. Not cached, so a '
        'change of account takes effect at once.'
    ),
    responses=PaymentInstructionsResponseSerializer,
))
class PaymentInstructionsView(StorefrontAPIMixin, APIView):
    cache_max_age = None

    def get(self, request):
        profiles = RecipientProfile.primary_for_customers()
        return Response({
            'results': KioskRecipientProfileSerializer(profiles, many=True).data,
            'currency': live_currency_payload(),
        })


# ── Orders and payments ──────────────────────────────────────────────────────

def _error(message, code, http_status, **extra):
    return Response({'error': message, 'code': code, **extra}, status=http_status)


def _own_order(request, order_number):
    """The channel's order with this number, or 404. Another channel's is a 404 too."""
    channel_order = (
        channel_orders()
        .filter(channel=request.channel, sales_order__order_number=order_number)
        .first()
    )
    if channel_order is None:
        raise NotFound()
    return channel_order


@extend_schema_view(post=extend_schema(
    tags=TAGS,
    summary='Create an order',
    description=(
        'Creates the order, reserves its stock and freezes the exchange rate, so the '
        'amount in the secondary currency in `quote` is what the customer pays. '
        '`client_reference` is the idempotency key: repeating it returns the same order '
        'with `200` instead of making a second one, and reusing it for a different cart is '
        '`409 client_reference_conflict`. Only products in a published listing can be ordered. '
        'The response carries the payment instructions to show the customer.'
    ),
    request=StorefrontOrderCreateSerializer,
    responses={201: StorefrontOrderCreatedSerializer, 200: StorefrontOrderCreatedSerializer},
))
class OrderCreateView(StorefrontAPIMixin, APIView):
    throttle_classes = [StorefrontOrderThrottle]
    cache_max_age = None

    def post(self, request):
        serializer = StorefrontOrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        customer = data['customer']

        try:
            channel_order, created = create_channel_order(
                request.channel,
                client_reference=data['client_reference'],
                profile=CustomerProfile(
                    external_ref=customer['external_ref'],
                    email=customer['email'],
                    first_name=customer['first_name'],
                    last_name=customer['last_name'],
                    email_verified=customer['email_verified'],
                    phone=customer['phone'],
                ),
                items=[(line['sku'], line['quantity']) for line in data['items']],
                fulfillment=data['fulfillment'],
                contact=data['contact'],
            )
        except InvalidOrderItems as exc:
            return _error(str(exc), exc.code, status.HTTP_400_BAD_REQUEST)
        except CustomerEmailConflict as exc:
            return _error(str(exc), exc.code, status.HTTP_409_CONFLICT)
        except ClientReferenceConflict as exc:
            return _error(str(exc), exc.code, status.HTTP_409_CONFLICT)
        except InsufficientStockError as exc:
            # Which products fell short, never how many are left.
            return _error(
                'Insufficient stock', 'insufficient_stock', status.HTTP_409_CONFLICT,
                insufficient=[
                    {'sku': shortfall['sku'], 'requested': shortfall['requested']}
                    for shortfall in exc.shortfalls
                ],
            )

        return Response(
            StorefrontOrderCreatedSerializer(channel_order).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema_view(get=extend_schema(
    tags=TAGS,
    summary='Retrieve an order',
    description='One of the channel\'s own orders. `404` for any other, including another channel\'s.',
    responses=StorefrontOrderSerializer,
))
class OrderDetailView(StorefrontAPIMixin, APIView):
    cache_max_age = None

    def get(self, request, order_number):
        return Response(StorefrontOrderSerializer(_own_order(request, order_number)).data)


@extend_schema_view(post=extend_schema(
    tags=TAGS,
    summary='Register a payment',
    description=(
        'Sends the receipt for an order. It is read through OCR and compared with the quote the '
        'customer was given, so a change of the exchange rate since the order was created does not '
        'matter. A receipt that matches is `verified` and the order becomes paid; when OCR cannot '
        'decide it is `needs_review` and a person settles it. Returns the order. '
        'Only a confirmed order takes a payment, and not while another is under review.'
    ),
    request=StorefrontPaymentCreateSerializer,
    responses={201: StorefrontOrderSerializer},
))
class OrderPaymentView(StorefrontAPIMixin, APIView):
    throttle_classes = [StorefrontPaymentThrottle]
    cache_max_age = None

    def post(self, request, order_number):
        channel_order = _own_order(request, order_number)
        serializer = StorefrontPaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            register_channel_payment(
                channel_order,
                payment_method=data['payment_method'],
                receipt=dict(data['receipt']),
            )
        except OrderNotPayableError as exc:
            return _error(
                'This order can no longer take a payment.', exc.code, status.HTTP_409_CONFLICT,
                customer_status=customer_status(
                    exc.order, exc.order.payments.all(), delivery_complete(exc.order),
                ),
            )
        except PaymentUnderReview as exc:
            return _error(str(exc), exc.code, status.HTTP_409_CONFLICT)
        except DuplicateTransactionError as exc:
            return _error(
                'This receipt transaction has already been recorded.',
                'duplicate_transaction', status.HTTP_409_CONFLICT,
                details={'transaction_key': exc.transaction_key},
            )
        except AmountMismatchError as exc:
            return _error(
                'Receipt amount does not match the amount owed.',
                'amount_mismatch', status.HTTP_422_UNPROCESSABLE_ENTITY,
                details={
                    'receipt_amount': str(exc.receipt_amount),
                    'order_total': str(exc.order_total),
                },
            )
        except ReceiptValidationError as exc:
            extra = {'details': exc.details} if exc.details else {}
            return _error(str(exc), exc.code, exc.http_status, **extra)
        except InvalidReceiptImageError as exc:
            return _error(str(exc), exc.code, exc.http_status)

        fresh = channel_orders().get(pk=channel_order.pk)
        return Response(StorefrontOrderSerializer(fresh).data, status=status.HTTP_201_CREATED)


@extend_schema_view(get=extend_schema(
    tags=TAGS,
    summary="A customer's orders",
    description=(
        'The channel\'s orders for one of its customers, newest first. A customer the channel '
        'has not ordered for is an empty list, not an error.'
    ),
))
class CustomerOrdersView(StorefrontAPIMixin, ListAPIView):
    serializer_class = StorefrontOrderSerializer
    pagination_class = CappedPageNumberPagination
    filter_backends = []
    cache_max_age = None

    def get_queryset(self):
        return (
            channel_orders()
            .filter(
                channel=self.request.channel,
                customer_link__external_ref=self.kwargs['external_ref'],
            )
            .order_by('-created_at', '-pk')
        )


# ── Delivered entitlements ───────────────────────────────────────────────────

def _own_entitlement(request, pk):
    """The channel's own entitlement, or 404. Another channel's, or a non-channel order's, is a 404 too."""
    entitlement = (
        Entitlement.objects
        .select_related('order_item__product')
        .filter(pk=pk, order_item__sales_order__channel_order__channel=request.channel)
        .first()
    )
    if entitlement is None:
        raise NotFound()
    return entitlement


@extend_schema_view(
    get=extend_schema(
        tags=TAGS,
        summary='Retrieve an entitlement',
        description='What was delivered for an order line. A licence key is masked; see `reveal`.',
        responses=StorefrontEntitlementSerializer,
    ),
    put=extend_schema(
        tags=TAGS,
        summary='Supply an entitlement',
        description=(
            'Provides the value of a `pending` entitlement and activates it: its term starts now. '
            'This is how a store that issues its own licence keys hands them to RetailOps. '
            'Only a pending entitlement can be supplied; an active or revoked one is `409`.'
        ),
        request=StorefrontEntitlementValueSerializer,
        responses=StorefrontEntitlementSerializer,
    ),
)
class EntitlementDetailView(StorefrontAPIMixin, APIView):
    throttle_classes = [StorefrontEntitlementThrottle]
    cache_max_age = None

    def get(self, request, pk):
        return Response(StorefrontEntitlementSerializer(_own_entitlement(request, pk)).data)

    def put(self, request, pk):
        entitlement = _own_entitlement(request, pk)
        serializer = StorefrontEntitlementValueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            supply_value(entitlement.pk, serializer.validated_data['value'])
        except EntitlementStateError as exc:
            return _error(str(exc), exc.code, status.HTTP_409_CONFLICT, entitlement_status=exc.entitlement.status)
        except InvalidEntitlementValue as exc:
            return _error(str(exc), exc.code, status.HTTP_400_BAD_REQUEST)
        except EncryptionNotConfigured as exc:
            logger.error('Cannot store an entitlement key: %s', exc)
            return _error(
                'Licence keys cannot be stored right now.', exc.code, status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(StorefrontEntitlementSerializer(_own_entitlement(request, pk)).data)


@extend_schema_view(post=extend_schema(
    tags=TAGS,
    summary='Reveal an entitlement',
    description=(
        'Returns the whole licence key, link or instructions. This is the one place a key is ever '
        'read back, and it is a POST because it changes state: the first reveal is recorded as '
        '`revealed_at`, which is the evidence if the customer later disputes a refund. Only an '
        '`active` entitlement can be revealed. Never cached.'
    ),
    request=None,
    responses={200: {
        'type': 'object',
        'properties': {
            'id': {'type': 'integer'},
            'kind': {'type': 'string', 'enum': ['key', 'link', 'instructions']},
            'value': {'type': 'string'},
            'revealed_at': {'type': 'string', 'format': 'date-time'},
        },
    }},
))
class EntitlementRevealView(StorefrontAPIMixin, APIView):
    throttle_classes = [StorefrontEntitlementThrottle]
    cache_max_age = None

    def post(self, request, pk):
        entitlement = _own_entitlement(request, pk)
        try:
            entitlement, value = reveal(entitlement.pk)
        except EntitlementStateError as exc:
            return _error(str(exc), exc.code, status.HTTP_409_CONFLICT, entitlement_status=exc.entitlement.status)
        except (EncryptionNotConfigured, EntitlementDecryptionError) as exc:
            logger.error('Cannot reveal entitlement %s: %s', entitlement.pk, exc)
            return _error('This key cannot be read right now.', exc.code, status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response({
            'id': entitlement.pk,
            'kind': entitlement.kind,
            'value': value,
            'revealed_at': entitlement.revealed_at,
        })
