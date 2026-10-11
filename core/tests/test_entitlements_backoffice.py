"""
Digital delivery where staff work: the order page, the admin, and what the staff
API does not show.

What matters: a licence key is write-only for staff (never rendered, masked
afterwards), reissuing and revoking are Manager-level, and nothing outside the
storefront's one reveal ever carries a key.
"""
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from api.tests.helpers import (
    auth_client,
    make_category,
    make_customer,
    make_fulfillment,
    make_order,
    make_product,
    make_user,
    pay_order,
    set_currency,
)
from core.admin import ProductAdmin, ProductFulfillmentInline
from core.models import Entitlement, Product, Role, SalesOrder
from core.services import entitlements as service
from core.services.entitlements import supply_value

KEY = Fernet.generate_key().decode()
LICENCE = 'ABCD-1234-EFGH-5678'
LINK = 'https://downloads.example.com/setup'
MASKED = '•' * 8 + '5678'


@override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY])
class BackofficeFixture(TestCase):
    def setUp(self):
        set_currency()
        self.staff = make_user(Role.STAFF)
        self.manager = make_user(Role.MANAGER)
        self.admin = make_user(Role.ADMIN, is_staff=True, is_superuser=True)
        category = make_category('Software')
        self.keyed = make_product(
            sku='KEY-1Y', stock=0, kind=Product.DIGITAL, track_inventory=False, category=category,
        )
        make_fulfillment(self.keyed, mode='supplied', kind='key')
        self.linked = make_product(
            sku='LINK-1Y', stock=0, kind=Product.DIGITAL, track_inventory=False, category=category,
        )
        make_fulfillment(self.linked, duration_days=30)

    def paid_order(self, *products):
        order = make_order(customer=make_customer(), product=products[0], status=SalesOrder.CONFIRMED)
        for product in products[1:]:
            order.items.create(product=product, quantity=1, unit_price=product.unit_price,
                               line_total=product.unit_price)
            order.total_amount += product.unit_price
            order.save()
        pay_order(order)
        return SalesOrder.objects.get(pk=order.pk)

    def entitlement(self, order, sku):
        return Entitlement.objects.get(order_item__sales_order=order, order_item__product__sku=sku)

    def detail(self, order):
        return self.client.get(reverse('order-detail', args=[order.pk]))

    def supply(self, order, entitlement, value=LICENCE, follow=True):
        return self.client.post(
            reverse('entitlement-supply', args=[order.pk, entitlement.pk]), {'value': value}, follow=follow,
        )

    def revoke(self, order, entitlement, follow=True):
        return self.client.post(reverse('entitlement-revoke', args=[order.pk, entitlement.pk]), follow=follow)

    def messages(self, response):
        return [str(m) for m in response.context['messages']]


class OrderPageTests(BackofficeFixture):
    def test_the_page_lists_what_was_delivered_and_never_a_key(self):
        order = self.paid_order(self.keyed, self.linked)
        supply_value(self.entitlement(order, 'KEY-1Y').pk, LICENCE)
        self.client.force_login(self.manager)

        response = self.detail(order)

        self.assertContains(response, 'Deliverables')
        self.assertContains(response, 'KEY-1Y')
        self.assertContains(response, MASKED)
        self.assertContains(response, LINK)
        html = response.content.decode()
        for secret in (LICENCE, 'ABCD', '1234-EFGH'):
            self.assertNotIn(secret, html)
        self.assertNotIn(service.seal(LICENCE)[:20], html)
        self.assertNotIn('/reveal/', html)

    def test_an_order_with_nothing_delivered_shows_no_card(self):
        plain = make_order(customer=make_customer(), product=make_product(stock=5), status=SalesOrder.CONFIRMED)
        pay_order(plain)
        self.client.force_login(self.manager)

        self.assertNotContains(self.detail(plain), 'Deliverables')

    def test_the_supply_and_revoke_forms_are_not_nested_inside_the_order_form(self):
        order = self.paid_order(self.keyed)
        self.client.force_login(self.manager)

        html = self.detail(order).content.decode()

        opens = html.index('id="order-form"')
        closes = html.index('</form>', opens)
        entitlement = self.entitlement(order, 'KEY-1Y')
        for name in ('supply', 'revoke'):
            declared = html.index(f'<form id="{name}-{entitlement.pk}"')
            self.assertGreater(declared, closes, name)
        self.assertEqual(html.count('<form'), html.count('</form>'))

    def test_staff_see_when_a_key_was_revealed_but_never_the_key(self):
        order = self.paid_order(self.keyed)
        entitlement = self.entitlement(order, 'KEY-1Y')
        supply_value(entitlement.pk, LICENCE)
        service.reveal(entitlement.pk)
        self.client.force_login(self.staff)

        response = self.detail(order)

        self.assertContains(response, 'Revealed')
        self.assertNotContains(response, LICENCE)

    def test_the_query_count_does_not_grow_with_the_deliveries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.manager)
        one = self.paid_order(self.keyed)
        with CaptureQueriesContext(connection) as small:
            self.detail(one)
        many = self.paid_order(self.keyed, self.linked, make_product(sku='KEY-2', stock=0, category=self.keyed.category))
        for product in Product.objects.filter(sku='KEY-2'):
            make_fulfillment(product, mode='supplied', kind='key')
        with CaptureQueriesContext(connection) as large:
            self.detail(many)

        self.assertLessEqual(len(large), len(small) + 1)


