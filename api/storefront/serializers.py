"""
What a storefront sees of the catalogue.

Everything here is deliberately narrower than the staff API. Prices come from
the product; availability is a label (`in_stock`, `low_stock`, `out_of_stock`
or `unlimited`) and never a count; and nothing identifies a customer, an order
or an internal user.
"""
from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.serializers.currency import live_currency, recorded_currency_payload
from api.serializers.product import absolute_image_url
from core.models import Listing, ListingVariant, ProductCategory
from core.services.currency import current_context
from core.services.listings import (
    VARIANTS_ATTR,
    listing_availability,
    variant_availability,
)

AVAILABILITY_VALUES = ['in_stock', 'low_stock', 'out_of_stock', 'unlimited']

# The shape of the `currency` block every storefront response carries. The
# currency in force when the response was produced: figures derived from it
# are indicative, because an order quotes its own amount when it is created.
LIVE_CURRENCY_SCHEMA = {
    'type': 'object',
    'description': (
        'The live currency configuration. Indicative only: an order quotes its '
        'own amount in the secondary currency when it is created.'
    ),
    'properties': {
        'code': {'type': 'string'},
        'symbol': {'type': 'string'},
        'decimal_places': {'type': 'integer'},
        'secondary': {
            'type': 'object',
            'nullable': True,
            'description': 'Null when no secondary currency is configured.',
            'properties': {
                'code': {'type': 'string'},
                'symbol': {'type': 'string'},
                'decimal_places': {'type': 'integer'},
                'rate': {'type': 'string', 'description': 'Secondary units per 1 primary unit.'},
                'rate_as_of': {'type': 'string', 'format': 'date-time', 'nullable': True},
                'rate_source': {'type': 'string', 'enum': ['fetched', 'manual', 'unknown']},
            },
        },
    },
}


def live_currency_payload():
    return recorded_currency_payload(current_context())


class StorefrontCategoryRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ['id', 'name']
        read_only_fields = fields


class StorefrontVariantSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source='product.sku', read_only=True)
    kind = serializers.CharField(source='product.kind', read_only=True)
    price = serializers.DecimalField(
        source='product.unit_price', max_digits=10, decimal_places=2, read_only=True,
    )
    compare_at_price = serializers.SerializerMethodField()
    availability = serializers.SerializerMethodField()

    class Meta:
        model = ListingVariant
        fields = ['sku', 'label', 'kind', 'price', 'compare_at_price', 'availability']
        read_only_fields = fields

    @extend_schema_field({
        'type': 'string', 'format': 'decimal', 'nullable': True,
        'description': 'The struck-through price. Null unless it is higher than `price`.',
    })
    def get_compare_at_price(self, obj):
        compare, price = obj.compare_at_price, obj.product.unit_price
        if compare is None or compare <= price:
            return None
        return format(compare.quantize(Decimal('0.01')), 'f')

    @extend_schema_field(serializers.ChoiceField(choices=AVAILABILITY_VALUES))
    def get_availability(self, obj):
        return variant_availability(obj.product, obj._stock)


class StorefrontListingSerializer(serializers.ModelSerializer):
    category = StorefrontCategoryRefSerializer(read_only=True)
    image_url = serializers.SerializerMethodField()
    from_price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    availability = serializers.SerializerMethodField()
    variants = StorefrontVariantSerializer(source=VARIANTS_ATTR, many=True, read_only=True)

    class Meta:
        model = Listing
        fields = [
            'slug', 'title', 'summary', 'badge', 'display_type', 'category',
            'image_url', 'from_price', 'availability', 'variants',
            'features', 'attributes',
        ]
        read_only_fields = fields

    def _first_variant(self, obj):
        variants = getattr(obj, VARIANTS_ATTR)
        return variants[0] if variants else None

    def get_image_url(self, obj) -> str:
        url = obj.primary_image_url
        if not url:
            first = self._first_variant(obj)
            url = first.product.primary_image_url if first else ''
        return absolute_image_url(url, self.context.get('request'))

    @extend_schema_field(serializers.ChoiceField(choices=AVAILABILITY_VALUES))
    def get_availability(self, obj):
        return listing_availability(
            variant_availability(variant.product, variant._stock)
            for variant in getattr(obj, VARIANTS_ATTR)
        )


class StorefrontListingDetailSerializer(StorefrontListingSerializer):
    description = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()

    class Meta(StorefrontListingSerializer.Meta):
        fields = StorefrontListingSerializer.Meta.fields + ['description', 'currency']
        read_only_fields = fields

    def get_description(self, obj) -> str:
        if obj.description:
            return obj.description
        first = self._first_variant(obj)
        return first.product.description if first else ''

    @extend_schema_field(LIVE_CURRENCY_SCHEMA)
    def get_currency(self, obj):
        return recorded_currency_payload(live_currency(self))


# ── Envelopes that only exist to describe responses in the OpenAPI schema ────

class _WithCurrency(serializers.Serializer):
    currency = serializers.SerializerMethodField()

    @extend_schema_field(LIVE_CURRENCY_SCHEMA)
    def get_currency(self, obj):  # pragma: no cover - documentation only
        return None


class CategoryNodeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    listing_count = serializers.IntegerField(
        help_text='Published listings under this category, including its descendants.',
    )
    children = serializers.ListField(
        child=serializers.DictField(),
        help_text='Child categories, each with this same shape.',
    )


class CategoryTreeResponseSerializer(_WithCurrency):
    results = CategoryNodeSerializer(many=True)


class PaymentInstructionSerializer(serializers.Serializer):
    payment_method = serializers.CharField()
    payment_method_display = serializers.CharField()
    bank = serializers.CharField()
    phone = serializers.CharField()
    account_number = serializers.CharField()
    document_id = serializers.CharField()


class PaymentInstructionsResponseSerializer(_WithCurrency):
    results = PaymentInstructionSerializer(many=True)
