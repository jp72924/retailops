import shutil

from django.conf import settings
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import InventoryMovement, Product, Role, SalesOrder
from api.tests.helpers import (
    auth_client,
    make_category,
    make_customer,
    make_order,
    make_product,
    make_user,
    png_upload,
)


class CustomerCategoryProductAPITests(APITestCase):
    def setUp(self):
        safe_id = self.id().replace('.', '_').replace(':', '_')
        self.media_root = settings.BASE_DIR / 'test_media' / safe_id
        shutil.rmtree(self.media_root, ignore_errors=True)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))

        self.manager = make_user(Role.MANAGER, email='manager-catalog@example.com')
        self.staff = make_user(Role.STAFF, email='staff-catalog@example.com')
        self.category = make_category('Beverages')

    def test_customer_crud_validation_filters_and_protected_delete(self):
        auth_client(self.client, self.staff)
        create = self.client.post('/api/v1/customers/', {
            'first_name': 'Ana',
            'last_name': 'Lopez',
            'email': 'ana@example.com',
            'national_id': 'V11111111',
            'city': 'Caracas',
        }, format='json')
        self.assertEqual(create.status_code, status.HTTP_201_CREATED)

        duplicate_email = self.client.post('/api/v1/customers/', {
            'first_name': 'Other',
            'last_name': 'Person',
            'email': 'ana@example.com',
            'national_id': 'V22222222',
        }, format='json')
        self.assertEqual(duplicate_email.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', duplicate_email.data['details'])

        search = self.client.get('/api/v1/customers/?search=Ana&ordering=-created_at&page_size=1')
        self.assertEqual(search.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(search.data['count'], 1)
        self.assertEqual(len(search.data['results']), 1)

        customer = make_customer(email='protected@example.com')
        make_order(customer=customer, user=self.staff, status=SalesOrder.DRAFT)
        delete = self.client.delete(f'/api/v1/customers/{customer.pk}/')
        self.assertEqual(delete.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(delete.data['code'], 'conflict')

    def test_category_crud_validation_search_and_permissions(self):
        auth_client(self.client, self.staff)
        forbidden = self.client.post('/api/v1/categories/', {'name': 'Staff Category'}, format='json')
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)

        auth_client(self.client, self.manager)
        parent = self.client.post('/api/v1/categories/', {
            'name': 'Parent',
            'description': 'Searchable parent',
        }, format='json')
        self.assertEqual(parent.status_code, status.HTTP_201_CREATED)

        child = self.client.post('/api/v1/categories/', {
            'name': 'Child',
            'parent_category': parent.data['id'],
        }, format='json')
        self.assertEqual(child.status_code, status.HTTP_201_CREATED)
        self.assertIn('Parent', child.data['display_name'])

        self_parent = self.client.patch(
            f"/api/v1/categories/{child.data['id']}/",
            {'parent_category': child.data['id']},
            format='json',
        )
        self.assertEqual(self_parent.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('parent_category', self_parent.data['details'])

        search = self.client.get('/api/v1/categories/?search=parent&ordering=name')
        self.assertEqual(search.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(search.data['count'], 1)

    def test_product_crud_validation_images_filters_and_movements(self):
        auth_client(self.client, self.manager)
        no_image = self.client.post('/api/v1/products/', {
            'sku': 'NOIMG',
            'name': 'No Image',
            'category_id': self.category.pk,
            'unit_price': '3.00',
            'low_stock_threshold': 2,
            'is_active': True,
        }, format='json')
        self.assertEqual(no_image.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('image', no_image.data['details'])

        created = self.client.post('/api/v1/products/', {
            'sku': 'URLSKU',
            'name': 'URL Product',
            'category_id': self.category.pk,
            'unit_price': '3.00',
            'low_stock_threshold': 2,
            'external_image_url': 'https://cdn.example.com/urlsku.png',
            'is_active': True,
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertTrue(created.data['has_image'])

        duplicate = self.client.post('/api/v1/products/', {
            'sku': 'URLSKU',
            'name': 'Duplicate SKU',
            'category_id': self.category.pk,
            'unit_price': '3.00',
            'low_stock_threshold': 2,
            'external_image_url': 'https://cdn.example.com/dup.png',
        }, format='json')
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('sku', duplicate.data['details'])

        invalid_price = self.client.post('/api/v1/products/', {
            'sku': 'BADPRICE',
            'name': 'Bad Price',
            'category_id': self.category.pk,
            'unit_price': '0.00',
            'low_stock_threshold': -1,
            'external_image_url': 'https://cdn.example.com/bad.png',
        }, format='json')
        self.assertEqual(invalid_price.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('unit_price', invalid_price.data['details'])
        self.assertIn('low_stock_threshold', invalid_price.data['details'])

        upload = self.client.post('/api/v1/products/', {
            'sku': 'UPLOADSKU',
            'name': 'Upload Product',
            'category_id': self.category.pk,
            'unit_price': '5.00',
            'low_stock_threshold': 1,
            'image': png_upload(),
        }, format='multipart')
        self.assertEqual(upload.status_code, status.HTTP_201_CREATED)
        self.assertIn('/media/products/', upload.data['primary_image_url'])

        product = Product.objects.get(sku='URLSKU')
        InventoryMovement.objects.create(
            product=product,
            movement_type=InventoryMovement.PURCHASE,
            quantity=4,
            reference_type=InventoryMovement.MANUAL_ADJUSTMENT,
            reference_id=0,
            created_by=self.manager,
        )
        list_response = self.client.get('/api/v1/products/?search=URL&stock=ok&ordering=sku&page_size=1')
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data['count'], 1)
        self.assertEqual(list_response.data['results'][0]['sku'], 'URLSKU')

        movements = self.client.get(f'/api/v1/products/{product.pk}/movements/')
        self.assertEqual(movements.status_code, status.HTTP_200_OK)
        self.assertEqual(movements.data['count'], 1)

        missing_movements = self.client.get('/api/v1/products/999999/movements/')
        self.assertEqual(missing_movements.status_code, status.HTTP_404_NOT_FOUND)


class InventoryAPITests(APITestCase):
    def setUp(self):
        self.manager = make_user(Role.MANAGER, email='manager-inventory@example.com')
        self.staff = make_user(Role.STAFF, email='staff-inventory@example.com')
        self.product = make_product(sku='INV-001', stock=5)

    def test_adjust_inventory_success_validation_and_permissions(self):
        auth_client(self.client, self.staff)
        forbidden = self.client.post('/api/v1/inventory/adjust/', {
            'product_id': self.product.pk,
            'quantity': 1,
        }, format='json')
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)

        auth_client(self.client, self.manager)
        positive = self.client.post('/api/v1/inventory/adjust/', {
            'product_id': self.product.pk,
            'quantity': 3,
            'notes': 'Restock',
        }, format='json')
        self.assertEqual(positive.status_code, status.HTTP_201_CREATED)
        self.assertEqual(positive.data['quantity'], 3)

        negative = self.client.post('/api/v1/inventory/adjust/', {
            'product_id': self.product.pk,
            'quantity': -2,
        }, format='json')
        self.assertEqual(negative.status_code, status.HTTP_201_CREATED)

        zero = self.client.post('/api/v1/inventory/adjust/', {
            'product_id': self.product.pk,
            'quantity': 0,
        }, format='json')
        self.assertEqual(zero.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('quantity', zero.data['details'])

        invalid_product = self.client.post('/api/v1/inventory/adjust/', {
            'product_id': 999999,
            'quantity': 1,
        }, format='json')
        self.assertEqual(invalid_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('product_id', invalid_product.data['details'])

    def test_bulk_adjust_filters_and_read_only_collection(self):
        auth_client(self.client, self.manager)
        other = make_product(sku='INV-002', stock=0)
        response = self.client.post('/api/v1/inventory/bulk-adjust/', {
            'adjustments': [
                {'product_id': self.product.pk, 'quantity': 5, 'notes': 'Restock'},
                {'product_id': other.pk, 'quantity': 0},
                {'product_id': 999999, 'quantity': 1},
                {'product_id': 'bad', 'quantity': 1},
            ],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['succeeded']), 1)
        self.assertEqual(len(response.data['failed']), 3)

        empty = self.client.post('/api/v1/inventory/bulk-adjust/', {'adjustments': []}, format='json')
        self.assertEqual(empty.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(empty.data['code'], 'invalid_request')

        filtered = self.client.get(f'/api/v1/inventory/?product={self.product.pk}&movement_type=adjustment')
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(filtered.data['count'], 1)

        disallowed = self.client.post('/api/v1/inventory/', {
            'product_id': self.product.pk,
            'quantity': 1,
        }, format='json')
        self.assertEqual(disallowed.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
