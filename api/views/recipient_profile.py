from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from core.models import RecipientProfile
from api.permissions import IsManagerOrAdmin
from api.serializers.recipient_profile import RecipientProfileSerializer


class RecipientProfileViewSet(ModelViewSet):
    """
    Recipient profile management — the fraud-prevention allowlist OCR-verified
    kiosk payments (Mobile Payment / Bank Transfer) are checked against.

    GET/POST/PUT/PATCH/DELETE  /api/v1/payment-recipient-profiles/

    Unlike ProductCategoryViewSet, list/retrieve are also Manager/Admin-only —
    these records carry bank-account and document identifiers used for fraud
    control, not general catalog data.

    At most one profile per payment_method may have is_primary=true — it marks
    the profile customer-facing systems should show when several are registered.
    Setting it on one profile automatically clears it from whichever other
    profile of the same payment_method currently holds it. A payment_method's
    only profile is always its primary one: the flag is applied automatically
    on create, and again if deleting one leaves a single profile behind.

    Filters:  ?payment_method=mobile_payment|bank_transfer, ?is_active=true|false,
              ?is_primary=true|false.
    Search:   ?search= on label, phone, account_number, bank, document_id.
    Ordering: ?ordering=payment_method|label|created_at.
    """
    queryset = RecipientProfile.objects.order_by('payment_method', 'label')
    serializer_class = RecipientProfileSerializer
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]
    # DjangoFilterBackend is a global backend, so without filterset_fields a
    # ?payment_method= query is silently ignored rather than rejected.
    filterset_fields = ['payment_method', 'is_active', 'is_primary']
    search_fields = ['label', 'phone', 'account_number', 'bank', 'document_id']
    ordering_fields = ['payment_method', 'label', 'created_at']
    ordering = ['payment_method', 'label']

    def perform_create(self, serializer):
        with transaction.atomic():
            if serializer.validated_data.get('is_primary', False):
                RecipientProfile.demote_other_primaries(serializer.validated_data['payment_method'])
            serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.instance
        is_primary = serializer.validated_data.get('is_primary', instance.is_primary)
        payment_method = serializer.validated_data.get('payment_method', instance.payment_method)
        with transaction.atomic():
            if is_primary:
                RecipientProfile.demote_other_primaries(payment_method, exclude_pk=instance.pk)
            serializer.save()
