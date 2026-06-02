from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Product, ProductCategory


class ProductImageModelTests(TestCase):
    def setUp(self):
        self.category = ProductCategory.objects.create(name='Beverages')

    def _product(self, **overrides):
        data = {
            'sku': 'SKU-IMG',
            'name': 'Water',
            'category': self.category,
            'unit_price': Decimal('1.50'),
            'is_active': True,
        }
        data.update(overrides)
        return Product(**data)

    def test_active_product_requires_image_or_external_url(self):
        product = self._product()

        with self.assertRaises(ValidationError) as ctx:
            product.full_clean(validate_unique=False)

        self.assertIn('image', ctx.exception.message_dict)

    def test_inactive_product_can_omit_image_source(self):
        product = self._product(is_active=False)

        product.full_clean(validate_unique=False)

    def test_external_url_counts_as_primary_image_source(self):
        product = self._product(external_image_url='https://cdn.example.com/water.png')

        product.full_clean(validate_unique=False)
        self.assertTrue(product.has_image_source)
        self.assertEqual(product.primary_image_url, 'https://cdn.example.com/water.png')
