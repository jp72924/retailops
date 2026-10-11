"""
A store cancelling an order it no longer wants.

What matters: an unpaid order gives its stock back and stays cancelled, doing
it twice is safe, an order with any payment (even a rejected one) or that is no
longer confirmed can only be closed by a person, a store can only cancel its
own orders, and the client_reference stays used.
"""
from rest_framework import status

from api.tests.helpers import channel_auth, make_channel, make_payment, pay_order
from api.tests.test_storefront_orders import ORDERS, OrderFixture
from api.storefront.throttling import StorefrontOrderThrottle
from api.storefront.views import OrderCancelView
from core.models import InventoryMovement, Payment, SalesOrder


class CancelFixture(OrderFixture):
    def placed(self, **overrides):
        response = self.place(**overrides)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        return response.data['order_number']

    def cancel(self, order_number, auth=None, **params):
        return self.client.post(
            f'{ORDERS}{order_number}/cancel/', params, **(auth or self.auth),
        )

    def sales_order(self, order_number):
        return SalesOrder.objects.get(order_number=order_number)


class CancelTests(CancelFixture):
    def test_cancelling_an_unpaid_order_gives_its_stock_back(self):
        order_number = self.placed()
        self.assertEqual(self.stock(self.shelf), 9)

        response = self.cancel(order_number)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data['customer_status'], 'cancelled')
        self.assertEqual(self.sales_order(order_number).status, SalesOrder.CANCELLED)
        self.assertEqual(self.stock(self.shelf), 10)

    def test_the_order_records_that_the_channel_cancelled_it(self):
        order_number = self.placed()

        self.cancel(order_number)

        self.assertIn('Cancelled by the channel', self.sales_order(order_number).notes)

    def test_cancelling_twice_is_safe_and_returns_the_stock_once(self):
        order_number = self.placed()

        first = self.cancel(order_number)
        second = self.cancel(order_number)

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(second.data['customer_status'], 'cancelled')
        self.assertEqual(self.stock(self.shelf), 10)
        returns = InventoryMovement.objects.filter(
            movement_type=InventoryMovement.RETURN, reference_id=self.sales_order(order_number).pk,
        )
        self.assertEqual(returns.count(), 1)

    def test_an_order_of_products_that_track_no_stock_cancels_without_a_return(self):
        order_number = self.placed(items=[{'sku': 'LIC-12M', 'quantity': 1}])

        response = self.cancel(order_number)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(InventoryMovement.objects.filter(movement_type=InventoryMovement.RETURN).exists())

    def test_the_client_reference_stays_used_after_a_cancel(self):
        order_number = self.placed()
        self.cancel(order_number)

        again = self.place()

        self.assertEqual(again.status_code, status.HTTP_200_OK)  # the same, cancelled, order
        self.assertEqual(again.data['order_number'], order_number)
        self.assertEqual(again.data['customer_status'], 'cancelled')
        self.assertEqual(self.stock(self.shelf), 10)

    def test_a_new_attempt_with_a_new_reference_reserves_stock_again(self):
        self.cancel(self.placed())

        fresh = self.place(client_reference='WEB-1002')

        self.assertEqual(fresh.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.stock(self.shelf), 9)


class WhatBlocksACancelTests(CancelFixture):
    def test_an_order_waiting_for_review_is_left_to_a_person(self):
        order_number = self.placed()
        make_payment(self.sales_order(order_number), status=Payment.PENDING_REVIEW, payment_method=Payment.MOBILE_PAYMENT)

        response = self.cancel(order_number)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'order_not_cancellable')
        self.assertEqual(response.data['customer_status'], 'payment_under_review')
        self.assertEqual(self.sales_order(order_number).status, SalesOrder.CONFIRMED)
        self.assertEqual(self.stock(self.shelf), 9)

    def test_even_a_rejected_payment_means_a_person_decides(self):
        order_number = self.placed()
        make_payment(self.sales_order(order_number), status=Payment.REJECTED, payment_method=Payment.MOBILE_PAYMENT)

        response = self.cancel(order_number)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['customer_status'], 'awaiting_payment')
        self.assertEqual(self.stock(self.shelf), 9)

    def test_a_paid_order_cannot_be_cancelled_by_the_store(self):
        order_number = self.placed()
        pay_order(self.sales_order(order_number))

        response = self.cancel(order_number)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'order_not_cancellable')
        self.assertEqual(response.data['customer_status'], 'processing')
        self.assertEqual(self.sales_order(order_number).status, SalesOrder.PAID)

    def test_an_order_that_moved_on_is_not_cancelled(self):
        order_number = self.placed()
        order = self.sales_order(order_number)
        order.status = SalesOrder.SHIPPED
        order.save(update_fields=['status'])

        response = self.cancel(order_number)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(self.sales_order(order_number).status, SalesOrder.SHIPPED)

    def test_a_refunded_order_is_not_cancellable(self):
        order_number = self.placed()
        order = self.sales_order(order_number)
        order.status = SalesOrder.REFUNDED
        order.save(update_fields=['status'])

        response = self.cancel(order_number)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['customer_status'], 'refunded')


class WhoMayCancelTests(CancelFixture):
    def test_another_channels_order_is_a_404_and_stays_untouched(self):
        order_number = self.placed()
        _other, other_key = make_channel('other-shop', admin=self.admin)

        response = self.cancel(order_number, auth=channel_auth(other_key))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.sales_order(order_number).status, SalesOrder.CONFIRMED)

    def test_an_unknown_order_is_a_404(self):
        self.assertEqual(self.cancel('SO-NOPE').status_code, status.HTTP_404_NOT_FOUND)

    def test_it_needs_a_channel_key(self):
        order_number = self.placed()

        self.assertEqual(self.client.post(f'{ORDERS}{order_number}/cancel/').status_code, 401)
        self.assertEqual(self.sales_order(order_number).status, SalesOrder.CONFIRMED)

    def test_cancelling_is_counted_with_order_creation_not_with_reads(self):
        self.assertEqual(OrderCancelView.throttle_classes, [StorefrontOrderThrottle])

    def test_the_responses_are_never_cached(self):
        order_number = self.placed()

        response = self.cancel(order_number)

        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('Authorization', response['Vary'])
