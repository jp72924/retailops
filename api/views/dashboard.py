from decimal import Decimal

from django.db.models import F, IntegerField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Product, SalesOrder


_RecentOrderSerializer = inline_serializer(
    name='RecentOrder',
    fields={
        'id':           serializers.IntegerField(),
        'order_number': serializers.CharField(),
        'customer':     serializers.CharField(),
        'total_amount': serializers.CharField(),
        'status':       serializers.CharField(),
        'created_at':   serializers.CharField(),
    },
)

_DashboardResponseSerializer = inline_serializer(
    name='DashboardResponse',
    fields={
        'orders_this_month':      serializers.IntegerField(),
        'revenue_this_month':     serializers.CharField(),
        'pending_payments_count': serializers.IntegerField(),
        'low_stock_count':        serializers.IntegerField(),
        'recent_orders':          serializers.ListField(child=_RecentOrderSerializer),
    },
)


@extend_schema(
    responses={200: _DashboardResponseSerializer},
    description='Summary statistics: orders and revenue this month, pending payments, low stock, and 5 recent orders.',
    tags=['dashboard'],
)
class DashboardView(APIView):
    """
    GET /api/v1/dashboard/

    Summary statistics for the operations dashboard.  Any authenticated user
    may access this endpoint.

    Response shape:
    {
        "orders_this_month":      42,
        "revenue_this_month":     "18450.00",
        "pending_payments_count": 7,
        "low_stock_count":        3,
        "recent_orders": [
            {
                "id":           12,
                "order_number": "SO-20260409-0012",
                "customer":     "Jane Doe",
                "total_amount": "349.00",
                "status":       "confirmed",
                "created_at":   "2026-04-09T14:32:00Z"
            },
            ...
        ]
    }

    Implementation notes
    --------------------
    * revenue_this_month counts orders that reached Paid/Shipped/Delivered and
      whose paid_at falls within the current calendar month — mirrors the HTML
      dashboard view exactly.
    * low_stock_count uses a queryset annotation (_stock) so it is resolved in a
      single SQL query rather than loading every product into Python and iterating.
    * recent_orders returns the 5 most recently created orders.
    """
    permission_classes = [IsAuthenticated]
    # DashboardView is read-only metadata — throttle at the standard user rate,
    # no special override needed.

    def get(self, request):
        now         = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        orders_this_month = SalesOrder.objects.filter(
            created_at__gte=month_start
        ).count()

        revenue_this_month = (
            SalesOrder.objects
            .filter(
                status__in=[SalesOrder.PAID, SalesOrder.SHIPPED, SalesOrder.DELIVERED],
                paid_at__gte=month_start,
            )
            .aggregate(total=Sum('total_amount'))['total']
        ) or Decimal('0.00')

        # Confirmed orders that have not yet been fully paid — the operator has
        # an obligation to collect money on all of these.
        pending_payments_count = SalesOrder.objects.filter(
            status=SalesOrder.CONFIRMED
        ).count()

        # Annotate each product with its net stock, then filter in SQL.
        # This avoids loading every product into Python memory (contrast with
        # the HTML dashboard's list comprehension over all_products).
        annotated = Product.objects.annotate(
            _stock=Coalesce(
                Sum('inventory_movements__quantity'),
                Value(0, output_field=IntegerField()),
            )
        )
        low_stock_count = annotated.filter(
            _stock__gt=0,
            _stock__lte=F('low_stock_threshold'),
        ).count() + annotated.filter(_stock__lte=0).count()

        recent_orders = (
            SalesOrder.objects
            .select_related('customer')
            .order_by('-created_at')[:5]
        )

        recent_orders_data = [
            {
                'id':           order.pk,
                'order_number': order.order_number,
                'customer':     order.customer.get_full_name(),
                'total_amount': str(order.total_amount),
                'status':       order.status,
                'created_at':   order.created_at.isoformat(),
            }
            for order in recent_orders
        ]

        return Response({
            'orders_this_month':      orders_this_month,
            'revenue_this_month':     f'{revenue_this_month:.2f}',
            'pending_payments_count': pending_payments_count,
            'low_stock_count':        low_stock_count,
            'recent_orders':          recent_orders_data,
        })
