"""
Untracked products in the back-office: the product form, the inventory page,
manual adjustments, the dashboard, and cancelling from the order page.
"""
from django.test import TestCase
from django.urls import reverse

from core.models import InventoryMovement, Product, Role, SalesOrder, SalesOrderItem
from api.tests.helpers import make_category, make_customer, make_product, make_user


class UntrackedBackofficeFixture(TestCase):
    def setUp(self):
        self.manager = make_user(Role.MANAGER)
        self.client.force_login(self.manager)
        self.box = make_product(sku='BOX-1', name='Box', stock=0)
        self.licence = make_product(
            sku='LIC-12M', name='Licence', stock=0,
            kind=Product.DIGITAL, track_inventory=False, low_stock_threshold=7,
        )


class ProductFormTests(UntrackedBackofficeFixture):
    def _post(self, url, **overrides):
        data = {
            'sku': 'SVC-1', 'name': 'Install', 'category': str(make_category().pk),
            'unit_of_measure': Product.PIECE, 'unit_price': '30.00',
            'external_image_url': 'https://cdn.example.com/svc.png', 'is_active': '1',
            'kind': Product.SERVICE,
        }
        data.update(overrides)
        return self.client.post(url, {k: v for k, v in data.items() if v is not None})

    def test_a_new_product_defaults_to_physical_and_tracked(self):
        response = self.client.get(reverse('product-create'))
        self.assertContains(response, 'name="track_inventory" value="1"\n                checked')
        self.assertContains(response, '<option value="physical"\n                  selected')

    def test_an_untracked_service_can_be_created_without_a_threshold(self):
        # The threshold field is disabled when tracking is off, so the browser
        # does not submit it.
        response = self._post(reverse('product-create'), low_stock_threshold=None)

        self.assertRedirects(response, reverse('inventory-list'))
        product = Product.objects.get(sku='SVC-1')
        self.assertEqual(product.kind, Product.SERVICE)
        self.assertFalse(product.track_inventory)

    def test_editing_without_the_threshold_keeps_the_stored_one(self):
        response = self._post(
            reverse('product-edit', args=[self.licence.pk]),
            sku='LIC-12M', name='Licence', kind=Product.DIGITAL, low_stock_threshold=None,
        )

        self.assertRedirects(response, reverse('inventory-list'))
        self.licence.refresh_from_db()
        self.assertEqual(self.licence.low_stock_threshold, 7)
        self.assertFalse(self.licence.track_inventory)

    def test_tracking_can_be_turned_back_on(self):
        self._post(
            reverse('product-edit', args=[self.licence.pk]),
            sku='LIC-12M', name='Licence', kind=Product.DIGITAL,
            track_inventory='1', low_stock_threshold='3',
        )
        self.licence.refresh_from_db()
        self.assertTrue(self.licence.track_inventory)
        self.assertEqual(self.licence.low_stock_threshold, 3)

    def test_an_unknown_kind_is_refused(self):
        response = self._post(reverse('product-create'), kind='ebook')
        self.assertEqual(response.status_code, 200)
        self.assertIn('kind', response.context['errors'])
        self.assertFalse(Product.objects.filter(sku='SVC-1').exists())


class InventoryPageTests(UntrackedBackofficeFixture):
    def test_an_untracked_product_shows_unlimited_and_cannot_be_adjusted(self):
        response = self.client.get(reverse('inventory-list'))

        self.assertContains(response, 'Unlimited')
        self.assertNotContains(response, f'openAdjustModal({self.licence.pk},')
        self.assertContains(response, f'openAdjustModal({self.box.pk},')
        self.assertNotContains(response, f'<option value="{self.licence.pk}">')

    def test_it_is_never_listed_as_out_of_stock(self):
        out = self.client.get(reverse('inventory-list'), {'stock': 'out'})
        ok = self.client.get(reverse('inventory-list'), {'stock': 'ok'})

        self.assertEqual([p.sku for p in out.context['products']], ['BOX-1'])
        self.assertEqual([p.sku for p in ok.context['products']], ['LIC-12M'])
        self.assertEqual(out.context['low_stock_count'], 1)

    def test_the_movement_panel_reports_no_stock(self):
        response = self.client.get(f'/inventory/products/{self.licence.pk}/movements/')
        self.assertEqual(response.json()['product']['current_stock'], None)
        self.assertFalse(response.json()['product']['track_inventory'])

    def test_a_manual_adjustment_is_refused(self):
        response = self.client.post(reverse('inventory-adjust'), {
            'product_id': self.licence.pk, 'movement_type': InventoryMovement.PURCHASE,
            'quantity': '10',
        }, follow=True)

        self.assertEqual(
            [str(m) for m in response.context['messages']],
            ['LIC-12M does not track inventory, so its stock cannot be adjusted.'],
        )
        self.assertFalse(self.licence.inventory_movements.exists())

    def test_the_dashboard_does_not_alert_on_untracked_products(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual([p.sku for p in response.context['low_stock_products']], ['BOX-1'])
        self.assertEqual(response.context['stats']['low_stock_count'], 1)


class CancelFromOrderPageTests(UntrackedBackofficeFixture):
    def test_cancelling_an_order_that_never_deducted_invents_no_stock(self):
        order = SalesOrder.objects.create(
            customer=make_customer(), status=SalesOrder.CONFIRMED, created_by=self.manager,
        )
        SalesOrderItem.objects.create(
            sales_order=order, product=self.box, quantity=4,
            unit_price=self.box.unit_price, line_total=self.box.unit_price * 4,
        )

        response = self.client.post(reverse('order-cancel', args=[order.pk]))

        self.assertRedirects(response, reverse('order-detail', args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.CANCELLED)
        self.assertFalse(InventoryMovement.objects.filter(movement_type=InventoryMovement.RETURN).exists())
        self.assertEqual(Product.objects.get(pk=self.box.pk).current_stock, 0)
