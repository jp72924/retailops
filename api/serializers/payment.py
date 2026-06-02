from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from core.models import Payment, SalesOrder, SystemSettings


RECEIPT_PAYMENT_METHODS = {Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER}


class ReceiptVerifySerializer(serializers.Serializer):
    image = serializers.FileField()
    sales_order = serializers.PrimaryKeyRelatedField(
        queryset=SalesOrder.objects.all(),
        required=False,
        allow_null=True,
    )
    payment_method = serializers.ChoiceField(
        choices=[Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER],
    )
    expected_amount_usd = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
    )
    expected_reference = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )
    expected_paid_on = serializers.DateField(required=False)
    expected_origin_bank = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        if not attrs.get('sales_order') and attrs.get('expected_amount_usd') is None:
            raise serializers.ValidationError({
                'expected_amount_usd': (
                    'Provide expected_amount_usd when verifying without a sales_order.'
                ),
            })
        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    """
    Single serializer used for both reads and writes.

    On write:
      - `payment_number` is auto-generated — ignored if supplied.
      - `recorded_by` is injected from `request.user` by the view.
      - `sales_order` must be in CONFIRMED status (validated here).
      - `amount` must be > 0.

    On read the full payment record is returned, with a compact
    nested representation of the linked sales order.
    """
    payment_method_display = serializers.CharField(
        source='get_payment_method_display',
        read_only=True,
    )
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True,
    )
    sales_order_number = serializers.CharField(
        source='sales_order.order_number',
        read_only=True,
    )
    recorded_by_name = serializers.SerializerMethodField()
    receipt_image = serializers.ImageField(
        required=False,
        allow_null=True,
        write_only=True,
    )
    ocr_receipt_data = serializers.JSONField(required=False, allow_null=True)
    transaction_key = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model  = Payment
        fields = [
            'id', 'payment_number',
            'sales_order', 'sales_order_number',
            'amount',
            'payment_method', 'payment_method_display',
            'status', 'status_display',
            'reference_number',
            'receipt_image', 'ocr_receipt_data',
            'transaction_key', 'origin_phone', 'origin_bank',
            'recipient_bank', 'recipient_account', 'verified_at',
            'notes',
            'recorded_by', 'recorded_by_name',
            'created_at',
        ]
        read_only_fields = [
            'id', 'payment_number', 'sales_order_number',
            'payment_method_display', 'status_display',
            'recorded_by', 'recorded_by_name',
            'verified_at', 'created_at',
        ]

    def get_recorded_by_name(self, obj) -> str:
        return obj.recorded_by.get_full_name() or obj.recorded_by.email

    def validate_sales_order(self, order):
        if order.status != SalesOrder.CONFIRMED:
            raise serializers.ValidationError(
                f'Payments can only be recorded against Confirmed orders '
                f'(current status: {order.get_status_display()}).'
            )
        return order

    def validate_amount(self, value):
        if value <= Decimal('0'):
            raise serializers.ValidationError('Amount must be greater than zero.')
        return value

    def validate(self, attrs):
        payment_method = attrs.get(
            'payment_method',
            self.instance.payment_method if self.instance else None,
        )
        transaction_key = (attrs.get('transaction_key') or '').strip()
        notes = (attrs.get('notes') or '').strip()

        if transaction_key:
            attrs['transaction_key'] = transaction_key
            queryset = Payment.objects.filter(transaction_key=transaction_key)
            if self.instance is not None:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError({
                    'transaction_key': 'A payment with this transaction key already exists.'
                })

        if payment_method in RECEIPT_PAYMENT_METHODS:
            settings = SystemSettings.get()
            enabled_methods = settings.ocr_enabled_methods or []
            ocr_applies = settings.ocr_enabled and payment_method in enabled_methods

            if ocr_applies and not transaction_key and not notes:
                raise serializers.ValidationError({
                    'transaction_key': (
                        'Verified receipt transaction key is required, or provide '
                        'manual override notes for pending review.'
                    ),
                    'notes': 'Manual override notes are required when no OCR transaction key is supplied.',
                })

            if ocr_applies and not transaction_key:
                attrs['status'] = Payment.PENDING_REVIEW
            elif attrs.get('ocr_receipt_data') and attrs.get('verified_at') is None:
                attrs['verified_at'] = timezone.now()

        return attrs
