"""
Letting unpaid channel orders lapse.

An unpaid online order holds its stock until it expires. The command releases
it, but only while nothing has been paid: an order with any payment is a human's
to settle, and a payment that lands while the command runs must win.
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from api.tests.helpers import (
    make_category,
    make_channel,
    make_channel_order,
    make_customer,
    make_listing,
    make_order,
    make_product,
    make_user,
    set_currency,
)
from core.models import InventoryMovement, Payment, Product, Role, SalesOrder
from core.services import channel_orders as channel_order_service
from core.services.channel_orders import expire_channel_orders, lapsed_channel_orders
from core.services.payments import approve_payment


class ExpiryFixture(TestCase):
    def setUp(self):
        set_currency()
        self.admin = make_user(Role.ADMIN, is_staff=True)
        self.channel, _key = make_channel(admin=self.admin)
        category = make_category('Software')
        self.shelf = make_product(sku='BOX-1', stock=10, category=category)
        self.licence = make_product(
            sku='LIC-12M', stock=0, kind=Product.DIGITAL, track_inventory=False, category=category,
        )
        make_listing(self.shelf, slug='box')
        make_listing(self.licence, slug='licence')

    def stock(self):
        return Product.objects.get(pk=self.shelf.pk).current_stock

    def lapsed(self, reference='WEB-1', **kwargs):
        return make_channel_order(self.channel, self.shelf, reference=reference, minutes_old=1, **kwargs)

    def status_of(self, channel_order):
        return SalesOrder.objects.get(pk=channel_order.sales_order_id).status

    def pay(self, channel_order, state=Payment.PENDING_REVIEW):
        return Payment.objects.create(
            sales_order=channel_order.sales_order, amount=channel_order.sales_order.total_amount,
            payment_method=Payment.MOBILE_PAYMENT, status=state, reference_number='ref',
            recorded_by=self.channel.service_user, currency_snapshot=channel_order.currency_snapshot,
        )


class ExpireOrdersTests(ExpiryFixture):
    def test_a_lapsed_unpaid_order_is_cancelled_and_its_stock_returns(self):
        order = self.lapsed(quantity=3)
        self.assertEqual(self.stock(), 7)

        result = expire_channel_orders()

        self.assertEqual((result.expired, result.skipped), (1, 0))
        self.assertEqual(self.status_of(order), SalesOrder.CANCELLED)
        self.assertEqual(self.stock(), 10)
        returns = InventoryMovement.objects.filter(movement_type=InventoryMovement.RETURN)
        self.assertEqual(list(returns.values_list('quantity', flat=True)), [3])
        self.assertEqual(returns.get().created_by, self.channel.service_user)
        self.assertIn('Expired unpaid', SalesOrder.objects.get(pk=order.sales_order_id).notes)

    def test_an_order_not_yet_due_is_left_alone(self):
        order = make_channel_order(self.channel, self.shelf)

        result = expire_channel_orders()

        self.assertEqual(result.expired, 0)
        self.assertEqual(self.status_of(order), SalesOrder.CONFIRMED)
        self.assertEqual(self.stock(), 9)

    def test_it_is_idempotent(self):
        self.lapsed()

        first = expire_channel_orders()
        second = expire_channel_orders()

        self.assertEqual((first.expired, second.expired), (1, 0))
        self.assertEqual(InventoryMovement.objects.filter(movement_type=InventoryMovement.RETURN).count(), 1)
        self.assertEqual(self.stock(), 10)

    def test_an_order_of_untracked_products_expires_without_touching_stock(self):
        order = make_channel_order(self.channel, self.licence, reference='D-1', minutes_old=1)

        expire_channel_orders()

        self.assertEqual(self.status_of(order), SalesOrder.CANCELLED)
        self.assertFalse(InventoryMovement.objects.filter(movement_type=InventoryMovement.RETURN).exists())

    def test_an_order_with_any_payment_is_never_expired(self):
        pending = self.lapsed('A')
        rejected = self.lapsed('B')
        self.pay(pending)
        self.pay(rejected, state=Payment.REJECTED)

        result = expire_channel_orders()

        # Not candidates at all, so not even counted as skipped, and a run's
        # limit is never spent on them.
        self.assertEqual((result.expired, result.skipped), (0, 0))
        self.assertEqual(lapsed_channel_orders(timezone.now()).count(), 0)
        self.assertEqual(self.status_of(pending), SalesOrder.CONFIRMED)
        self.assertEqual(self.status_of(rejected), SalesOrder.CONFIRMED)
        self.assertEqual(self.stock(), 8)

    def test_a_paid_order_is_no_candidate(self):
        order = self.lapsed()
        payment = self.pay(order)
        approve_payment(payment, user=self.admin, transaction_key='bank-key')

        self.assertEqual(self.status_of(order), SalesOrder.PAID)
        self.assertEqual(expire_channel_orders().expired, 0)
        self.assertEqual(self.status_of(order), SalesOrder.PAID)

    def test_a_payment_that_lands_while_it_runs_wins(self):
        order = self.lapsed()
        real_lock = channel_order_service.lock_order

        def pay_then_lock(order_id):
            self.pay(order)  # arrives after the candidates were listed
            return real_lock(order_id)

        with patch.object(channel_order_service, 'lock_order', side_effect=pay_then_lock):
            result = expire_channel_orders()

        self.assertEqual((result.expired, result.skipped), (0, 1))
        self.assertEqual(self.status_of(order), SalesOrder.CONFIRMED)
        self.assertEqual(self.stock(), 9)

    def test_it_only_touches_channel_orders(self):
        elsewhere = make_order(
            customer=make_customer(), product=self.shelf, status=SalesOrder.CONFIRMED,
        )

        expire_channel_orders(now=timezone.now() + timedelta(days=30))

        self.assertEqual(SalesOrder.objects.get(pk=elsewhere.pk).status, SalesOrder.CONFIRMED)

    def test_a_run_is_limited_and_the_rest_waits_for_the_next(self):
        for index in range(3):
            self.lapsed(f'WEB-{index}')

        first = expire_channel_orders(limit=2)
        second = expire_channel_orders(limit=2)

        self.assertEqual((first.expired, second.expired), (2, 1))
        self.assertEqual(self.stock(), 10)

    def test_the_oldest_lapse_first(self):
        newer = make_channel_order(self.channel, self.shelf, reference='NEW', minutes_old=1)
        older = make_channel_order(self.channel, self.shelf, reference='OLD', minutes_old=60)

        expire_channel_orders(limit=1)

        self.assertEqual(self.status_of(older), SalesOrder.CANCELLED)
        self.assertEqual(self.status_of(newer), SalesOrder.CONFIRMED)

    def test_an_order_cancelled_by_hand_is_not_cancelled_twice(self):
        order = self.lapsed()
        SalesOrder.objects.filter(pk=order.sales_order_id).update(status=SalesOrder.CANCELLED)

        self.assertEqual(lapsed_channel_orders(timezone.now()).count(), 0)
        self.assertEqual(expire_channel_orders().expired, 0)


class ExpireCommandTests(ExpiryFixture):
    def _run(self, *args):
        out = StringIO()
        call_command('expire_channel_orders', *args, stdout=out)
        return out.getvalue()

    def test_it_reports_what_it_cancelled(self):
        self.lapsed('A')
        self.lapsed('B')

        output = self._run()

        self.assertIn('Cancelled 2 lapsed order(s); skipped 0.', output)
        self.assertEqual(self.stock(), 10)

    def test_a_dry_run_changes_nothing(self):
        order = self.lapsed()

        output = self._run('--dry-run')

        self.assertIn('1 order(s) would be cancelled.', output)
        self.assertEqual(self.status_of(order), SalesOrder.CONFIRMED)

    def test_a_dry_run_counts_no_more_than_the_limit(self):
        for index in range(3):
            self.lapsed(f'WEB-{index}')

        self.assertIn('2 order(s) would be cancelled.', self._run('--dry-run', '--limit', '2'))

    def test_with_nothing_due_it_says_so(self):
        self.assertIn('Cancelled 0 lapsed order(s); skipped 0.', self._run())
