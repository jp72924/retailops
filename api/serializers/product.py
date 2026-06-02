from rest_framework import serializers

from core.models import Product, ProductCategory
from .category import ProductCategoryNestedSerializer


def product_image_url(obj, request=None):
    url = obj.primary_image_url
    if not url:
        return ''
    if url.startswith(('http://', 'https://')):
        return url
    if request is not None:
        return request.build_absolute_uri(url)
    return url


class ProductSerializer(serializers.ModelSerializer):
    """
    Full read/write serializer for Product.

    N+1 fix
    -------
    Product.current_stock, is_low_stock, and is_out_of_stock are Python
    @property methods that each fire an aggregate SQL query. On a 25-item
    list page that would be up to 75 extra queries.

    ProductViewSet.get_queryset() annotates the queryset with:

        _stock = Coalesce(Sum('inventory_movements__quantity'), Value(0))

    The three SerializerMethodField getters below read obj._stock when the
    annotation is present and fall back to the @property only when the
    serializer is used outside of the ViewSet (e.g., in tests or the shell).
    This guarantees zero extra queries per object in normal API use.

    FK write pattern
    ----------------
    category     — nested read-only representation (ProductCategoryNestedSerializer)
    category_id  — PK accepted on write (write_only, source='category')
    """
    category = ProductCategoryNestedSerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=ProductCategory.objects.all(),
        source='category',
        write_only=True,
    )

    current_stock   = serializers.SerializerMethodField()
    is_low_stock    = serializers.SerializerMethodField()
    is_out_of_stock = serializers.SerializerMethodField()
    primary_image_url = serializers.SerializerMethodField()
    has_image = serializers.SerializerMethodField()
    clear_image = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model  = Product
        fields = [
            'id', 'sku', 'name', 'description',
            'category', 'category_id',
            'unit_of_measure', 'unit_price', 'low_stock_threshold',
            'image', 'external_image_url', 'primary_image_url', 'has_image',
            'clear_image',
            'is_active',
            'current_stock', 'is_low_stock', 'is_out_of_stock',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id',
            'primary_image_url', 'has_image',
            'current_stock', 'is_low_stock', 'is_out_of_stock',
            'created_at', 'updated_at',
        ]

    # ── Stock field getters ───────────────────────────────────────────────────

    def _stock_value(self, obj):
        """
        Return the annotated stock value when available, otherwise fall back
        to the @property (which issues a SQL aggregate).
        """
        if hasattr(obj, '_stock'):
            return obj._stock
        return obj.current_stock

    def get_current_stock(self, obj) -> int:
        return self._stock_value(obj)

    def get_is_low_stock(self, obj) -> bool:
        stock = self._stock_value(obj)
        return 0 < stock <= obj.low_stock_threshold

    def get_is_out_of_stock(self, obj) -> bool:
        return self._stock_value(obj) <= 0

    # ── Validation ────────────────────────────────────────────────────────────

    def get_primary_image_url(self, obj) -> str:
        return product_image_url(obj, self.context.get('request'))

    def get_has_image(self, obj) -> bool:
        return obj.has_image_source

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance
        clear_image = attrs.get('clear_image', False)
        is_active = attrs.get('is_active', instance.is_active if instance else True)
        image = attrs.get('image', instance.image if instance else None)
        external_image_url = attrs.get(
            'external_image_url',
            instance.external_image_url if instance else '',
        )

        if clear_image:
            image = None
        if is_active and not image and not (external_image_url or '').strip():
            raise serializers.ValidationError({
                'image': 'Active products need an uploaded image or an external image URL.',
            })
        return attrs

    def validate_sku(self, value):
        qs = Product.objects.filter(sku=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('A product with this SKU already exists.')
        return value

    def validate_unit_price(self, value):
        if value <= 0:
            raise serializers.ValidationError('Unit price must be greater than zero.')
        return value

    def validate_low_stock_threshold(self, value):
        if value < 0:
            raise serializers.ValidationError('Low stock threshold must be non-negative.')
        return value

    def create(self, validated_data):
        validated_data.pop('clear_image', None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        clear_image = validated_data.pop('clear_image', False)
        old_image_name = instance.image.name if instance.image else ''
        old_image_storage = instance.image.storage if instance.image else None
        replacing_image = 'image' in validated_data

        if clear_image:
            instance.image = None

        instance = super().update(instance, validated_data)

        if old_image_name and old_image_storage and (clear_image or replacing_image):
            if old_image_name != (instance.image.name if instance.image else ''):
                old_image_storage.delete(old_image_name)
        return instance
