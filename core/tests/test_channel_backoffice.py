"""
Channel orders where staff work: the order page, the admin, and the channel's
new settings.
"""
from django.test import TestCase
from django.urls import reverse

from api.tests.helpers import (
    make_category,
    make_channel,
    make_channel_order,
    make_listing,
    make_order,
    make_product,
    make_user,
    set_currency,
)
from core.models import ChannelCustomerLink, ChannelOrder, OcrCallLog, Role, SalesChannel, SalesOrder


class ChannelOrderBackofficeTests(TestCase):
    def setUp(self):
        set_currency()
        self.admin = make_user(Role.ADMIN, is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)
        self.channel, _key = make_channel(admin=self.admin)
        self.product = make_product(sku='BOX-1', stock=10, category=make_category('Software'))
        make_listing(self.product, slug='box')
        self.channel_order = make_channel_order(self.channel, self.product, reference='WEB-77')

    def _detail(self, order):
        return self.client.get(reverse('order-detail', args=[order.pk]))

    def test_the_order_page_says_where_the_order_came_from(self):
        response = self._detail(self.channel_order.sales_order)

        self.assertContains(response, 'Online Order')
        self.assertContains(response, 'Trend Store')
        self.assertContains(response, 'WEB-77')
        self.assertContains(response, 'Pickup')
        self.assertContains(response, 'Expires')
        self.assertContains(response, 'Quoted rate')
        self.assertContains(response, '50.00 VES / USD')

    def test_the_expiry_is_only_shown_while_the_order_is_unpaid(self):
        SalesOrder.objects.filter(pk=self.channel_order.sales_order_id).update(status=SalesOrder.PAID)

        self.assertNotContains(self._detail(self.channel_order.sales_order), 'Expires')

    def test_a_contact_the_store_sent_is_shown_and_escaped(self):
        ChannelOrder.objects.filter(pk=self.channel_order.pk).update(
            contact={'whatsapp': '0414-5550000', 'note': '<script>x</script>'},
        )

        response = self._detail(self.channel_order.sales_order)

        self.assertContains(response, '0414-5550000')
        self.assertContains(response, '&lt;script&gt;x&lt;/script&gt;')
        self.assertNotContains(response, '<script>x</script>')

    def test_an_ordinary_order_shows_no_online_card(self):
        ordinary = make_order(product=self.product, status=SalesOrder.CONFIRMED)

        self.assertNotContains(self._detail(ordinary), 'Online Order')

    def test_the_order_page_does_not_add_a_query_per_channel_field(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as online:
            self._detail(self.channel_order.sales_order)
        ordinary = make_order(product=self.product, status=SalesOrder.CONFIRMED)
        with CaptureQueriesContext(connection) as plain:
            self._detail(ordinary)

        self.assertLessEqual(len(online), len(plain) + 1)


class ChannelAdminTests(TestCase):
    def setUp(self):
        set_currency()
        self.admin = make_user(Role.ADMIN, is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)
        self.channel, _key = make_channel(admin=self.admin)
        product = make_product(stock=10, category=make_category('Software'))
        make_listing(product, slug='box')
        self.channel_order = make_channel_order(self.channel, product, reference='WEB-5')

    def test_channel_orders_and_links_are_view_only(self):
        link = ChannelCustomerLink.objects.get()
        for model, obj in (('channelorder', self.channel_order), ('channelcustomerlink', link)):
            changelist = self.client.get(reverse(f'admin:core_{model}_changelist'))
            self.assertEqual(changelist.status_code, 200, model)
            self.assertEqual(self.client.get(reverse(f'admin:core_{model}_add')).status_code, 403, model)
            detail = self.client.get(reverse(f'admin:core_{model}_change', args=[obj.pk]))
            self.assertEqual(detail.status_code, 200, model)
            self.assertNotContains(detail, 'name="_save"', msg_prefix=model)
            self.assertEqual(
                self.client.post(reverse(f'admin:core_{model}_delete', args=[obj.pk]), {'post': 'yes'}).status_code,
                403, model,
            )
        self.assertEqual(ChannelOrder.objects.count(), 1)

    def test_the_channel_settings_can_be_changed_in_the_admin(self):
        url = reverse('admin:core_saleschannel_change', args=[self.channel.pk])
        page = self.client.get(url)
        self.assertContains(page, 'name="trusts_email_verification"')
        self.assertContains(page, 'name="order_expiry_minutes"')

        response = self.client.post(url, {
            'name': self.channel.name, 'is_active': 'on',
            'trusts_email_verification': 'on', 'order_expiry_minutes': '30',
            'keys-TOTAL_FORMS': '0', 'keys-INITIAL_FORMS': '0',
            'keys-MIN_NUM_FORMS': '0', 'keys-MAX_NUM_FORMS': '1000',
        })

        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        self.channel.refresh_from_db()
        self.assertTrue(self.channel.trusts_email_verification)
        self.assertEqual(self.channel.order_expiry_minutes, 30)

    def test_an_expiry_shorter_than_five_minutes_is_refused(self):
        url = reverse('admin:core_saleschannel_change', args=[self.channel.pk])

        response = self.client.post(url, {
            'name': self.channel.name, 'is_active': 'on', 'order_expiry_minutes': '1',
            'keys-TOTAL_FORMS': '0', 'keys-INITIAL_FORMS': '0',
            'keys-MIN_NUM_FORMS': '0', 'keys-MAX_NUM_FORMS': '1000',
        })

        self.assertEqual(response.status_code, 200)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.order_expiry_minutes, 60)

    def test_a_new_channel_does_not_trust_emails_and_holds_stock_for_an_hour(self):
        self.assertFalse(SalesChannel.objects.get().trusts_email_verification)
        self.assertEqual(SalesChannel.objects.get().order_expiry_minutes, 60)

    def test_the_ocr_log_shows_which_channel_spent_the_call(self):
        OcrCallLog.objects.create(
            channel=self.channel, sales_order=self.channel_order.sales_order,
            status='ok', latency_ms=120, bytes_sent=2048,
        )

        response = self.client.get(reverse('admin:core_ocrcalllog_changelist'))

        self.assertContains(response, 'Trend Store')
