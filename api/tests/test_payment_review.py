"""
Settling payments that are pending review.

Before this, a pending_review payment could only be settled by editing it in
the Django admin, which did not move the order to Paid either. These pin the
approve and reject actions on the API and in the back-office.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Payment, Role, SalesOrder
from api.tests.helpers import (
    auth_client,
    make_kiosk_station,
    make_order,
    make_payment,
    make_user,
)


def _pending_payment(order, user, amount, **attrs):
    return make_payment(
        order=order,
        user=user,
        amount=amount,
        status=Payment.PENDING_REVIEW,
        payment_method=Payment.MOBILE_PAYMENT,
        **attrs,
    )


class PaymentReviewApiTests(APITestCase):
    def setUp(self):
        self.manager = make_user(Role.MANAGER)
        self.order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('100.00'))
        self.payment = _pending_payment(self.order, self.manager, '100.00')
        auth_client(self.client, self.manager)

    def _approve(self, payment=None, **data):
        payment = payment or self.payment
        return self.client.post(f'/api/v1/payments/{payment.pk}/approve/', data, format='json')

    def _reject(self, payment=None, **data):
        payment = payment or self.payment
        return self.client.post(f'/api/v1/payments/{payment.pk}/reject/', data, format='json')

    def test_approving_a_covering_payment_marks_the_order_paid(self):
        response = self._approve(notes='Checked against the bank statement.')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], Payment.CONFIRMED)
        self.assertEqual(response.data['reviewed_by'], self.manager.pk)
        self.assertEqual(response.data['review_notes'], 'Checked against the bank statement.')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.PAID)
        self.assertIsNotNone(self.order.paid_at)

    def test_approving_a_partial_payment_leaves_the_order_confirmed(self):
        order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('100.00'))
        partial = _pending_payment(order, self.manager, '40.00')

        self.assertEqual(self._approve(partial).status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.CONFIRMED)
        self.assertIsNone(order.paid_at)

    def test_approving_a_kiosk_payment_only_records_when_it_was_paid(self):
        # A kiosk order is DELIVERED the moment the customer leaves; with its
        # receipt still under review it has no paid_at, so revenue misses it.
        order = make_order(status=SalesOrder.DELIVERED, total=Decimal('25.00'))
        SalesOrder.objects.filter(pk=order.pk).update(paid_at=None)
        kiosk_payment = _pending_payment(order, self.manager, '25.00')

        self.assertEqual(self._approve(kiosk_payment).status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.DELIVERED)
        self.assertIsNotNone(order.paid_at)

    def test_rejecting_requires_a_reason_and_leaves_the_order_alone(self):
        missing_reason = self._reject()
        self.assertEqual(missing_reason.status_code, 400)
        self.assertIn('notes', missing_reason.data['details'])

        response = self._reject(notes='Receipt shows a different account.')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], Payment.REJECTED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.CONFIRMED)

    def test_only_a_pending_payment_can_be_reviewed(self):
        confirmed = make_payment(
            order=make_order(status=SalesOrder.CONFIRMED), user=self.manager,
        )
        for action in (self._approve, self._reject):
            with self.subTest(action=action.__name__):
                response = action(confirmed, notes='reason')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data['code'], 'payment_not_pending')

    def test_a_rejected_payment_cannot_be_approved_later(self):
        self._reject(notes='Fraudulent receipt.')
        response = self._approve()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'payment_not_pending')

    def test_approval_records_a_hand_checked_transaction_key(self):
        response = self._approve(transaction_key='BDV-998877')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['transaction_key'], 'BDV-998877')

    def test_approval_refuses_a_transaction_key_already_spent(self):
        make_payment(
            order=make_order(status=SalesOrder.CONFIRMED),
            user=self.manager,
            transaction_key='BDV-998877',
        )
        response = self._approve(transaction_key='BDV-998877')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'duplicate_transaction')
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.PENDING_REVIEW)

    def test_a_payment_for_a_cancelled_order_cannot_be_approved(self):
        SalesOrder.objects.filter(pk=self.order.pk).update(status=SalesOrder.CANCELLED)
        response = self._approve()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'order_not_payable')

    def test_staff_cannot_review_payments(self):
        auth_client(self.client, make_user(Role.STAFF))
        self.assertEqual(self._approve().status_code, 403)
        self.assertEqual(self._reject(notes='x').status_code, 403)

    def test_a_kiosk_key_cannot_review_payments(self):
        self.client.credentials()
        _station, raw_key = make_kiosk_station()
        response = self.client.post(
            f'/api/v1/payments/{self.payment.pk}/approve/',
            {}, format='json', HTTP_AUTHORIZATION=f'KioskKey {raw_key}',
        )
        self.assertEqual(response.status_code, 403)

    def test_a_payment_cannot_be_recorded_as_rejected(self):
        response = self.client.post('/api/v1/payments/', {
            'sales_order': make_order(status=SalesOrder.CONFIRMED).pk,
            'amount': '10.00',
            'payment_method': Payment.CASH,
            'status': Payment.REJECTED,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('status', response.data['details'])

    def test_rejected_payments_can_be_listed(self):
        self._reject(notes='Wrong amount.')
        response = self.client.get('/api/v1/payments/?status=rejected')
        self.assertEqual([p['id'] for p in response.data['results']], [self.payment.pk])


class PaymentReviewBackOfficeTests(TestCase):
    def setUp(self):
        self.manager = make_user(Role.MANAGER)
        self.order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('100.00'))
        self.payment = _pending_payment(self.order, self.manager, '100.00')

    def test_a_manager_approves_from_the_payment_page(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse('payment-detail', args=[self.payment.pk]))
        self.assertContains(page, reverse('payment-approve', args=[self.payment.pk]))

        response = self.client.post(reverse('payment-approve', args=[self.payment.pk]))
        self.assertRedirects(response, reverse('payment-detail', args=[self.payment.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.PAID)

    def test_a_manager_rejects_with_a_reason(self):
        self.client.force_login(self.manager)
        self.client.post(
            reverse('payment-reject', args=[self.payment.pk]),
            {'review_notes': 'Receipt is a screenshot of another sale.'},
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.REJECTED)
        self.assertEqual(self.payment.reviewed_by, self.manager)

    def test_a_rejection_without_a_reason_changes_nothing(self):
        self.client.force_login(self.manager)
        self.client.post(reverse('payment-reject', args=[self.payment.pk]), {'review_notes': '  '})
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.PENDING_REVIEW)

    def test_staff_see_no_review_controls_and_cannot_post_them(self):
        staff = make_user(Role.STAFF)
        self.client.force_login(staff)
        page = self.client.get(reverse('payment-detail', args=[self.payment.pk]))
        self.assertNotContains(page, reverse('payment-approve', args=[self.payment.pk]))

        response = self.client.post(reverse('payment-approve', args=[self.payment.pk]))
        self.assertEqual(response.status_code, 403)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.PENDING_REVIEW)
