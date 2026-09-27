"""
Recorded payments keep their currency and rate across every surface.

The requirement, stated as tests: change the symbol, the decimals or the
exchange rate, and nothing already paid moves — not in the API, not in the
back-office, not on a kiosk receipt. Unpaid balances, by contrast, follow the
live rate. Also pins the query shape of every list that now reads payments.
"""
import base64
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import CurrencySnapshot, Payment, Role, SalesOrder, SystemSettings
from core.services.currency import RATE_SOURCE_BACKFILLED, CurrencyContext
from api.tests.helpers import (
    auth_client,
    make_customer,
    make_kiosk_station,
    make_order,
    make_payment,
    make_product,
    make_user,
    set_currency,
    vepay_payload,
)


def _confirmed_order(total='100.00', user=None):
    return make_order(status=SalesOrder.CONFIRMED, total=Decimal(total), user=user)


# ── REST API ──────────────────────────────────────────────────────────────────

class PaymentCurrencyApiTests(APITestCase):
    def setUp(self):
        set_currency()
        self.user = make_user(Role.MANAGER)
        auth_client(self.client, self.user)
        self.order = _confirmed_order(total='10.00', user=self.user)

    def test_recording_a_payment_returns_its_currency_and_rate(self):
        response = self.client.post('/api/v1/payments/', {
            'sales_order': self.order.pk,
            'amount': '10.00',
            'payment_method': Payment.CASH,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['amount_secondary'], '500.00')
        currency = response.data['currency']
        self.assertEqual(
            (currency['code'], currency['symbol'], currency['decimal_places']),
            ('USD', '$', 2),
        )
        self.assertEqual(currency['secondary']['code'], 'VES')
        self.assertEqual(currency['secondary']['rate'], '50.00000000')

    def test_a_recorded_payment_reads_the_same_after_every_setting_changes(self):
        payment = make_payment(order=self.order, user=self.user, amount='10.00')

        set_currency(
            currency_symbol='US$',
            secondary_currency_symbol='VES',
            secondary_decimal_places=0,
            secondary_exchange_rate=Decimal('80'),
        )
        response = self.client.get(f'/api/v1/payments/{payment.pk}/')

        self.assertEqual(response.data['amount_secondary'], '500.00')
        self.assertEqual(response.data['currency']['symbol'], '$')
        self.assertEqual(response.data['currency']['secondary']['symbol'], 'Bs.')
        self.assertEqual(response.data['currency']['secondary']['rate'], '50.00000000')

    def test_a_backfilled_payment_says_its_rate_is_approximate(self):
        snapshot = CurrencySnapshot.objects.intern(CurrencyContext.build(
            currency_code='USD', currency_symbol='$', decimal_places=2,
            secondary_currency_enabled=True, secondary_currency_code='VES',
            secondary_currency_symbol='Bs.', secondary_decimal_places=2,
            secondary_exchange_rate=Decimal('36.5'), rate_as_of=None,
            rate_source=RATE_SOURCE_BACKFILLED,
        ))
        payment = make_payment(order=self.order, user=self.user, currency_snapshot=snapshot)

        response = self.client.get(f'/api/v1/payments/{payment.pk}/')
        self.assertEqual(response.data['currency']['secondary']['rate_source'], 'backfilled')
        self.assertEqual(response.data['amount_secondary'], '365.00')


class OrderCurrencyApiTests(APITestCase):
    def setUp(self):
        set_currency()
        self.user = make_user(Role.MANAGER)
        auth_client(self.client, self.user)
        self.order = _confirmed_order(user=self.user)

    def _get(self):
        response = self.client.get(f'/api/v1/orders/{self.order.pk}/')
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_an_unpaid_order_follows_the_live_rate(self):
        data = self._get()
        self.assertEqual(data['currency']['basis'], 'live')
        self.assertEqual(data['secondary']['total_amount'], '5000.00')

        set_currency(secondary_exchange_rate=Decimal('60'))
        data = self._get()
        self.assertEqual(data['secondary']['total_amount'], '6000.00')
        self.assertEqual(data['secondary']['live_rate'], '60.00000000')

    def test_the_paid_portion_stays_while_the_balance_moves(self):
        make_payment(order=self.order, user=self.user, amount='40.00')
        set_currency(secondary_exchange_rate=Decimal('60'))

        secondary = self._get()['secondary']
        self.assertEqual(secondary['amount_paid'], '2000.00')
        self.assertEqual(secondary['amount_outstanding'], '3600.00')
        self.assertEqual(secondary['total_amount'], '5600.00')

    def test_a_paid_order_is_frozen(self):
        make_payment(order=self.order, user=self.user, amount='100.00')
        set_currency(secondary_exchange_rate=Decimal('80'))

        data = self._get()
        self.assertEqual(data['currency']['basis'], 'recorded')
        self.assertEqual(data['secondary']['total_amount'], '5000.00')
        self.assertEqual(data['secondary']['amount_outstanding'], '0.00')

    def test_no_secondary_currency_means_no_secondary_block(self):
        set_currency(secondary_currency_enabled=False)
        self.assertIsNone(self._get()['secondary'])


class SettingsApiTests(APITestCase):
    def setUp(self):
        set_currency()
        auth_client(self.client, make_user(Role.MANAGER))

    def test_the_primary_currency_code_is_locked_once_orders_exist(self):
        make_order()
        response = self.client.patch('/api/v1/settings/', {'currency_code': 'EUR'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('currency_code', response.data['details'])
        self.assertEqual(SystemSettings.get().currency_code, 'USD')

    def test_the_code_can_change_on_an_empty_database(self):
        response = self.client.patch('/api/v1/settings/', {'currency_code': 'EUR'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_a_manual_rate_is_reported_as_manual(self):
        response = self.client.patch(
            '/api/v1/settings/',
            {'secondary_exchange_rate': '61.5', 'secondary_rate_source': 'fetched'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        # secondary_rate_source is read-only: a client cannot claim provenance.
        self.assertEqual(response.data['secondary_rate_source'], 'manual')
        self.assertIsNotNone(response.data['secondary_rate_updated_at'])


# ── Kiosk ─────────────────────────────────────────────────────────────────────

class KioskCurrencyTests(APITestCase):
    def setUp(self):
        self.station, self.raw_key = make_kiosk_station()
        self.customer = make_customer()
        self.product = make_product(sku='SKU-KIOSK', unit_price='10.00', stock=20)
        settings = set_currency()
        settings.ocr_enabled = True
        settings.ocr_base_url = 'https://vepay.test'
        settings.ocr_api_key = 'test-key'
        settings.ocr_enabled_methods = [Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER]
        settings.save()

    def _checkout(self, payment_method=Payment.MOBILE_PAYMENT, receipt=None):
        return self.client.post('/api/v1/kiosk/checkout/', {
            'customer_id': self.customer.pk,
            'items': [{'sku': self.product.sku, 'quantity': 1}],
            'payment_method': payment_method,
            'payment_reference': 'REF123',
            **({'receipt': receipt} if receipt is not None else {}),
        }, format='json', HTTP_AUTHORIZATION=f'KioskKey {self.raw_key}')

    def _receipt(self):
        image = base64.b64encode(b'test receipt image bytes').decode('ascii')
        return {
            'origin_bank': 'BDV',
            'origin_phone': '04121234567',
            'reference': 'REF123',
            'paid_on': '2026-05-03',
            'amount_usd': '10.00',
            'receipt_image_base64': f'data:image/png;base64,{image}',
            'receipt_image_content_type': 'image/png',
        }

    def _ocr_returning(self, payload, *, rate_during_ocr=None):
        """
        Stand in for the VEPay round trip. Optionally change the live rate
        while "OCR is running", as a BCV refresh landing mid-request would.
        """
        def fake_async_to_sync(_fn):
            def run(*args, **kwargs):
                if rate_during_ocr is not None:
                    settings = SystemSettings.get()
                    settings.secondary_exchange_rate = rate_during_ocr
                    settings.save()
                return payload
            return run
        return patch('api.kiosk.views.async_to_sync', fake_async_to_sync)

    def test_the_rate_that_validated_the_receipt_is_the_rate_recorded(self):
        # 500 VES is 10.00 USD at the rate in effect when checkout began (50),
        # but 8.33 at the rate that lands during OCR (60). Validating against
        # one and recording the other would be the bug.
        with self._ocr_returning(vepay_payload(amount='500.00', reference='REF123'),
                                 rate_during_ocr=Decimal('60')):
            response = self._checkout(receipt=self._receipt())

        self.assertEqual(response.status_code, 201, response.data)
        payment = Payment.objects.select_related('currency_snapshot').get()
        self.assertEqual(payment.currency_snapshot.secondary_exchange_rate, Decimal('50'))
        self.assertEqual(SystemSettings.get().secondary_exchange_rate, Decimal('60'))
        receipt = response.data['receipt']
        self.assertEqual(receipt['amount_secondary'], '500.00')
        self.assertEqual(receipt['currency']['secondary']['rate'], '50.00000000')

    def test_a_rejected_receipt_leaves_no_snapshot_behind(self):
        with self._ocr_returning(vepay_payload(amount='400.00', reference='REF123')):
            response = self._checkout(receipt=self._receipt())

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data['code'], 'receipt_field_mismatch')
        self.assertFalse(CurrencySnapshot.objects.exists())

    def test_the_receipt_endpoint_reports_the_recorded_currency(self):
        response = self._checkout(payment_method=Payment.CARD)
        self.assertEqual(response.status_code, 201, response.data)

        set_currency(secondary_exchange_rate=Decimal('90'))
        receipt = self.client.get(
            f"/api/v1/kiosk/receipt/{response.data['order_id']}/",
            HTTP_AUTHORIZATION=f'KioskKey {self.raw_key}',
        ).data
        self.assertEqual(receipt['currency']['secondary']['rate'], '50.00000000')
        self.assertEqual(receipt['amount_secondary'], '500.00')


# ── Back-office ───────────────────────────────────────────────────────────────

class BackOfficeRenderingTests(TestCase):
    def setUp(self):
        set_currency()
        self.user = make_user(Role.MANAGER)
        self.client.force_login(self.user)

    def test_payment_detail_keeps_the_recorded_amount_and_rate(self):
        payment = make_payment(order=_confirmed_order(total='10.00'), amount='10.00')
        set_currency(secondary_exchange_rate=Decimal('80'))

        response = self.client.get(reverse('payment-detail', args=[payment.pk]))

        self.assertContains(response, 'Bs.500.00')
        self.assertNotContains(response, 'Bs.800.00')
        self.assertContains(response, '$1 = Bs.50')

    def test_payment_detail_shows_what_the_receipt_says_was_sent(self):
        payment = make_payment(
            order=_confirmed_order(total='10.00'),
            amount='10.00',
            ocr_receipt_data=vepay_payload(amount='501.30', currency='VES'),
        )
        response = self.client.get(reverse('payment-detail', args=[payment.pk]))
        self.assertContains(response, 'Bs.501.30')

    def test_a_paid_order_keeps_its_total(self):
        order = _confirmed_order()
        make_payment(order=order, amount='100.00')
        set_currency(secondary_exchange_rate=Decimal('80'))

        response = self.client.get(reverse('order-detail', args=[order.pk]))
        self.assertContains(response, 'Bs.5,000.00')
        self.assertNotContains(response, 'Bs.8,000.00')

    def test_an_outstanding_balance_follows_the_live_rate(self):
        order = _confirmed_order()
        make_payment(order=order, amount='40.00')
        set_currency(secondary_exchange_rate=Decimal('60'))

        response = self.client.get(reverse('order-detail', args=[order.pk]))
        self.assertContains(response, 'Bs.3,600.00')  # outstanding 60 at 60
        self.assertContains(response, 'Bs.5,600.00')  # 2,000 paid + 3,600 due

    def test_the_revenue_card_does_not_restate_history_at_todays_rate(self):
        order = make_order(status=SalesOrder.PAID, total=Decimal('100.00'))
        make_payment(order=order, amount='100.00')

        content = self.client.get(reverse('dashboard')).content.decode()
        self.assertRegex(content, r'<div class="stat-value">\$100\.00</div>')

    def test_the_dashboard_still_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_a_blocked_currency_change_is_explained_on_the_form(self):
        admin = make_user(Role.ADMIN)
        self.client.force_login(admin)
        make_order()

        response = self.client.post(reverse('settings'), {
            'timezone': 'UTC',
            'language': 'en',
            'currency_code': 'EUR',
            'currency_symbol': '€',
            'decimal_places': '2',
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn('currency_code', response.context['errors'])
        self.assertContains(response, 'cannot be changed once orders or payments exist')
        self.assertEqual(SystemSettings.get().currency_code, 'USD')


class AdminTests(TestCase):
    def setUp(self):
        set_currency()
        self.client.force_login(make_user(Role.ADMIN, is_staff=True, is_superuser=True))

    def test_the_payment_form_cannot_re_point_a_payment(self):
        payment = make_payment()
        response = self.client.get(f'/admin/core/payment/{payment.pk}/change/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="currency_snapshot"')
        self.assertContains(response, 'Recorded currency')

    def test_snapshots_are_view_only(self):
        payment = make_payment()
        base = '/admin/core/currencysnapshot/'
        self.assertEqual(self.client.get(base).status_code, 200)
        self.assertEqual(self.client.get(f'{base}add/').status_code, 403)
        self.assertEqual(
            self.client.get(f'{base}{payment.currency_snapshot_id}/delete/').status_code, 403,
        )


# ── Query shape ───────────────────────────────────────────────────────────────

def _query_count(client, url):
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200, response.status_code
    return len(ctx.captured_queries)


class ApiQueryShapeTests(APITestCase):
    """Reading payments to price orders must not reintroduce an N+1."""

    @classmethod
    def setUpTestData(cls):
        set_currency()
        cls.user = make_user(Role.MANAGER)
        product = make_product(stock=500)
        for index in range(30):
            # Mix drafts (live basis) with paid orders (recorded basis), and
            # change the rate as we go so the payments use several snapshots.
            if index % 3 == 0:
                make_order(product=product, user=cls.user)
                continue
            set_currency(secondary_exchange_rate=Decimal(50 + index))
            order = make_order(
                status=SalesOrder.CONFIRMED, product=product, user=cls.user,
                total=Decimal('10.00'),
            )
            make_payment(order=order, user=cls.user, amount='4.00')
            make_payment(order=order, user=cls.user, amount='6.00')

    def setUp(self):
        auth_client(self.client, self.user)

    def test_orders_list_query_count_does_not_scale_with_page_size(self):
        self.assertEqual(
            _query_count(self.client, '/api/v1/orders/?page_size=1'),
            _query_count(self.client, '/api/v1/orders/?page_size=25'),
        )

    def test_payments_list_query_count_does_not_scale_with_page_size(self):
        self.assertEqual(
            _query_count(self.client, '/api/v1/payments/?page_size=1'),
            _query_count(self.client, '/api/v1/payments/?page_size=25'),
        )


class BackOfficeQueryShapeTests(TestCase):
    def setUp(self):
        set_currency()
        self.user = make_user(Role.MANAGER)
        self.client.force_login(self.user)
        self.product = make_product(stock=500)

    def _add_paid_orders(self, count):
        for _ in range(count):
            order = make_order(
                status=SalesOrder.CONFIRMED, product=self.product, user=self.user,
                total=Decimal('10.00'),
            )
            make_payment(order=order, user=self.user, amount='10.00')

    def _currency_queries(self):
        # Scoped to the tables this feature reads. The page has an unrelated,
        # pre-existing per-row query ({{ order.items.count }}) that a total
        # count would trip over.
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('order-list'))
        self.assertEqual(response.status_code, 200)
        tables = ('core_payment', 'core_currencysnapshot', 'core_systemsettings')
        return [q['sql'] for q in ctx.captured_queries if any(t in q['sql'] for t in tables)]

    def test_pricing_the_order_list_does_not_scale_with_rows(self):
        self._add_paid_orders(1)
        one = self._currency_queries()
        self._add_paid_orders(14)
        many = self._currency_queries()
        self.assertEqual(len(one), len(many), '\n'.join(q[:160] for q in many))
