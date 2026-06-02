import hashlib
import os
import shutil
from decimal import Decimal
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import (
    KioskStation,
    Product,
    ProductCategory,
    Role,
    User,
)


def product_image_file(name='product.png'):
    buffer = BytesIO()
    Image.new('RGB', (8, 8), color='red').save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


class ProductImageAPITests(APITestCase):
    def setUp(self):
        safe_id = self.id().replace('.', '_').replace(':', '_')
        self.media_root = settings.BASE_DIR / 'test_media' / safe_id
        shutil.rmtree(self.media_root, ignore_errors=True)
        os.makedirs(self.media_root, exist_ok=True)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))

        manager_role = Role.objects.create(name=Role.MANAGER)
        self.user = User.objects.create_user(
            email='manager@example.com',
            password='ManagerPass123!',
            first_name='Maya',
            last_name='Manager',
            role=manager_role,
        )
        self.client.force_authenticate(self.user)
        self.category = ProductCategory.objects.create(name='Beverages')
        self.url = reverse('api:product-list')

    def test_active_product_requires_image_source(self):
        response = self.client.post(self.url, {
            'sku': 'SKU-NO-IMAGE',
            'name': 'No Image Product',
            'category_id': self.category.pk,
            'unit_of_measure': Product.PIECE,
            'unit_price': '3.50',
            'low_stock_threshold': 5,
            'is_active': True,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('image', response.data['details'])

    def test_active_product_can_use_external_image_url(self):
        response = self.client.post(self.url, {
            'sku': 'SKU-URL',
            'name': 'URL Product',
            'category_id': self.category.pk,
            'unit_of_measure': Product.PIECE,
            'unit_price': '3.50',
            'low_stock_threshold': 5,
            'external_image_url': 'https://cdn.example.com/products/url-product.png',
            'is_active': True,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['has_image'])
        self.assertEqual(
            response.data['primary_image_url'],
            'https://cdn.example.com/products/url-product.png',
        )

    def test_multipart_upload_exposes_absolute_primary_image_url(self):
        response = self.client.post(self.url, {
            'sku': 'SKU-UPLOAD',
            'name': 'Uploaded Product',
            'category_id': self.category.pk,
            'unit_of_measure': Product.PIECE,
            'unit_price': '4.25',
            'low_stock_threshold': 5,
            'image': product_image_file(),
            'is_active': True,
        }, format='multipart')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['has_image'])
        self.assertIn('/media/products/', response.data['primary_image_url'])
        self.assertTrue(response.data['primary_image_url'].startswith('http://testserver/'))

    def test_inactive_product_can_be_created_without_image(self):
        response = self.client.post(self.url, {
            'sku': 'SKU-INACTIVE',
            'name': 'Inactive Product',
            'category_id': self.category.pk,
            'unit_of_measure': Product.PIECE,
            'unit_price': '1.00',
            'low_stock_threshold': 5,
            'is_active': False,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data['has_image'])
        self.assertEqual(response.data['primary_image_url'], '')


class KioskProductImageAPITests(APITestCase):
    def setUp(self):
        admin_role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        kiosk_role, _ = Role.objects.get_or_create(name=Role.KIOSK)
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='AdminPass123!',
            first_name='Ada',
            last_name='Admin',
            role=admin_role,
            is_staff=True,
        )
        self.service_user = User.objects.create_user(
            email='kiosk@example.com',
            password=None,
            first_name='Kiosk',
            last_name='Station',
            role=kiosk_role,
        )
        self.raw_key = 'testkey-product-image'
        KioskStation.objects.create(
            store_identifier='MAIN',
            station_number=1,
            label='Front',
            api_key_prefix=self.raw_key[:8],
            api_key_hash=hashlib.sha256(self.raw_key.encode()).hexdigest(),
            service_user=self.service_user,
            created_by=self.admin,
        )
        category = ProductCategory.objects.create(name='Beverages')
        self.product = Product.objects.create(
            sku='SKU-KIOSK',
            name='Kiosk Water',
            category=category,
            unit_price=Decimal('2.00'),
            external_image_url='https://cdn.example.com/products/kiosk-water.png',
        )

    def test_product_search_returns_image_url_for_kiosk(self):
        response = self.client.get(
            '/api/v1/kiosk/products/?search=water',
            HTTP_AUTHORIZATION=f'KioskKey {self.raw_key}',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(
            response.data['results'][0]['image_url'],
            'https://cdn.example.com/products/kiosk-water.png',
        )
