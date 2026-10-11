"""
The catalogue a sales channel reads.

Every view here authenticates with a channel key and nothing else: a staff
token or a kiosk key is not accepted, and a channel key is accepted nowhere
outside this namespace. Responses carry the live `currency` block so a
storefront never needs the staff-only settings endpoint.
"""
from django.utils.cache import patch_cache_control, patch_vary_headers
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from api.kiosk.serializers import KioskRecipientProfileSerializer
from core.models import RecipientProfile
from core.services.listings import category_tree, published_listings

from .authentication import ChannelKeyAuthentication
from .filters import ListingFilter
from .pagination import StorefrontPagination
from .permissions import IsSalesChannel
from .serializers import (
    CategoryTreeResponseSerializer,
    PaymentInstructionsResponseSerializer,
    StorefrontListingDetailSerializer,
    StorefrontListingSerializer,
    live_currency_payload,
)
from .throttling import StorefrontReadThrottle

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
