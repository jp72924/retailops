from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Role, SalesOrder
from api.tests.helpers import auth_client, make_order, make_product, make_user


class AuthAndPublicEndpointTests(APITestCase):
    def setUp(self):
        self.password = 'ManagerPass123!'
        self.user = make_user(Role.MANAGER, email='manager-auth@example.com', password=self.password)

    def test_token_obtain_success_and_me(self):
        response = self.client.post('/api/v1/auth/token/', {
            'email': self.user.email,
            'password': self.password,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('token', response.data)
        self.assertEqual(response.data['role_name'], Role.MANAGER)

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")
        me = self.client.get('/api/v1/auth/me/')
        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.data['email'], self.user.email)
        self.assertEqual(me.data['role_name'], Role.MANAGER)

    def test_token_obtain_rejects_invalid_inactive_and_missing_fields(self):
        invalid = self.client.post('/api/v1/auth/token/', {
            'email': self.user.email,
            'password': 'wrong',
        }, format='json')
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid.data['code'], 'validation_error')

        inactive = make_user(Role.STAFF, email='inactive@example.com', password='Inactive123!')
        inactive.is_active = False
        inactive.save()
        inactive_response = self.client.post('/api/v1/auth/token/', {
            'email': inactive.email,
            'password': 'Inactive123!',
        }, format='json')
        self.assertEqual(inactive_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(inactive_response.data['code'], 'validation_error')

        missing = self.client.post('/api/v1/auth/token/', {'email': self.user.email}, format='json')
        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', missing.data['details'])

    def test_revoke_token_and_invalid_token_errors(self):
        token = auth_client(self.client, self.user)
        response = self.client.post('/api/v1/auth/token/revoke/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        after_revoke = self.client.get('/api/v1/auth/me/')
        self.assertEqual(after_revoke.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(after_revoke.data['code'], 'authentication_failed')

        self.client.credentials(HTTP_AUTHORIZATION='Token definitely-invalid')
        invalid = self.client.get('/api/v1/auth/me/')
        self.assertEqual(invalid.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(invalid.data['code'], 'authentication_failed')

    def test_password_reset_request_and_confirm_validation(self):
        request = self.client.post('/api/v1/auth/password-reset/', {
            'email': self.user.email,
        }, format='json')
        self.assertEqual(request.status_code, status.HTTP_200_OK)

        unknown = self.client.post('/api/v1/auth/password-reset/', {
            'email': 'nobody@example.com',
        }, format='json')
        self.assertEqual(unknown.status_code, status.HTTP_200_OK)

        invalid = self.client.post('/api/v1/auth/password-reset/confirm/', {
            'uid': 'bad',
            'token': 'bad',
            'new_password': 'short',
        }, format='json')
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid.data['code'], 'validation_error')

        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        confirm = self.client.post('/api/v1/auth/password-reset/confirm/', {
            'uid': uid,
            'token': token,
            'new_password': 'NewPassword123!',
        }, format='json')
        self.assertEqual(confirm.status_code, status.HTTP_200_OK)

    def test_public_schema_and_skill_endpoints_are_available(self):
        for url in (
            '/api/v1/mcp-skill/',
            '/api/v1/schema/',
            '/api/v1/schema/swagger/',
            '/api/v1/schema/redoc/',
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_200_OK)


class RolePermissionMatrixTests(APITestCase):
    def setUp(self):
        self.staff = make_user(Role.STAFF, email='staff-perms@example.com')
        self.manager = make_user(Role.MANAGER, email='manager-perms@example.com')
        self.admin = make_user(Role.ADMIN, email='admin-perms@example.com', is_staff=True)
        self.no_role = make_user(None, email='norole-perms@example.com')
        self.product = make_product(stock=10)
        self.pending_order = make_order(product=self.product, user=self.staff, status=SalesOrder.PENDING)

    def test_private_endpoints_require_authentication(self):
        for url in ('/api/v1/products/', '/api/v1/orders/', '/api/v1/payments/', '/api/v1/settings/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_staff_can_create_and_submit_order_but_cannot_manager_actions(self):
        auth_client(self.client, self.staff)
        create = self.client.post('/api/v1/orders/', {
            'customer_id': self.pending_order.customer_id,
            'items': [{'product_id': self.product.pk, 'quantity': 1}],
        }, format='json')
        self.assertEqual(create.status_code, status.HTTP_201_CREATED)

        submit = self.client.post(f"/api/v1/orders/{create.data['id']}/submit/")
        self.assertEqual(submit.status_code, status.HTTP_200_OK)

        confirm = self.client.post(f'/api/v1/orders/{self.pending_order.pk}/confirm/')
        self.assertEqual(confirm.status_code, status.HTTP_403_FORBIDDEN)

        settings = self.client.patch('/api/v1/settings/', {'currency_symbol': '$'}, format='json')
        self.assertEqual(settings.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_can_confirm_and_adjust_inventory_but_not_admin_only(self):
        auth_client(self.client, self.manager)
        confirm = self.client.post(f'/api/v1/orders/{self.pending_order.pk}/confirm/')
        self.assertEqual(confirm.status_code, status.HTTP_200_OK)

        adjust = self.client.post('/api/v1/inventory/adjust/', {
            'product_id': self.product.pk,
            'quantity': 2,
        }, format='json')
        self.assertEqual(adjust.status_code, status.HTTP_201_CREATED)

        users = self.client.get('/api/v1/users/')
        self.assertEqual(users.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_access_roles_and_user_management(self):
        auth_client(self.client, self.admin)
        roles = self.client.get('/api/v1/roles/')
        self.assertEqual(roles.status_code, status.HTTP_200_OK)

        users = self.client.get('/api/v1/users/')
        self.assertEqual(users.status_code, status.HTTP_200_OK)

    def test_authenticated_user_without_role_is_forbidden_from_role_gated_actions(self):
        auth_client(self.client, self.no_role)
        response = self.client.post('/api/v1/inventory/adjust/', {
            'product_id': self.product.pk,
            'quantity': 1,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
