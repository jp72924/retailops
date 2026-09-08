from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from core.exceptions import (
    DomainError,
    EmptyOrderError,
    InsufficientStockError,
    OrderStateError,
)
from core.models import InventoryMovement, Payment, SalesOrder
from api.filters import SalesOrderFilter
from api.permissions import IsAdminRole, IsManagerOrAdmin, IsStaffOrAbove
from api.throttling import OrderTransitionRateThrottle
from api.serializers.order import SalesOrderReadSerializer, SalesOrderWriteSerializer
from api.views.product import _annotated_products


_TRANSITION_ACTIONS = frozenset({'submit', 'confirm', 'ship', 'deliver', 'cancel', 'refund'})


# Domain rule violations carry their own HTTP shape. Both confirm() and
# bulk_transition() raise the same exceptions, so the mapping lives once here
# rather than being restated per action -- that duplication is how the two
# drifted apart before, letting bulk confirm bypass checks the single-order
# path enforced.
def _domain_error_payload(exc):
    """Return the {error, code, ...} body for a DomainError."""
    if isinstance(exc, InsufficientStockError):
        return {
            'error': 'Insufficient stock',
            'code': 'insufficient_stock',
            # Top-level key, matching the kiosk checkout's existing contract
            # (api/kiosk/views.py) so clients parse one shape for one code.
            'insufficient': exc.shortfalls,
        }
    if isinstance(exc, EmptyOrderError):
        return {'error': str(exc), 'code': 'no_items'}
    if isinstance(exc, OrderStateError):
        return {'error': str(exc), 'code': 'wrong_status'}
    return {'error': str(exc), 'code': 'domain_error'}


def _domain_error_response(exc):
    """409 Response for a DomainError."""
    return Response(_domain_error_payload(exc), status=status.HTTP_409_CONFLICT)


def _annotated_orders():
    """
    Base queryset for orders with the _amount_paid annotation baked in.

    _amount_paid uses a correlated Subquery rather than Sum() over the reverse
    relation.  Sum() forces a LEFT JOIN + GROUP BY, and DRF pagination calls
    .count() on this queryset on every list request -- Django cannot count a
    grouped query without wrapping it as a subquery and materialising every
    group, which cost a fixed ~230ms per request independent of page size.
    Subquery.contains_aggregate is False, so QuerySet._annotate() never sets
    group_by and the pagination count stays a plain COUNT(*) on core_salesorder.

    The items prefetch reuses _annotated_products() so the nested
    ProductSerializer reads the _stock annotation instead of falling back to
    Product.current_stock -- a plain @property that fires one aggregate per
    call, and which the serializer calls three times per product
    (current_stock, is_low_stock, is_out_of_stock).
    """
    money = DecimalField(max_digits=12, decimal_places=2)
    confirmed_payment_total = (
        Payment.objects
        .filter(sales_order=OuterRef('pk'), status=Payment.CONFIRMED)
        .values('sales_order')
        .annotate(total=Sum('amount'))
        .values('total')
    )
    return (
        SalesOrder.objects
        .select_related('customer', 'created_by', 'confirmed_by')
        .prefetch_related(
            Prefetch('items__product', queryset=_annotated_products().order_by())
        )
        .annotate(
            _amount_paid=Coalesce(
                Subquery(confirmed_payment_total, output_field=money),
                Value(Decimal('0.00')),
                output_field=money,
            )
        )
        .order_by('-created_at')
    )


class OrderViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    GenericViewSet,
):
    """
    Sales order management.

    GET    /api/v1/orders/              list (any auth user)
    POST   /api/v1/orders/              create (Staff+)
    GET    /api/v1/orders/<id>/         retrieve (any auth user)
    PUT    /api/v1/orders/<id>/         full update (Staff+, Draft only)
    PATCH  /api/v1/orders/<id>/         partial update (Staff+, Draft only)
    DELETE /api/v1/orders/<id>/         delete (Staff+, Draft only)

    Transition actions (each POST):
      /api/v1/orders/<id>/submit/       Draft → Pending (Staff+)
      /api/v1/orders/<id>/confirm/      Pending → Confirmed (Manager+)
      /api/v1/orders/<id>/ship/         Paid → Shipped (Staff+)
      /api/v1/orders/<id>/deliver/      Shipped → Delivered (Staff+)
      /api/v1/orders/<id>/cancel/       Confirmed → Cancelled (Manager+)
      /api/v1/orders/<id>/refund/       Paid → Refunded (Admin only)

    Filtering:
      ?customer=<id>
      ?status=draft|pending|confirmed|paid|shipped|delivered|cancelled|refunded
      ?date_from=YYYY-MM-DD
      ?date_to=YYYY-MM-DD

    Search:
      ?search=<term>   matches order_number

    Ordering:
      ?ordering=created_at|total_amount (prefix - for descending)
    """
    filterset_class = SalesOrderFilter
    search_fields   = ['order_number']
    ordering_fields = ['created_at', 'total_amount']
    ordering        = ['-created_at']

    def get_queryset(self):
        return _annotated_orders()

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve', 'submit', 'confirm', 'ship',
                           'deliver', 'cancel', 'refund', 'bulk_transition'):
            return SalesOrderReadSerializer
        return SalesOrderWriteSerializer

    def get_permissions(self):
        # NOTE: this override replaces permission_classes entirely, including
        # any set on an @action decorator — so every action must be named here.
        # bulk_transition declares IsManagerOrAdmin on its decorator, but that
        # was never consulted, letting Staff bulk-confirm (and so deduct stock)
        # through an endpoint whose single-order equivalent requires Manager.
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated()]
        if self.action in ('confirm', 'cancel', 'bulk_transition'):
            return [IsAuthenticated(), IsManagerOrAdmin()]
        if self.action == 'refund':
            return [IsAuthenticated(), IsAdminRole()]
        # create, update, partial_update, destroy, submit, ship, deliver
        return [IsAuthenticated(), IsStaffOrAbove()]

    def get_throttles(self):
        if self.action in _TRANSITION_ACTIONS or self.action == 'bulk_transition':
            return [OrderTransitionRateThrottle()]
        return super().get_throttles()

    # ── Guards ────────────────────────────────────────────────────────────────

    def _require_status(self, order, expected_status):
        """Return 409 response if the order is not in the expected status."""
        if order.status != expected_status:
            return Response(
                {
                    'error': (
                        f'This action requires status "{expected_status}"; '
                        f'order is currently "{order.status}".'
                    ),
                    'code': 'wrong_status',
                },
                status=status.HTTP_409_CONFLICT,
            )
        return None

    def destroy(self, request, *args, **kwargs):
        order = self.get_object()
        if order.status != SalesOrder.DRAFT:
            return Response(
                {
                    'error': 'Only Draft orders can be deleted.',
                    'code': 'wrong_status',
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def create(self, request, *args, **kwargs):
        write_serializer = SalesOrderWriteSerializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        self.perform_create(write_serializer)
        instance = _annotated_orders().get(pk=write_serializer.instance.pk)
        return Response(
            SalesOrderReadSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        order = self.get_object()
        if order.status != SalesOrder.DRAFT:
            return Response(
                {'error': 'Only Draft orders can be edited.', 'code': 'wrong_status'},
                status=status.HTTP_409_CONFLICT,
            )
        partial = kwargs.pop('partial', False)
        write_serializer = SalesOrderWriteSerializer(
            order, data=request.data, partial=partial
        )
        write_serializer.is_valid(raise_exception=True)
        write_serializer.save()
        instance = _annotated_orders().get(pk=order.pk)
        return Response(SalesOrderReadSerializer(instance).data)

    # ── Transition actions ────────────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """POST /api/v1/orders/<id>/submit/  —  Draft → Pending."""
        order = self.get_object()
        err = self._require_status(order, SalesOrder.DRAFT)
        if err:
            return err

        if not order.items.exists():
            return Response(
                {'error': 'Cannot submit an order with no line items.', 'code': 'no_items'},
                status=status.HTTP_409_CONFLICT,
            )

        order.status = SalesOrder.PENDING
        order.save(update_fields=['status', 'updated_at'])
        return Response(SalesOrderReadSerializer(order).data)

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """
        POST /api/v1/orders/<id>/confirm/  —  Pending → Confirmed.
        Side-effect: deducts stock via InventoryMovement per line item.

        Rejects the transition with 409 insufficient_stock if any line exceeds
        available stock. The rule lives in SalesOrder.confirm(); this method
        only translates its failures into the API error envelope.
        """
        order = self.get_object()
        try:
            order.confirm(request.user)
        except DomainError as exc:
            return _domain_error_response(exc)

        return Response(SalesOrderReadSerializer(self.get_object()).data)

    @action(detail=True, methods=['post'])
    def ship(self, request, pk=None):
        """POST /api/v1/orders/<id>/ship/  —  Paid → Shipped."""
        order = self.get_object()
        err = self._require_status(order, SalesOrder.PAID)
        if err:
            return err

        order.status = SalesOrder.SHIPPED
        order.save(update_fields=['status', 'updated_at'])
        return Response(SalesOrderReadSerializer(order).data)

    @action(detail=True, methods=['post'])
    def deliver(self, request, pk=None):
        """POST /api/v1/orders/<id>/deliver/  —  Shipped → Delivered."""
        order = self.get_object()
        err = self._require_status(order, SalesOrder.SHIPPED)
        if err:
            return err

        order.status = SalesOrder.DELIVERED
        order.save(update_fields=['status', 'updated_at'])
        return Response(SalesOrderReadSerializer(order).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        POST /api/v1/orders/<id>/cancel/  —  Confirmed → Cancelled.
        Side-effect: restores stock via InventoryMovement per line item.
        """
        order = self.get_object()
        err = self._require_status(order, SalesOrder.CONFIRMED)
        if err:
            return err

        with transaction.atomic():
            order.status = SalesOrder.CANCELLED
            order.save(update_fields=['status', 'updated_at'])

            movements = [
                InventoryMovement(
                    product=item.product,
                    movement_type=InventoryMovement.RETURN,
                    quantity=item.quantity,
                    reference_type=InventoryMovement.SALES_ORDER,
                    reference_id=order.pk,
                    notes=f'Stock restored on cancellation of {order.order_number}',
                    created_by=request.user,
                )
                for item in order.items.select_related('product').all()
            ]
            InventoryMovement.objects.bulk_create(movements)

        return Response(SalesOrderReadSerializer(order).data)

    @action(detail=True, methods=['post'])
    def refund(self, request, pk=None):
        """
        POST /api/v1/orders/<id>/refund/  —  Paid → Refunded (Admin only).
        Side-effect: adds stock back via InventoryMovement per line item.
        """
        order = self.get_object()
        err = self._require_status(order, SalesOrder.PAID)
        if err:
            return err

        with transaction.atomic():
            order.status = SalesOrder.REFUNDED
            order.save(update_fields=['status', 'updated_at'])

            movements = [
                InventoryMovement(
                    product=item.product,
                    movement_type=InventoryMovement.RETURN,
                    quantity=item.quantity,
                    reference_type=InventoryMovement.SALES_ORDER,
                    reference_id=order.pk,
                    notes=f'Stock restored on refund of {order.order_number}',
                    created_by=request.user,
                )
                for item in order.items.select_related('product').all()
            ]
            InventoryMovement.objects.bulk_create(movements)

        return Response(SalesOrderReadSerializer(order).data)

    # ── Bulk transitions ──────────────────────────────────────────────────────

    @action(
        detail=False,
        methods=['post'],
        url_path='bulk-transition',
        permission_classes=[IsAuthenticated, IsManagerOrAdmin],
    )
    def bulk_transition(self, request):
        """
        POST /api/v1/orders/bulk-transition/

        Apply a status transition to multiple orders in a single request.
        Each order is processed independently — failures are collected and
        reported without aborting the rest of the batch.

        Request body:
            {
                "order_ids": [1, 2, 3],
                "action":    "confirm" | "ship" | "deliver"
            }

        Supported actions and their required source status:
            confirm  — Pending → Confirmed (deducts stock; Manager+)
            ship     — Paid → Shipped
            deliver  — Shipped → Delivered

        Response 200:
            {
                "succeeded": [ <SalesOrderReadSerializer>, ... ],
                "failed":    [ {"id": <int>, "error": "<reason>"}, ... ]
            }

        Requires Manager or Admin role.
        """
        order_ids   = request.data.get('order_ids')
        action_name = request.data.get('action', '').strip()

        if not isinstance(order_ids, list) or not order_ids:
            return Response(
                {'error': '"order_ids" must be a non-empty list of integers.', 'code': 'invalid_request'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _ALLOWED = {'confirm', 'ship', 'deliver'}
        if action_name not in _ALLOWED:
            return Response(
                {'error': f'"action" must be one of: {", ".join(sorted(_ALLOWED))}.', 'code': 'invalid_action'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _REQUIRED_STATUS = {
            'confirm': SalesOrder.PENDING,
            'ship':    SalesOrder.PAID,
            'deliver': SalesOrder.SHIPPED,
        }
        required_status = _REQUIRED_STATUS[action_name]

        succeeded = []
        failed    = []

        for oid in order_ids:
            try:
                order = (
                    SalesOrder.objects
                    .select_related('customer', 'created_by', 'confirmed_by')
                    .prefetch_related('items__product')
                    .get(pk=oid)
                )
            except SalesOrder.DoesNotExist:
                failed.append({'id': oid, 'error': 'Order not found.'})
                continue

            if order.status != required_status:
                failed.append({
                    'id': oid,
                    'error': (
                        f'Expected status "{required_status}"; '
                        f'order is currently "{order.status}".'
                    ),
                })
                continue

            try:
                with transaction.atomic():
                    if action_name == 'confirm':
                        # Same SalesOrder.confirm() the single-order action
                        # calls, so bulk confirmation enforces the identical
                        # stock rule. Re-implementing the transition here is
                        # what previously let this path skip checks.
                        order.confirm(request.user)
                    elif action_name == 'ship':
                        order.status = SalesOrder.SHIPPED
                        order.save(update_fields=['status', 'updated_at'])
                    elif action_name == 'deliver':
                        order.status = SalesOrder.DELIVERED
                        order.save(update_fields=['status', 'updated_at'])
            except DomainError as exc:
                payload = _domain_error_payload(exc)
                entry = {'id': oid, 'error': payload['error'], 'code': payload['code']}
                if 'insufficient' in payload:
                    entry['insufficient'] = payload['insufficient']
                failed.append(entry)
                continue
            except Exception as exc:
                failed.append({'id': oid, 'error': str(exc)})
                continue

            succeeded.append(SalesOrderReadSerializer(order).data)

        return Response({'succeeded': succeeded, 'failed': failed})
