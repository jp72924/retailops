"""
What recording a payment does to its order.

Pins core.services.payments as the one place an order becomes paid, and the
defects it closed: the back-office form paying a cancelled order back to life,
an extra payment re-stamping paid_at (moving revenue between months), and the
API locking payment rows instead of the order.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import Payment, Role, SalesOrder
from core.services import payments as payment_service
from api.tests.helpers import auth_client, make_order, make_payment, make_user


class BackOfficePaymentTests(TestCase):
    def setUp(self):
        self.user = make_user(Role.STAFF)
        self.client.force_login(self.user)

    def _record(self, order, amount):
        return self.client.post(reverse('payment-create'), {
            'sales_order': order.pk,
            'amount': amount,
            'payment_method': Payment.CASH,
        })

    def test_a_covering_payment_still_marks_a_confirmed_order_paid(self):
        order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('50.00'))
        self._record(order, '50.00')
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.PAID)
        self.assertIsNotNone(order.paid_at)

    def test_a_cancelled_order_cannot_be_paid_back_to_life(self):
        # A later refund would then have restored its stock a second time.
        order = make_order(status=SalesOrder.CANCELLED, total=Decimal('50.00'))

        response = self._record(order, '50.00')

        self.assertRedirects(response, reverse('order-detail', args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.CANCELLED)
        self.assertFalse(order.payments.exists())

    def test_an_extra_payment_does_not_move_when_the_order_was_paid(self):
        order = make_order(status=SalesOrder.PAID, total=Decimal('50.00'))
        last_month = timezone.now() - timedelta(days=35)
        SalesOrder.objects.filter(pk=order.pk).update(paid_at=last_month)

        self._record(order, '5.00')

        order.refresh_from_db()
        self.assertEqual(order.payments.count(), 1)
        self.assertEqual(order.paid_at, last_month)

    def test_a_partial_payment_leaves_the_order_confirmed(self):
        order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('50.00'))
        self._record(order, '20.00')
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.CONFIRMED)


class ApiPaymentTests(APITestCase):
    def setUp(self):
        self.user = make_user(Role.STAFF)
        auth_client(self.client, self.user)

    def test_the_order_is_rechecked_under_the_lock(self):
        # The serializer validates the order before the lock is taken. If the
        # order is cancelled in between, the payment must not be written.
        order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('50.00'))
        real_lock = payment_service.lock_payable_order

        def cancel_then_lock(order_id, allowed):
            SalesOrder.objects.filter(pk=order_id).update(status=SalesOrder.CANCELLED)
            return real_lock(order_id, allowed)

        with patch('api.views.payment.lock_payable_order', side_effect=cancel_then_lock):
            response = self.client.post('/api/v1/payments/', {
                'sales_order': order.pk, 'amount': '50.00', 'payment_method': Payment.CASH,
            }, format='json')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'order_not_payable')
        self.assertFalse(Payment.objects.filter(sales_order=order).exists())


class OrderLockTests(TestCase):
    """
    Two partial payments landing together must see each other. That holds
    only if every path locks the order row before reading the paid total.
    SQLite has no SELECT FOR UPDATE, so this pins the mechanism rather than
    racing two connections.
    """

    def test_applying_payments_locks_the_order_row(self):
        order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('50.00'))
        make_payment(order=order, amount='50.00')
        with patch.object(payment_service, 'lock_order', wraps=payment_service.lock_order) as lock:
            payment_service.apply_confirmed_payments(order)
        lock.assert_called_once_with(order.pk)

    def test_recording_through_the_api_locks_the_order_first(self):
        user = make_user(Role.STAFF)
        order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('50.00'))
        calls = []

        def spy(order_id, allowed):
            calls.append(('lock', Payment.objects.filter(sales_order_id=order_id).count()))
            return payment_service.lock_order(order_id)

        client = APIClient()
        auth_client(client, user)
        with patch('api.views.payment.lock_payable_order', side_effect=spy):
            client.post('/api/v1/payments/', {
                'sales_order': order.pk, 'amount': '20.00', 'payment_method': Payment.CASH,
            }, format='json')

        # Locked once, before the payment row existed.
        self.assertEqual(calls, [('lock', 0)])
