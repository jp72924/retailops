from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import Customer
from api.serializers.customer import CustomerSerializer


class CustomerViewSet(ModelViewSet):
    """
    Full CRUD for customers. Any authenticated user may read or write.

    GET    /api/v1/customers/         — paginated list; ?search= on name/email
    POST   /api/v1/customers/         — create customer
    GET    /api/v1/customers/<id>/    — retrieve customer
    PUT    /api/v1/customers/<id>/    — full update
    PATCH  /api/v1/customers/<id>/    — partial update
    DELETE /api/v1/customers/<id>/    — delete (409 if the customer has orders)

    Search: ?search=<term> matches first_name, last_name, email.
    Ordering: ?ordering=first_name|last_name|email|created_at (prefix - for desc).
    """
    queryset = Customer.objects.select_related('user').order_by('last_name', 'first_name')
    serializer_class   = CustomerSerializer
    permission_classes = [IsAuthenticated]

    search_fields  = ['first_name', 'last_name', 'email']
    ordering_fields = ['first_name', 'last_name', 'email', 'created_at']
    ordering        = ['last_name', 'first_name']

    def destroy(self, request, *args, **kwargs):
        """
        Guard: return 409 if the customer has associated orders.

        Without this check the DB's on_delete=PROTECT would raise an
        IntegrityError, which surfaces as an unhandled 500.
        """
        customer = self.get_object()
        if customer.orders.exists():
            return Response(
                {
                    'error': 'Cannot delete a customer who has existing orders.',
                    'code':  'conflict',
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)
