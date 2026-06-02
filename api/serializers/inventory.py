from drf_spectacular.utils import extend_schema_field
from drf_spectacular.openapi import OpenApiTypes
from rest_framework import serializers

from core.models import InventoryMovement, Product


@extend_schema_field(OpenApiTypes.STR)
class _CreatedByField(serializers.Field):
    """Read-only field that returns the full name of the creating user."""

    def to_representation(self, value):
        return value.get_full_name()


class InventoryMovementSerializer(serializers.ModelSerializer):
    """
    Read-only representation of an InventoryMovement.

    - product: compact nested object (id, sku, name).
    - movement_type_display: human-readable label for movement_type.
    - created_by: full name string (not a nested user object — the audience
      is inventory auditing, not user management).
    """
    product = serializers.SerializerMethodField()
    movement_type_display = serializers.CharField(
        source='get_movement_type_display',
        read_only=True,
    )
    created_by = _CreatedByField(read_only=True)

    class Meta:
        model  = InventoryMovement
        fields = [
            'id',
            'product',
            'movement_type', 'movement_type_display',
            'quantity',
            'reference_type', 'reference_id',
            'notes',
            'created_by', 'created_at',
        ]
        read_only_fields = fields

    @extend_schema_field({'type': 'object', 'properties': {
        'id':   {'type': 'integer'},
        'sku':  {'type': 'string'},
        'name': {'type': 'string'},
    }})
    def get_product(self, obj):
        return {
            'id':   obj.product_id,
            'sku':  obj.product.sku,
            'name': obj.product.name,
        }


class ManualAdjustmentSerializer(serializers.Serializer):
    """
    Write-only serializer used exclusively by POST /api/v1/inventory/adjust/.

    Fills the CLAUDE.md gap: there is currently no UI for recording stock
    purchases or manual adjustments outside of the seed command.

    Fields:
      product_id  — the product to adjust
      quantity    — signed integer; positive = addition, negative = deduction
      notes       — optional free-text reason for the adjustment

    movement_type is fixed to 'adjustment'.
    reference_type is fixed to 'ManualAdjustment'.
    reference_id is set to 0 (no external document reference).
    created_by is injected from request.user in the ViewSet.
    """
    product_id = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.all(),
        source='product',
    )
    quantity = serializers.IntegerField()
    notes    = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_quantity(self, value):
        if value == 0:
            raise serializers.ValidationError('Quantity must be non-zero.')
        return value

    def create(self, validated_data):
        return InventoryMovement.objects.create(
            product=validated_data['product'],
            movement_type=InventoryMovement.ADJUSTMENT,
            quantity=validated_data['quantity'],
            reference_type=InventoryMovement.MANUAL_ADJUSTMENT,
            reference_id=0,
            notes=validated_data.get('notes', ''),
            created_by=validated_data['created_by'],
        )
