from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from core.models import InventoryMovement
from api.filters import InventoryMovementFilter
from api.permissions import IsManagerOrAdmin
from api.throttling import InventoryAdjustRateThrottle
from api.serializers.inventory import InventoryMovementSerializer, ManualAdjustmentSerializer


class InventoryMovementViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    GenericViewSet,
):
    """
    Inventory movement log.

    GET /api/v1/inventory/          paginated movement list (any auth user)
    GET /api/v1/inventory/<id>/     single movement (any auth user)
    POST /api/v1/inventory/adjust/  record a manual stock adjustment (Manager+)

    This fills the gap documented in CLAUDE.md: "No UI for recording stock
    purchases or manual adjustments — movements only come from order
    confirmations and the seed command."

    Filtering (via django-filter):
      ?product=<id>
      ?movement_type=sale|purchase|adjustment|return
      ?reference_type=SalesOrder|PurchaseOrder|ManualAdjustment|Return
      ?date_from=YYYY-MM-DD
      ?date_to=YYYY-MM-DD

    Ordering (DRF OrderingFilter):
      ?ordering=created_at (default: -created_at, newest first)
    """
    serializer_class   = InventoryMovementSerializer
    filterset_class    = InventoryMovementFilterSet = InventoryMovementFilter
    ordering_fields    = ['created_at']
    ordering           = ['-created_at']

    def get_queryset(self):
        return (
            InventoryMovement.objects
            .select_related('product', 'created_by')
            .order_by('-created_at')
        )

    def get_throttles(self):
        if self.action in ('adjust', 'bulk_adjust'):
            return [InventoryAdjustRateThrottle()]
        return super().get_throttles()

    # ── Manual adjustment action ──────────────────────────────────────────────

    @action(
        detail=False,
        methods=['post'],
        permission_classes=[IsAuthenticated, IsManagerOrAdmin],
        url_path='adjust',
    )
    def adjust(self, request):
        """
        POST /api/v1/inventory/adjust/

        Record a manual stock adjustment for a product.

        Request body:
            {
                "product_id": 7,
                "quantity":   -5,       // negative = deduction, positive = addition
                "notes":      "Damaged stock written off."
            }

        Response 201:
            The created InventoryMovement in standard read format.

        movement_type is fixed to 'adjustment'.
        reference_type is fixed to 'ManualAdjustment'.
        reference_id is set to 0 (no external document reference).
        """
        serializer = ManualAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        movement = serializer.save(created_by=request.user)

        read_serializer = InventoryMovementSerializer(movement)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

    # ── Bulk adjustment action ────────────────────────────────────────────────

    @action(
        detail=False,
        methods=['post'],
        permission_classes=[IsAuthenticated, IsManagerOrAdmin],
        url_path='bulk-adjust',
    )
    def bulk_adjust(self, request):
        """
        POST /api/v1/inventory/bulk-adjust/

        Record manual stock adjustments for multiple products in one request.
        Each adjustment is processed independently — failures are collected
        and reported without aborting the rest of the batch.

        Request body:
            {
                "adjustments": [
                    {"product_id": 1, "quantity": 50, "notes": "Restock"},
                    {"product_id": 2, "quantity": -5, "notes": "Damaged"}
                ]
            }

        Each adjustment follows the same rules as POST /inventory/adjust/:
          - quantity must be a non-zero integer
          - movement_type is fixed to "adjustment"
          - reference_type is fixed to "ManualAdjustment"

        Response 200:
            {
                "succeeded": [ <InventoryMovementSerializer>, ... ],
                "failed":    [ {"product_id": <int>, "error": "<reason>"}, ... ]
            }

        Requires Manager or Admin role.
        """
        from core.models import Product

        adjustments = request.data.get('adjustments')
        if not isinstance(adjustments, list) or not adjustments:
            return Response(
                {'error': '"adjustments" must be a non-empty list.', 'code': 'invalid_request'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        succeeded = []
        failed    = []

        for entry in adjustments:
            product_id = entry.get('product_id')
            quantity   = entry.get('quantity')
            notes      = entry.get('notes', '')

            # Validate individual entry
            if not isinstance(product_id, int):
                failed.append({'product_id': product_id, 'error': '"product_id" must be an integer.'})
                continue
            if not isinstance(quantity, int) or quantity == 0:
                failed.append({'product_id': product_id, 'error': '"quantity" must be a non-zero integer.'})
                continue

            try:
                product = Product.objects.get(pk=product_id)
            except Product.DoesNotExist:
                failed.append({'product_id': product_id, 'error': 'Product not found.'})
                continue

            try:
                movement = InventoryMovement.objects.create(
                    product=product,
                    movement_type=InventoryMovement.ADJUSTMENT,
                    quantity=quantity,
                    reference_type=InventoryMovement.MANUAL_ADJUSTMENT,
                    reference_id=0,
                    notes=notes,
                    created_by=request.user,
                )
            except Exception as exc:
                failed.append({'product_id': product_id, 'error': str(exc)})
                continue

            succeeded.append(InventoryMovementSerializer(movement).data)

        return Response({'succeeded': succeeded, 'failed': failed})
