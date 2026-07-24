from rest_framework import serializers

from core.models import RecipientProfile


class RecipientProfileSerializer(serializers.ModelSerializer):
    """
    Full read/write serializer for RecipientProfile.

    bank/document_id are always required. The identifying field is
    payment-method-dependent: mobile_payment requires phone (and rejects
    account_number); bank_transfer requires account_number (and rejects
    phone) — see validate(). A profile only matches a receipt when the
    relevant identifier plus bank plus document_id all agree (see
    core.services.receipt_matching.match_recipient_profile).
    """
    payment_method_display = serializers.CharField(source='get_payment_method_display', read_only=True)

    # Declared explicitly (rather than left to ModelSerializer's automatic
    # field generation) so DRF's unique-together handling for these two
    # fields doesn't force both to be present on every request just because
    # they're both part of the model's UniqueConstraint. Duplicate detection
    # is done by hand in validate() instead — see Meta.validators = [].
    phone = serializers.CharField(required=False, allow_blank=True, max_length=30)
    account_number = serializers.CharField(required=False, allow_blank=True, max_length=34)

    class Meta:
        model = RecipientProfile
        fields = [
            'id', 'label', 'payment_method', 'payment_method_display',
            'phone', 'account_number', 'bank', 'document_id', 'is_active',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at', 'payment_method_display']
        validators = []

    def validate(self, attrs):
        payment_method = attrs.get(
            'payment_method', self.instance.payment_method if self.instance else None,
        )
        phone = attrs.get('phone', self.instance.phone if self.instance else '') or ''
        account_number = attrs.get(
            'account_number', self.instance.account_number if self.instance else '',
        ) or ''
        bank = attrs.get('bank', self.instance.bank if self.instance else None)
        document_id = attrs.get('document_id', self.instance.document_id if self.instance else None)

        if payment_method == RecipientProfile.MOBILE_PAYMENT:
            if not phone.strip():
                raise serializers.ValidationError({'phone': 'Required for mobile payment profiles.'})
            if account_number.strip():
                raise serializers.ValidationError({
                    'account_number': 'Not used for mobile payment profiles — leave blank.'
                })
        elif payment_method == RecipientProfile.BANK_TRANSFER:
            if not account_number.strip():
                raise serializers.ValidationError({
                    'account_number': 'Required for bank transfer profiles.'
                })
            if phone.strip():
                raise serializers.ValidationError({
                    'phone': 'Not used for bank transfer profiles — leave blank.'
                })

        duplicate_qs = RecipientProfile.objects.filter(
            payment_method=payment_method, phone=phone, account_number=account_number,
            bank=bank, document_id=document_id,
        )
        if self.instance:
            duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
        if duplicate_qs.exists():
            raise serializers.ValidationError(
                'A profile with this exact payment method and identifying details already exists.'
            )
        return attrs
