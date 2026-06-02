from rest_framework import serializers

from core.models import ProductCategory


class ProductCategoryNestedSerializer(serializers.ModelSerializer):
    """
    Compact read-only representation used when embedding a category inside
    a Product response. Shows the full human-readable path via __str__.
    """
    display_name = serializers.SerializerMethodField()

    class Meta:
        model  = ProductCategory
        fields = ['id', 'name', 'display_name']
        read_only_fields = fields

    def get_display_name(self, obj) -> str:
        # ProductCategory.__str__ returns "Parent › Child" for nested categories.
        return str(obj)


class ProductCategorySerializer(serializers.ModelSerializer):
    """
    Full read/write serializer for ProductCategory.

    - parent_category: optional self-referential FK; accepted as a PK on write.
    - display_name: the full "Parent › Child" path string (read-only).
    - subcategories: list of immediate children (read-only, IDs only).
    """
    display_name    = serializers.SerializerMethodField()
    parent_category = serializers.PrimaryKeyRelatedField(
        queryset=ProductCategory.objects.all(),
        required=False,
        allow_null=True,
        default=None,
    )
    subcategories = serializers.PrimaryKeyRelatedField(
        many=True,
        read_only=True,
    )

    class Meta:
        model  = ProductCategory
        fields = [
            'id', 'name', 'description',
            'parent_category', 'display_name', 'subcategories',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'display_name', 'subcategories',
            'created_at', 'updated_at',
        ]

    def get_display_name(self, obj) -> str:
        return str(obj)

    def validate(self, data):
        parent = data.get('parent_category')
        if self.instance and parent and parent.pk == self.instance.pk:
            raise serializers.ValidationError(
                {'parent_category': 'A category cannot be its own parent.'}
            )
        return data
