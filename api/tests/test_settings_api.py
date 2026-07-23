from rest_framework.test import APITestCase

from core.models import Payment, Role, SystemSettings
from .helpers import auth_client, make_user
from .test_recipient_profile_crud import make_profile


URL = '/api/v1/settings/'


class SystemSettingsRecipientValidationAPITests(APITestCase):
    def test_enabling_without_an_active_profile_returns_400(self):
        auth_client(self.client, make_user(Role.MANAGER))

        resp = self.client.patch(URL, {'recipient_validation_enabled': True}, format='json')

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(SystemSettings.get().recipient_validation_enabled)

    def test_enabling_with_an_active_profile_succeeds(self):
        make_profile(payment_method=Payment.MOBILE_PAYMENT)
        auth_client(self.client, make_user(Role.MANAGER))

        resp = self.client.patch(URL, {'recipient_validation_enabled': True}, format='json')

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(SystemSettings.get().recipient_validation_enabled)

    def test_enabling_with_only_an_inactive_profile_returns_400(self):
        make_profile(payment_method=Payment.MOBILE_PAYMENT, is_active=False)
        auth_client(self.client, make_user(Role.MANAGER))

        resp = self.client.patch(URL, {'recipient_validation_enabled': True}, format='json')

        self.assertEqual(resp.status_code, 400)
