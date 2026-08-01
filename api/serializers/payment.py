from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from core.models import Payment, SalesOrder, SystemSettings


RECEIPT_PAYMENT_METHODS = {Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER}

# Mirrors the back-office rule in core.views.payment_create. The two sets
# overlap on BANK_TRANSFER, and that overlap is meaningful: such a payment can
# need both a bank reference and an OCR transaction key, which are different
# identifiers. Keep them separate, named for the rule each drives.
REFERENCE_REQUIRED_PAYMENT_METHODS = {Payment.BANK_TRANSFER, Payment.CARD, Payment.CHECK}


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
        instance = self.instance
        payment_method = attrs.get(
            'payment_method',
            instance.payment_method if instance else None,
        )
        # No .strip() needed: the generated CharField has trim_whitespace=True
        # and DRF trims before the allow_blank check, so "   " is already "".
        reference_number = attrs.get(
            'reference_number',
            instance.reference_number if instance else '',
        ) or ''
        transaction_key = (attrs.get('transaction_key') or '').strip()
        notes = (attrs.get('notes') or '').strip()

        # A conflict with an existing record, not a missing field on this one —
        # reported immediately and alone, above the collection below.
        if transaction_key:
            attrs['transaction_key'] = transaction_key
            queryset = Payment.objects.filter(transaction_key=transaction_key)
            if instance is not None:
                queryset = queryset.exclude(pk=instance.pk)
            if queryset.exists():
                raise serializers.ValidationError({
                    'transaction_key': 'A payment with this transaction key already exists.'
                })

        # ocr_applies is needed by the OCR check below and by the status
        # side-effects at the bottom, so compute it once — but only for receipt
        # methods, so a cash payment never reads SystemSettings (an uncached
        # get_or_create round-trip).
        is_receipt_method = payment_method in RECEIPT_PAYMENT_METHODS
        ocr_applies = False
        if is_receipt_method:
            settings = SystemSettings.get()
            enabled_methods = settings.ocr_enabled_methods or []
            ocr_applies = settings.ocr_enabled and payment_method in enabled_methods

        # Missing-field rules are collected so a caller sees all of them at once
        # instead of fixing one and rediscovering the next. Only bank_transfer
        # can reach more than one: card/check aren't receipt methods, and
        # mobile_payment doesn't require a reference.
        errors = {}

        if payment_method in REFERENCE_REQUIRED_PAYMENT_METHODS and not reference_number:
            errors['reference_number'] = (
                'A reference number is required for bank transfer, card, and check payments.'
            )

        if ocr_applies and not transaction_key and not notes:
            errors['transaction_key'] = (
                'Verified receipt transaction key is required, or provide '
                'manual override notes for pending review.'
            )
            errors['notes'] = (
                'Manual override notes are required when no OCR transaction key is supplied.'
            )

        if errors:
            raise serializers.ValidationError(errors)

        # Status side-effects run only once every rule above has passed, still
        # gated on RECEIPT_PAYMENT_METHODS exactly as before.
        if is_receipt_method:
            if ocr_applies and not transaction_key:
                attrs['status'] = Payment.PENDING_REVIEW
            elif attrs.get('ocr_receipt_data') and attrs.get('verified_at') is None:
                attrs['verified_at'] = timezone.now()

        return attrs
