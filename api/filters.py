import django_filters
from django.db.models import F, Q

from core.models import InventoryMovement, Payment, Product, SalesOrder


class ProductFilter(django_filters.FilterSet):
    """
    FilterSet for GET /api/v1/products/

    All standard fields (category, is_active) are handled by DjangoFilterBackend
    directly. The custom `stock` filter requires the queryset to carry the `_stock`
    annotation added by ProductViewSet.get_queryset() — it cannot be used with a
    plain, unannotated queryset.

    ?stock values:
      out   products where _stock <= 0
      low   products where 0 < _stock <= low_stock_threshold
      ok    products where _stock > low_stock_threshold
      all   no filter (same as omitting the param)
    """
    stock = django_filters.CharFilter(method='filter_stock')

    class Meta:
        model  = Product
        fields = ['category', 'is_active', 'unit_of_measure']

    def filter_stock(self, queryset, name, value):
        value = value.strip().lower()
        if value == 'out':
            return queryset.filter(_stock__lte=0)
        if value == 'low':
            # 0 < stock <= low_stock_threshold
            return queryset.filter(_stock__gt=0, _stock__lte=F('low_stock_threshold'))
        if value == 'ok':
            # stock > low_stock_threshold (fully stocked)
            return queryset.filter(_stock__gt=F('low_stock_threshold'))
        # 'all' or unrecognised value — return unfiltered
        return queryset


class SalesOrderFilter(django_filters.FilterSet):
    """
    FilterSet for GET /api/v1/orders/

      ?customer=<id>
      ?status=draft|pending|confirmed|paid|shipped|delivered|cancelled|refunded
      ?date_from=YYYY-MM-DD   filters on created_at date
      ?date_to=YYYY-MM-DD
    """
    date_from = django_filters.DateFilter(field_name='created_at', lookup_expr='date__gte')
    date_to   = django_filters.DateFilter(field_name='created_at', lookup_expr='date__lte')

    class Meta:
        model  = SalesOrder
        fields = ['customer', 'status']


class PaymentFilter(django_filters.FilterSet):
    """
    FilterSet for GET /api/v1/payments/

      ?sales_order=<id>
      ?payment_method=cash|bank_transfer|card|check|other
      ?method=cash|mobile_payment|bank_transfer|card|check|other
      ?status=confirmed|pending_review
      ?has_receipt=true|false
      ?bank=<partial bank name>
      ?date_from=YYYY-MM-DD
      ?date_to=YYYY-MM-DD
    """
    date_from = django_filters.DateFilter(field_name='created_at', lookup_expr='date__gte')
    date_to   = django_filters.DateFilter(field_name='created_at', lookup_expr='date__lte')
    method = django_filters.CharFilter(field_name='payment_method')
    has_receipt = django_filters.BooleanFilter(method='filter_has_receipt')
    bank = django_filters.CharFilter(method='filter_bank')

    class Meta:
        model  = Payment
        fields = ['sales_order', 'payment_method', 'status']

    def filter_has_receipt(self, queryset, name, value):
        if value:
            return queryset.exclude(Q(receipt_image='') | Q(receipt_image__isnull=True))
        return queryset.filter(Q(receipt_image='') | Q(receipt_image__isnull=True))

    def filter_bank(self, queryset, name, value):
        value = value.strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(origin_bank__icontains=value) | Q(recipient_bank__icontains=value)
        )


class InventoryMovementFilter(django_filters.FilterSet):
    """
    FilterSet for GET /api/v1/inventory/

    date_from / date_to filter on the created_at date (not datetime), so
    ?date_from=2026-01-01 includes all movements from midnight on that date.
    """
    date_from = django_filters.DateFilter(field_name='created_at', lookup_expr='date__gte')
    date_to   = django_filters.DateFilter(field_name='created_at', lookup_expr='date__lte')

    class Meta:
        model  = InventoryMovement
        fields = ['product', 'movement_type', 'reference_type']
