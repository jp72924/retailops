from rest_framework import serializers

from core.models import RecipientProfile


class RecipientProfileSerializer(serializers.ModelSerializer):
    """
    Full read/write serializer for RecipientProfile.

    phone/bank/document_id are all required — a profile only matches a
    receipt when all three fields agree (see core.services.receipt_matching
    .match_recipient_profile).
    """
    payment_method_display = serializers.CharField(source='get_payment_method_display', read_only=True)

    class Meta:
        model = RecipientProfile
        fields = [
            'id', 'label', 'payment_method', 'payment_method_display',
            'phone', 'bank', 'document_id', 'is_active',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at', 'payment_method_display']
