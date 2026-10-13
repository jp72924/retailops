"""
Kiosk and Channel accounts are service accounts, not people.

They are created by provisioning, have no usable password and authenticate with
a key in their own namespace. What matters: user management can neither create,
change nor reach one, the token endpoint and the staff API refuse one even if it
somehow holds a password or a token, and people are unaffected.
"""
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from api.tests.helpers import auth_client, make_channel, make_role, make_user
from core.models import Role

User = get_user_model()

USERS = '/api/v1/users/'
TOKEN = '/api/v1/auth/token/'
ME = '/api/v1/auth/me/'
PRODUCTS = '/api/v1/products/'


class ServiceUserFixture(APITestCase):
    def setUp(self):
        self.admin = make_user(Role.ADMIN, is_staff=True)
        auth_client(self.client, self.admin)
        self.kiosk_role = make_role(Role.KIOSK)
        self.channel_role = make_role(Role.CHANNEL)
        self.staff_role = make_role(Role.STAFF)
        # What provisioning leaves behind: no usable password.
        self.channel, _key = make_channel('shop', admin=self.admin)
        self.channel_user = self.channel.service_user
        self.kiosk_user = User.objects.create_user(
            email='kiosk-1@example.com', password=None, first_name='Kiosk', last_name='One',
            role=self.kiosk_role,
        )


class CreatingAndAssigningTests(ServiceUserFixture):
    def invite(self, role, email='new@example.com'):
        return self.client.post(USERS, {
            'email': email, 'first_name': 'New', 'last_name': 'Person',
            'password': 'Pass12345!', 'role': role.pk,
        }, format='json')

    def test_a_person_can_still_be_invited(self):
        response = self.invite(self.staff_role)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

    def test_the_kiosk_and_channel_roles_cannot_be_given_to_a_new_user(self):
        for role in (self.kiosk_role, self.channel_role):
            response = self.invite(role, email=f'{role.name.lower()}@example.com')

            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, role.name)
            self.assertFalse(User.objects.filter(email=f'{role.name.lower()}@example.com').exists())

    def test_a_person_cannot_be_turned_into_a_service_account(self):
        person = make_user(Role.STAFF, email='person@example.com')

        response = self.client.patch(f'{USERS}{person.pk}/', {'role': self.channel_role.pk}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        person.refresh_from_db()
        self.assertEqual(person.role_name, Role.STAFF)

    def test_a_person_can_still_change_role_between_person_roles(self):
        person = make_user(Role.STAFF, email='person@example.com')

        response = self.client.patch(f'{USERS}{person.pk}/', {'role': make_role(Role.MANAGER).pk}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        person.refresh_from_db()
        self.assertEqual(person.role_name, Role.MANAGER)


class ManagingTests(ServiceUserFixture):
    def test_a_service_account_can_be_listed_and_read(self):
        listed = self.client.get(USERS, {'page_size': 100}).data['results']
        emails = {row['email'] for row in listed}

        self.assertIn(self.channel_user.email, emails)
        self.assertIn(self.kiosk_user.email, emails)
        self.assertEqual(self.client.get(f'{USERS}{self.kiosk_user.pk}/').status_code, status.HTTP_200_OK)

    def test_a_service_account_cannot_be_updated(self):
        for user in (self.channel_user, self.kiosk_user):
            response = self.client.patch(
                f'{USERS}{user.pk}/', {'first_name': 'Changed', 'role': self.staff_role.pk}, format='json',
            )

            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, user.email)
            self.assertEqual(response.data['code'], 'service_user')
            user.refresh_from_db()
            self.assertNotEqual(user.first_name, 'Changed')
            self.assertTrue(user.is_service)

    def test_a_service_account_cannot_be_given_a_password(self):
        for user in (self.channel_user, self.kiosk_user):
            response = self.client.post(
                f'{USERS}{user.pk}/change-password/',
                {'new_password': 'Brand-new-1!', 'confirm_password': 'Brand-new-1!'}, format='json',
            )

            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, user.email)
            user.refresh_from_db()
            self.assertFalse(user.has_usable_password())

    def test_a_service_account_is_not_activated_or_deactivated_through_user_management(self):
        for user in (self.channel_user, self.kiosk_user):
            before = user.is_active
            deactivate = self.client.post(f'{USERS}{user.pk}/deactivate/')
            reactivate = self.client.post(f'{USERS}{user.pk}/reactivate/')

            self.assertEqual((deactivate.status_code, reactivate.status_code), (409, 409))
            user.refresh_from_db()
            self.assertEqual(user.is_active, before)

    def test_a_person_is_managed_as_before(self):
        person = make_user(Role.STAFF, email='person@example.com')

        update = self.client.patch(f'{USERS}{person.pk}/', {'first_name': 'Renamed'}, format='json')
        password = self.client.post(
            f'{USERS}{person.pk}/change-password/',
            {'new_password': 'Brand-new-1!', 'confirm_password': 'Brand-new-1!'}, format='json',
        )
        deactivate = self.client.post(f'{USERS}{person.pk}/deactivate/')
        reactivate = self.client.post(f'{USERS}{person.pk}/reactivate/')

        self.assertEqual(
            [update.status_code, password.status_code, deactivate.status_code, reactivate.status_code],
            [200, 200, 200, 200],
        )
        person.refresh_from_db()
        self.assertEqual((person.first_name, person.is_active), ('Renamed', True))
        self.assertTrue(person.check_password('Brand-new-1!'))


