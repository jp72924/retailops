"""
Regression tests for the /api/v1/orders/ list endpoint's query behaviour.

Background: the list endpoint cost a fixed ~230ms per request because
_amount_paid was annotated with Sum() over a reverse relation, which forces a
GROUP BY.  DRF pagination calls .count() on that queryset on every request, and
Django cannot count a grouped query without wrapping it in a subquery and
materialising every group.  A second, larger cost came from the nested
ProductSerializer falling back to Product.current_stock -- a plain @property
that fires one aggregate per call, called three times per product.

These tests pin the shape of the queries rather than their wall-clock cost, so
they stay meaningful on any hardware.
"""
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from core.models import Payment, Role, SalesOrder
from api.tests.helpers import (
    auth_client,
    make_category,
    make_customer,
    make_order,
    make_payment,
    make_product,
    make_user,
)


class OrderListQueryShapeTests(APITestCase):
    """The list endpoint must not scale its query count with page size."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user(Role.MANAGER)
        category = make_category('Perf')
        cls.customer = make_customer()
        # Two distinct products so each order carries more than one line item;
        # a single-item order would hide a per-item N+1.
        cls.products = [
            make_product(sku=f'PERF-{i}', stock=500, unit_price='10.00', category=category)
            for i in range(2)
        ]
        # 30 orders > the 25 default page size, so page_size=1 and page_size=25
        # both return a full page.
        for _ in range(30):
            order = make_order(
                customer=cls.customer,
                product=cls.products[0],
                user=cls.user,
                status=SalesOrder.CONFIRMED,
            )
            order.items.create(
                product=cls.products[1],
                quantity=2,
                unit_price=Decimal('10.00'),
                line_total=Decimal('20.00'),
            )

    def setUp(self):
        auth_client(self.client, self.user)

    def _capture(self, url):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return list(ctx.captured_queries)

    def test_query_count_does_not_scale_with_page_size(self):
        """
        Serializing 25 orders must cost the same number of queries as
        serializing 1.  Before the fix this differed by ~200 queries: the
        nested ProductSerializer called Product.current_stock (an aggregate)
        three times for every line item on the page.
        """
        one = self._capture('/api/v1/orders/?page_size=1')
        many = self._capture('/api/v1/orders/?page_size=25')

        self.assertEqual(
            len(one),
            len(many),
            'Query count scales with page size -- an N+1 has been reintroduced.\n'
            f'page_size=1 -> {len(one)} queries, page_size=25 -> {len(many)}.\n'
            + '\n'.join(q['sql'][:160] for q in many),
        )

    def test_pagination_count_is_a_plain_count(self):
        """
        The pagination COUNT must hit core_salesorder directly -- no join to
        core_payment and no GROUP BY.  Reintroducing an aggregate annotation on
        the orders queryset would break this.
        """
        queries = self._capture('/api/v1/orders/?page_size=25')
        counts = [q['sql'] for q in queries if q['sql'].upper().lstrip().startswith('SELECT COUNT(')]

        self.assertEqual(len(counts), 1, f'expected exactly one COUNT query, got {counts}')
        sql = counts[0]
        self.assertNotIn('GROUP BY', sql.upper(), f'pagination COUNT is grouped: {sql}')
        self.assertNotIn('core_payment', sql, f'pagination COUNT joins payments: {sql}')


class OrderAmountPaidTests(APITestCase):
    """
    _amount_paid switched from Sum() to a correlated Subquery.  Nothing in the
    suite asserted its value, so these tests pin the arithmetic the rewrite
    must preserve.
    """

    def setUp(self):
        self.user = make_user(Role.MANAGER)
        auth_client(self.client, self.user)

    def _amount_paid(self, order):
        response = self.client.get(f'/api/v1/orders/{order.pk}/')
        self.assertEqual(response.status_code, 200)
        return Decimal(str(response.data['amount_paid']))

    def test_counts_only_confirmed_payments(self):
        order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('100.00'))
        make_payment(order=order, user=self.user, amount='30.00', status=Payment.CONFIRMED)
        make_payment(order=order, user=self.user, amount='25.00', status=Payment.CONFIRMED)
        make_payment(
            order=order,
            user=self.user,
            amount='40.00',
            status=Payment.PENDING_REVIEW,
            reference_number='ref-pending',
        )

        self.assertEqual(self._amount_paid(order), Decimal('55.00'))

    def test_order_without_payments_reports_zero(self):
        order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('100.00'))
        self.assertEqual(self._amount_paid(order), Decimal('0'))

    def test_amount_paid_is_isolated_per_order(self):
        """A correlated subquery must not leak another order's payments."""
        paid = make_order(status=SalesOrder.CONFIRMED, total=Decimal('100.00'))
        unpaid = make_order(status=SalesOrder.CONFIRMED, total=Decimal('100.00'))
        make_payment(order=paid, user=self.user, amount='75.00', status=Payment.CONFIRMED)

        self.assertEqual(self._amount_paid(paid), Decimal('75.00'))
        self.assertEqual(self._amount_paid(unpaid), Decimal('0'))

    def test_annotated_list_matches_model_property(self):
        """The annotation and the SalesOrder.amount_paid fallback must agree."""
        order = make_order(status=SalesOrder.CONFIRMED, total=Decimal('100.00'))
        make_payment(order=order, user=self.user, amount='60.00', status=Payment.CONFIRMED)

        response = self.client.get(f'/api/v1/orders/?page_size=100')
        self.assertEqual(response.status_code, 200)
        row = next(o for o in response.data['results'] if o['id'] == order.pk)

        self.assertEqual(Decimal(str(row['amount_paid'])), order.amount_paid)
        self.assertEqual(Decimal(str(row['amount_outstanding'])), Decimal('40.00'))
