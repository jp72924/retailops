from rest_framework.permissions import IsAuthenticated
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

    Note: deleting a category that has products will raise a DB-level error
    because Product.category has on_delete=PROTECT. DRF's exception handler
    will convert this to a 409 response via the IntegrityError path.

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
