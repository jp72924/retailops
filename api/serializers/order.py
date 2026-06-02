from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from core.models import InventoryMovement, Product, SalesOrder, SalesOrderItem
from api.serializers.customer import CustomerSerializer
from api.serializers.product import ProductSerializer


# ── Nested read serializers ───────────────────────────────────────────────────

class SalesOrderItemReadSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)

    class Meta:
        model  = SalesOrderItem
        fields = [
            'id', 'product', 'quantity', 'unit_price', 'tax_rate', 'line_total',
        ]
        read_only_fields = fields


class SalesOrderReadSerializer(serializers.ModelSerializer):
    customer       = CustomerSerializer(read_only=True)
    items          = SalesOrderItemReadSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    amount_paid    = serializers.SerializerMethodField()
    amount_outstanding = serializers.SerializerMethodField()

    class Meta:
        model  = SalesOrder
        fields = [
            'id', 'order_number', 'customer', 'status', 'status_display',
            'subtotal', 'tax_amount', 'discount_amount', 'total_amount',
            'amount_paid', 'amount_outstanding',
            'notes', 'items',
            'created_by', 'confirmed_by',
            'created_at', 'updated_at', 'confirmed_at', 'paid_at',
        ]
        read_only_fields = fields

    def get_amount_paid(self, obj) -> str:
        # Use annotation if present (avoids extra query on list views)
        if hasattr(obj, '_amount_paid'):
            return obj._amount_paid
        return obj.amount_paid

    def get_amount_outstanding(self, obj) -> str:
        paid = self.get_amount_paid(obj)
        return obj.total_amount - Decimal(str(paid))


# ── Write input serializers ───────────────────────────────────────────────────

class SalesOrderItemWriteSerializer(serializers.Serializer):
    """Single line-item sent inside the `items` array on create/update."""
    product_id  = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity    = serializers.IntegerField(min_value=1)
    unit_price  = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.01'),
        required=False,
    )


class SalesOrderWriteSerializer(serializers.ModelSerializer):
    """
    Used for POST (create) and PUT/PATCH (update).

    `items` replaces the HTML form's `product_N`/`quantity_N` convention with
    a clean JSON array.  The field is required on create (enforced in validate)
    and optional on partial update.

    If `unit_price` is omitted on a line item, the product's current catalogue
    price is used as the snapshot.
    """
    customer_id = serializers.PrimaryKeyRelatedField(
        source='customer',
        queryset=__import__('core.models', fromlist=['Customer']).Customer.objects.all(),
    )
    items = SalesOrderItemWriteSerializer(many=True, required=False)

    class Meta:
        model  = SalesOrder
        fields = [
            'customer_id', 'discount_amount', 'tax_amount', 'notes', 'items',
        ]

    def validate(self, attrs):
        # On create (no instance) require at least one line item
        if self.instance is None:
            items = attrs.get('items', [])
            if not items:
                raise serializers.ValidationError(
                    {'items': 'At least one line item is required.'}
                )
        return attrs

    def _apply_items(self, order, raw_items):
        """Replace all existing line items and recalculate order totals."""
        order.items.all().delete()
        subtotal = Decimal('0.00')
        for item_data in raw_items:
            product    = item_data['product_id']
            quantity   = item_data['quantity']
            unit_price = item_data.get('unit_price') or product.unit_price
            line_total = unit_price * quantity
            subtotal  += line_total
            SalesOrderItem.objects.create(
                sales_order=order,
                product=product,
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total,
            )
        order.subtotal      = subtotal
        order.total_amount  = subtotal + order.tax_amount - order.discount_amount
        order.save(update_fields=['subtotal', 'total_amount'])

    @transaction.atomic
    def create(self, validated_data):
        raw_items = validated_data.pop('items', [])
        order = SalesOrder.objects.create(**validated_data)
        self._apply_items(order, raw_items)
        return order

    @transaction.atomic
    def update(self, instance, validated_data):
        raw_items = validated_data.pop('items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if raw_items is not None:
            self._apply_items(instance, raw_items)
        return instance
