from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.models import (
    Customer,
    Product,
    ProductCategory,
    Role,
    SalesOrder,
    SalesOrderItem,
    User,
)


class OrderProductThumbnailRenderTests(TestCase):
    def setUp(self):
        role = Role.objects.create(name=Role.MANAGER)
        self.user = User.objects.create_user(
            email='manager@example.com',
            password='ManagerPass123!',
            first_name='Maya',
            last_name='Manager',
            role=role,
        )
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(
            first_name='Juan',
            last_name='Perez',
            email='juan@example.com',
        )
        self.category = ProductCategory.objects.create(name='Beverages')
        self.product = Product.objects.create(
            sku='SKU-IMG',
            name='Image Product',
            category=self.category,
            unit_price=Decimal('3.50'),
            external_image_url='https://cdn.example.com/products/image-product.png',
        )

    def _order(self, status=SalesOrder.DRAFT):
        order = SalesOrder.objects.create(
            customer=self.customer,
            status=status,
            subtotal=Decimal('3.50'),
            total_amount=Decimal('3.50'),
            created_by=self.user,
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            product=self.product,
            quantity=1,
            unit_price=self.product.unit_price,
            line_total=self.product.unit_price,
        )
        return order

    def test_editable_order_line_item_renders_thumbnail_and_select_metadata(self):
        order = self._order(SalesOrder.DRAFT)

        response = self.client.get(reverse('order-detail', args=[order.pk]))

        self.assertContains(response, 'id="line-thumb-1"')
        self.assertContains(response, 'product-thumb product-thumb-sm')
        self.assertContains(response, self.product.external_image_url)
        self.assertContains(response, f'data-image-url="{self.product.external_image_url}"')

    def test_read_only_order_line_item_renders_thumbnail_without_select(self):
        order = self._order(SalesOrder.CONFIRMED)

        response = self.client.get(reverse('order-detail', args=[order.pk]))

        self.assertContains(response, 'id="line-thumb-1"')
        self.assertContains(response, self.product.external_image_url)
        self.assertNotContains(response, f'name="product_1"')
