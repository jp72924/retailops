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

    Search:   ?search= on label, phone, bank, document_id.
    Ordering: ?ordering=payment_method|label|created_at.
    """
    queryset = RecipientProfile.objects.order_by('payment_method', 'label')
    serializer_class = RecipientProfileSerializer
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]
    search_fields = ['label', 'phone', 'bank', 'document_id']
    ordering_fields = ['payment_method', 'label', 'created_at']
    ordering = ['payment_method', 'label']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