class SupplyTests(BackofficeFixture):
    def setUp(self):
        super().setUp()
        self.order = self.paid_order(self.keyed)
        self.pending = self.entitlement(self.order, 'KEY-1Y')

    def test_staff_supply_a_pending_key_and_it_is_stored_encrypted(self):
        self.client.force_login(self.staff)

        response = self.supply(self.order, self.pending)

        self.assertEqual(self.messages(response), ['Delivery supplied.'])
        stored = Entitlement.objects.get(pk=self.pending.pk)
        self.assertEqual(stored.status, 'active')
        self.assertNotIn('ABCD', stored.value)
        self.assertEqual(service.unseal(stored.value), LICENCE)
        self.assertNotContains(response, LICENCE)
        self.assertContains(response, MASKED)

    def test_replacing_a_delivered_key_is_for_managers_not_staff(self):
        supply_value(self.pending.pk, LICENCE)
        self.client.force_login(self.staff)

        refused = self.supply(self.order, self.pending, 'ZZZZ-9999-YYYY-0000', follow=False)

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(service.unseal(Entitlement.objects.get(pk=self.pending.pk).value), LICENCE)

        self.client.force_login(self.manager)
        self.supply(self.order, self.pending, 'ZZZZ-9999-YYYY-0000')
        self.assertEqual(service.unseal(Entitlement.objects.get(pk=self.pending.pk).value), 'ZZZZ-9999-YYYY-0000')

    def test_a_bad_value_changes_nothing_and_says_why(self):
        link_product = make_product(sku='LNK-S', stock=0, kind=Product.DIGITAL, track_inventory=False,
                                    category=self.keyed.category)
        make_fulfillment(link_product, mode='supplied', kind='link')
        order = self.paid_order(link_product)
        pending = self.entitlement(order, 'LNK-S')
        self.client.force_login(self.manager)

        response = self.supply(order, pending, 'not a url')

        self.assertTrue(self.messages(response))
        self.assertNotEqual(self.messages(response), ['Delivery supplied.'])
        self.assertEqual(Entitlement.objects.get(pk=pending.pk).status, 'pending')

    def test_without_an_encryption_key_it_says_so_and_stores_nothing(self):
        self.client.force_login(self.manager)

        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[]):
            response = self.supply(self.order, self.pending)

        self.assertIn('ENTITLEMENT_ENCRYPTION_KEYS', self.messages(response)[0])
        self.assertEqual(Entitlement.objects.get(pk=self.pending.pk).status, 'pending')

    def test_a_revoked_delivery_cannot_be_supplied(self):
        service.revoke(self.pending.pk)
        self.client.force_login(self.manager)

        response = self.supply(self.order, self.pending)

        self.assertIn('revoked', self.messages(response)[0])
        self.assertEqual(Entitlement.objects.get(pk=self.pending.pk).value, '')

    def test_an_entitlement_of_another_order_is_not_found(self):
        other = self.paid_order(self.keyed)
        self.client.force_login(self.manager)

        response = self.supply(other, self.pending, follow=False)  # this order's pk, someone else's entitlement

        self.assertEqual(response.status_code, 404)

    def test_it_is_a_post_and_needs_a_login(self):
        self.client.force_login(self.manager)
        url = reverse('entitlement-supply', args=[self.order.pk, self.pending.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

        self.client.logout()
        self.assertEqual(self.client.post(url, {'value': LICENCE}).status_code, 302)
        self.assertEqual(Entitlement.objects.get(pk=self.pending.pk).status, 'pending')


class RevokeTests(BackofficeFixture):
    def setUp(self):
        super().setUp()
        self.order = self.paid_order(self.linked)
        self.entitlement_ = self.entitlement(self.order, 'LINK-1Y')

    def test_a_manager_revokes_and_the_card_stops_offering_changes(self):
        self.client.force_login(self.manager)

        response = self.revoke(self.order, self.entitlement_)

        self.assertEqual(self.messages(response), ['Delivery revoked.'])
        self.assertEqual(Entitlement.objects.get(pk=self.entitlement_.pk).status, 'revoked')
        self.assertNotContains(response, f'id="supply-{self.entitlement_.pk}"')
        self.assertNotContains(response, f'id="revoke-{self.entitlement_.pk}"')

    def test_staff_cannot_revoke(self):
        self.client.force_login(self.staff)

        response = self.revoke(self.order, self.entitlement_, follow=False)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Entitlement.objects.get(pk=self.entitlement_.pk).status, 'active')

    def test_revoking_twice_says_so(self):
        self.client.force_login(self.manager)
        self.revoke(self.order, self.entitlement_)

        response = self.revoke(self.order, self.entitlement_)

        self.assertEqual(self.messages(response), ['This delivery was already revoked.'])

    def test_staff_get_no_revoke_button(self):
        self.client.force_login(self.staff)

        self.assertNotContains(self.detail(self.order), f'id="revoke-{self.entitlement_.pk}"')
        self.assertNotContains(self.detail(self.order), 'Revoke')


class AdminTests(BackofficeFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.order = self.paid_order(self.keyed)
        self.pending = self.entitlement(self.order, 'KEY-1Y')
        supply_value(self.pending.pk, LICENCE)
        self.pending.refresh_from_db()

    def test_delivery_is_configured_on_the_product_not_on_its_form(self):
        self.assertIn(ProductFulfillmentInline, ProductAdmin.inlines)

        response = self.client.get(reverse('admin:core_product_change', args=[self.keyed.pk]))

        self.assertContains(response, 'fulfillment-TOTAL_FORMS')
        self.assertContains(response, 'fulfillment-0-entitlement_kind')

    def test_the_entitlement_admin_never_shows_a_value_and_is_view_only(self):
        listing = self.client.get(reverse('admin:core_entitlement_changelist'))
        detail = self.client.get(reverse('admin:core_entitlement_change', args=[self.pending.pk]))

        for response in (listing, detail):
            self.assertEqual(response.status_code, 200)
            html = response.content.decode()
            for secret in (LICENCE, 'ABCD', self.pending.value):
                self.assertNotIn(secret, html)
            self.assertIn(MASKED, html)
        self.assertNotContains(detail, 'name="value"')
        self.assertNotContains(detail, 'name="_save"')
        self.assertEqual(self.client.get(reverse('admin:core_entitlement_add')).status_code, 403)
        self.assertEqual(
            self.client.post(reverse('admin:core_entitlement_delete', args=[self.pending.pk]), {'post': 'yes'}).status_code,
            403,
        )
        self.assertTrue(Entitlement.objects.filter(pk=self.pending.pk).exists())


class StaffApiTests(BackofficeFixture):
    def test_the_staff_api_carries_no_deliveries_and_no_key(self):
        order = self.paid_order(self.keyed, self.linked)
        supply_value(self.entitlement(order, 'KEY-1Y').pk, LICENCE)
        client = APIClient()
        auth_client(client, self.manager)

        responses = [
            client.get(f'/api/v1/orders/{order.pk}/'),
            client.get('/api/v1/orders/'),
            client.get(f'/api/v1/products/{self.keyed.pk}/'),
            client.get('/api/v1/products/'),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            body = response.content.decode()
            self.assertNotIn('entitlement', body.lower())
            for secret in (LICENCE, 'ABCD', LINK):
                self.assertNotIn(secret, body)
