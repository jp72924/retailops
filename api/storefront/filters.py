import django_filters

from core.models import Listing
from core.services.listings import category_and_descendants


class ListingFilter(django_filters.FilterSet):
    """
    FilterSet for GET /api/v1/storefront/catalog/listings/

      ?category=<id>        the category and every category beneath it
      ?display_type=<text>  exact match, ignoring case
    """
    category = django_filters.NumberFilter(method='filter_category')
    display_type = django_filters.CharFilter(field_name='display_type', lookup_expr='iexact')

    class Meta:
        model = Listing
        fields = []

    def filter_category(self, queryset, name, value):
        return queryset.filter(category_id__in=category_and_descendants(int(value)))
