"""
Orders and payments as a storefront sends and reads them.

What a channel reads about an order is deliberately small: its own order, its
lines, what is owed and what the customer was quoted, and for each payment only
how it reads to the customer. Receipt images, OCR readings, bank transaction
keys and reviewer notes stay inside RetailOps, and nothing here describes the
customer.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import RegexValidator
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.kiosk.serializers import KioskRecipientProfileSerializer
from api.serializers.currency import recorded_currency_payload, secondary_amount_payload
from core.models import (
    ENTITLEMENT_VALUE_MAX,
    ChannelOrder,
    Entitlement,
    Payment,
    RecipientProfile,
    SalesOrderItem,
    validate_channel_contact,
)
from core.services.channel_orders import MAX_LINE_QUANTITY, customer_status, review_status
from core.services.entitlements import delivery_complete
from core.services.receipt_validation import RECEIPT_PAYMENT_METHODS

from .serializers import LIVE_CURRENCY_SCHEMA

MAX_ORDER_LINES = 50

CUSTOMER_STATUSES = [
    'awaiting_payment', 'payment_under_review', 'processing', 'completed', 'cancelled', 'refunded',
]
REVIEW_STATUSES = ['verified', 'needs_review', 'rejected']
ENTITLEMENT_KINDS = ['key', 'link', 'instructions']
ENTITLEMENT_STATUSES = ['pending', 'active', 'revoked']

external_ref_validator = RegexValidator(
    r'^[\w.:@+-]+$',
    'Use letters, digits and . : @ + - _ only.',
)


def money(value):
    return format(Decimal(value).quantize(Decimal('0.01')), 'f')


# ── What a store sends ───────────────────────────────────────────────────────

class StorefrontCustomerSerializer(serializers.Serializer):
    external_ref = serializers.CharField(
        max_length=128, validators=[external_ref_validator],
        help_text="The store's own id for this customer. Stable: the same value is the same customer.",
    )
    email = serializers.EmailField(max_length=254)
    email_verified = serializers.BooleanField(
        default=False,
        help_text=(
            'True only if the store has verified this address. Honoured only for a channel '
            'RetailOps has been told to trust.'
        ),
    )
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default='')


class StorefrontOrderLineSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=100)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_LINE_QUANTITY)


class StorefrontOrderCreateSerializer(serializers.Serializer):
    client_reference = serializers.CharField(
        max_length=128,
        help_text=(
            "The store's own order id. It is the idempotency key: the same reference "
            'from the same channel is the same order.'
        ),
    )
    customer = StorefrontCustomerSerializer()
    items = StorefrontOrderLineSerializer(many=True, allow_empty=False)
    fulfillment = serializers.ChoiceField(choices=[c[0] for c in ChannelOrder.FULFILLMENT_CHOICES])
    contact = serializers.DictField(
        child=serializers.CharField(max_length=255, allow_blank=True),
        required=False,
        default=dict,
        help_text='Where to reach the buyer about this order: at most 10 short entries.',
    )

    def validate_items(self, value):
        if len(value) > MAX_ORDER_LINES:
            raise serializers.ValidationError(f'An order can have at most {MAX_ORDER_LINES} lines.')
        return value

    def validate_contact(self, value):
        try:
            validate_channel_contact(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value


class StorefrontReceiptSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=100, help_text='The bank reference printed on the receipt.')
    paid_on = serializers.CharField(max_length=40, help_text='The payment date, as on the receipt.')
    origin_bank = serializers.CharField(max_length=120)
    origin_phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    receipt_image_base64 = serializers.CharField(
        help_text='The receipt image, base64 or a data: URL. JPEG, PNG, HEIC or HEIF.',
    )
    receipt_image_content_type = serializers.CharField(max_length=50, required=False, allow_blank=True)


class StorefrontPaymentCreateSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=sorted(RECEIPT_PAYMENT_METHODS))
    receipt = StorefrontReceiptSerializer()


class StorefrontEntitlementValueSerializer(serializers.Serializer):
    value = serializers.CharField(
        max_length=ENTITLEMENT_VALUE_MAX,
        help_text='The licence key, link or instructions to deliver. A link must be an http(s) URL.',
    )


# ── What a store reads ───────────────────────────────────────────────────────

class StorefrontOrderLineOutSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source='product.sku', read_only=True)
    name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = SalesOrderItem
        fields = ['sku', 'name', 'quantity', 'unit_price', 'line_total']
        read_only_fields = fields


class StorefrontPaymentOutSerializer(serializers.ModelSerializer):
    review_status = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'payment_number', 'payment_method', 'reference_number', 'amount',
            'review_status', 'created_at',
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=REVIEW_STATUSES))
    def get_review_status(self, obj):
        return review_status(obj)


class StorefrontEntitlementSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source='order_item.product.sku', read_only=True)
    value = serializers.SerializerMethodField()

    class Meta:
        model = Entitlement
        fields = ['id', 'sku', 'kind', 'status', 'seats', 'value', 'activated_at', 'expires_at']
        read_only_fields = fields

    @extend_schema_field({
        'type': 'string', 'nullable': True,
        'description': (
            'The link or instructions to show the customer. A licence key is always masked '
            'here, showing only its last four characters; the whole key is read once, with '
            '`POST /entitlements/{id}/reveal/`. Null until the value is supplied.'
        ),
    })
    def get_value(self, obj):
        return obj.display_value


class StorefrontOrderSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='sales_order.order_number', read_only=True)
    customer_status = serializers.SerializerMethodField()
    items = StorefrontOrderLineOutSerializer(source='sales_order.items', many=True, read_only=True)
    total = serializers.DecimalField(
        source='sales_order.total_amount', max_digits=12, decimal_places=2, read_only=True,
    )
    amount_paid = serializers.SerializerMethodField()
    amount_outstanding = serializers.SerializerMethodField()
    quote = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    entitlements = serializers.SerializerMethodField()

    class Meta:
        model = ChannelOrder
        fields = [
            'order_number', 'client_reference', 'customer_status', 'created_at', 'expires_at',
            'fulfillment', 'contact', 'items', 'total', 'amount_paid', 'amount_outstanding',
            'quote', 'payments', 'entitlements',
        ]
        read_only_fields = fields

    def _payments(self, obj):
        # Prefetched by channel_orders(); sorted here so the order is stable.
        return sorted(obj.sales_order.payments.all(), key=lambda p: (p.created_at, p.pk))

    def _paid(self, obj):
        return sum(
            (p.amount for p in obj.sales_order.payments.all() if p.status == Payment.CONFIRMED),
            Decimal('0'),
        )

    @extend_schema_field(serializers.ChoiceField(choices=CUSTOMER_STATUSES))
    def get_customer_status(self, obj):
        return customer_status(
            obj.sales_order, obj.sales_order.payments.all(), delivery_complete(obj.sales_order),
        )

    def get_amount_paid(self, obj) -> str:
        return money(self._paid(obj))

    def get_amount_outstanding(self, obj) -> str:
        return money(obj.sales_order.total_amount - self._paid(obj))

    @extend_schema_field({
        'type': 'object',
        'description': (
            'What the customer was quoted when the order was created. It does not move: '
            'a receipt is validated against this rate, not against the live one.'
        ),
        'properties': {
            'currency': LIVE_CURRENCY_SCHEMA,
            'total': {'type': 'string', 'format': 'decimal'},
            'secondary_total': {
                'type': 'string', 'format': 'decimal', 'nullable': True,
                'description': 'The total in the secondary currency at the quoted rate.',
            },
        },
    })
    def get_quote(self, obj):
        snapshot, total = obj.currency_snapshot, obj.sales_order.total_amount
        return {
            'currency': recorded_currency_payload(snapshot),
            'total': money(total),
            'secondary_total': secondary_amount_payload(total, snapshot),
        }

    @extend_schema_field(StorefrontPaymentOutSerializer(many=True))
    def get_payments(self, obj):
        return StorefrontPaymentOutSerializer(self._payments(obj), many=True).data

    @extend_schema_field(StorefrontEntitlementSerializer(many=True))
    def get_entitlements(self, obj):
        # Prefetched by channel_orders(), so a list of orders costs no extra query.
        rows = [
            entitlement
            for item in obj.sales_order.items.all()
            for entitlement in item.entitlements.all()
        ]
        return StorefrontEntitlementSerializer(sorted(rows, key=lambda e: e.pk), many=True).data


class StorefrontOrderCreatedSerializer(StorefrontOrderSerializer):
    """The order, plus where the customer is told to pay."""
    payment_instructions = serializers.SerializerMethodField()

    class Meta(StorefrontOrderSerializer.Meta):
        fields = StorefrontOrderSerializer.Meta.fields + ['payment_instructions']
        read_only_fields = fields

    @extend_schema_field(KioskRecipientProfileSerializer(many=True))
    def get_payment_instructions(self, obj):
        return KioskRecipientProfileSerializer(
            RecipientProfile.primary_for_customers(), many=True,
        ).data