class SigningInTests(ServiceUserFixture):
    def setUp(self):
        super().setUp()
        self.anonymous = APIClient()
        # The dangerous case: a service account that somehow has a password.
        self.channel_user.set_password('Sneaky-pass-1!')
        self.channel_user.save()

    def token(self, email, password):
        return self.anonymous.post(TOKEN, {'email': email, 'password': password}, format='json')

    def test_a_service_account_with_a_password_gets_no_token(self):
        response = self.token(self.channel_user.email, 'Sneaky-pass-1!')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Token.objects.filter(user=self.channel_user).exists())

    def test_the_refusal_reads_like_a_wrong_password(self):
        refused = self.token(self.channel_user.email, 'Sneaky-pass-1!')
        wrong = self.token(self.admin.email, 'not-the-password')

        self.assertEqual(refused.status_code, wrong.status_code)
        self.assertEqual(refused.data, wrong.data)

    def test_a_person_still_gets_a_token(self):
        response = self.token(self.admin.email, 'Pass12345!')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('token', response.data)


class TheStaffApiTests(ServiceUserFixture):
    """Even a token a service account was somehow given is not accepted by the staff API."""

    def call(self, user, path):
        token, _ = Token.objects.get_or_create(user=user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        return client.get(path)

    def test_a_token_held_by_a_service_account_is_refused_everywhere_in_the_staff_api(self):
        for user in (self.channel_user, self.kiosk_user):
            for path in (ME, PRODUCTS):
                response = self.call(user, path)

                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, (user.email, path))

    def test_a_persons_token_works(self):
        for path in (ME, PRODUCTS):
            self.assertEqual(self.call(self.admin, path).status_code, status.HTTP_200_OK, path)

    def test_a_deactivated_persons_token_is_still_refused(self):
        person = make_user(Role.STAFF, email='gone@example.com')
        token, _ = Token.objects.get_or_create(user=person)
        person.is_active = False
        person.save()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        self.assertEqual(client.get(ME).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_an_unknown_token_is_refused(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token nope')

        self.assertEqual(client.get(ME).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticating_costs_one_query_for_the_token_user_and_role(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        token, _ = Token.objects.get_or_create(user=self.admin)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        with CaptureQueriesContext(connection) as queries:
            client.get(ME)

        lookups = [q['sql'] for q in queries if 'authtoken_token' in q['sql']]
        self.assertEqual(len(lookups), 1)
        self.assertIn('core_role', lookups[0])  # the role rides along in the same query
