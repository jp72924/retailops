from django.db.models import IntegerField, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from core.models import InventoryMovement, Product
from api.filters import ProductFilter
from api.permissions import IsManagerOrAdmin
from api.serializers.inventory import InventoryMovementSerializer
from api.serializers.product import ProductSerializer


def _annotated_products():
    """
    Base queryset for products with the _stock annotation baked in.

    Using Coalesce(Sum(...), Value(0)) means:
      - Products with movements → their net quantity sum.
      - Products with no movements at all → 0 (Coalesce handles the NULL).

    This single annotation replaces the three per-object SQL queries that
    Product.current_stock, is_low_stock, and is_out_of_stock would otherwise
    fire, eliminating the N+1 problem on list endpoints entirely.

    output_field=IntegerField() is required because the annotated column type
    must be declared explicitly when Coalesce's fallback is a plain Value(0);
    without it Django raises an ambiguous-annotation error on some backends.
    """
    return (
        Product.objects
        .select_related('category', 'category__parent_category')
        .annotate(
            _stock=Coalesce(
                Sum('inventory_movements__quantity'),
                Value(0, output_field=IntegerField()),
            )
        )
        .order_by('name')
    )


class ProductViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    GenericViewSet,
):
    """
    Product catalogue management.

    GET    /api/v1/products/                     list (any auth user)
    POST   /api/v1/products/                     create (Manager+)
    GET    /api/v1/products/<id>/                retrieve (any auth user)
    PUT    /api/v1/products/<id>/                full update (Manager+)
    PATCH  /api/v1/products/<id>/                partial update (Manager+)
    DELETE /api/v1/products/<id>/                delete (Manager+)
    GET    /api/v1/products/<id>/movements/      paginated movement history (any auth)

    Filtering  (via django-filter):
      ?category=<id>       filter by category FK
      ?is_active=true|false
      ?unit_of_measure=piece|kg|liter|meter|box|pack
      ?stock=out|low|ok|all   annotation-backed; no extra queries

    Search (DRF SearchFilter):
      ?search=<term>       matches sku, name, description

    Ordering (DRF OrderingFilter):
      ?ordering=name|sku|unit_price|created_at  (prefix - for descending)
    """
    serializer_class   = ProductSerializer
    parser_classes     = [JSONParser, FormParser, MultiPartParser]
    filterset_class    = ProductFilter
    search_fields      = ['sku', 'name', 'description']
    ordering_fields    = ['name', 'sku', 'unit_price', 'created_at']
    ordering           = ['name']

    def get_queryset(self):
        return _annotated_products()

    def get_permissions(self):
        if self.action in ('list', 'retrieve', 'movements'):
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsManagerOrAdmin()]

    # ── movements action ─────────────────────────────────────────────────────

    @action(detail=True, methods=['get'])
    def movements(self, request, pk=None):
        """
        GET /api/v1/products/<id>/movements/

        Paginated movement history for a single product, newest first.
        Mirrors the JSON returned by core.views.product_movements but adds
        pagination and uses the standard DRF response envelope.

        Any authenticated user may access this endpoint.
        """
        product = self.get_object()
        qs = (
            InventoryMovement.objects
            .filter(product=product)
            .select_related('created_by', 'product')
            .order_by('-created_at')
        )

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = InventoryMovementSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = InventoryMovementSerializer(qs, many=True)
        return Response(serializer.data)
