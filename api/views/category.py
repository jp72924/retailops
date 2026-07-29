from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import ProductCategory
from api.permissions import IsManagerOrAdmin
from api.serializers.category import ProductCategorySerializer


class ProductCategoryViewSet(ModelViewSet):
    """
    Product category management.

    GET    /api/v1/categories/         — list all categories (any authenticated user)
    POST   /api/v1/categories/         — create category (Manager+)
    GET    /api/v1/categories/<id>/    — retrieve category (any authenticated user)
    PUT    /api/v1/categories/<id>/    — full update (Manager+)
    PATCH  /api/v1/categories/<id>/    — partial update (Manager+)
    DELETE /api/v1/categories/<id>/    — delete (Manager+)

    Deleting a category that still has products returns 409 — see destroy().

    Search:   ?search= on name, description.
    Ordering: ?ordering=name|created_at.
    """
    queryset           = ProductCategory.objects.prefetch_related('subcategories').order_by('name')
    serializer_class   = ProductCategorySerializer
    search_fields      = ['name', 'description']
    ordering_fields    = ['name', 'created_at']
    ordering           = ['name']

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsManagerOrAdmin()]

    def destroy(self, request, *args, **kwargs):
        """
        Guard: return 409 if the category still has products.

        Product.category is on_delete=PROTECT, and custom_exception_handler has
        no ProtectedError branch — so without this the delete surfaced as an
        unhandled 500 despite the docs promising a 409.
        """
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {
                    'error': 'Cannot delete a category that still has products.',
                    'code':  'conflict',
                },
                status=status.HTTP_409_CONFLICT,
            )
