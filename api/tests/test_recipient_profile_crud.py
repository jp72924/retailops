from rest_framework.test import APITestCase

from core.models import Payment, RecipientProfile, Role
from .helpers import auth_client, make_user


URL = '/api/v1/payment-recipient-profiles/'


def make_profile(payment_method=Payment.MOBILE_PAYMENT, phone='4141234567',
                  bank='BDV', document_id='V12345678', **attrs):
    creator = attrs.pop('created_by', None) or make_user(Role.ADMIN)
    return RecipientProfile.objects.create(
        label=attrs.pop('label', 'Main store'),
        payment_method=payment_method,
        phone=phone,
        bank=bank,
        document_id=document_id,
        created_by=creator,
        **attrs,
    )


class RecipientProfileAPITests(APITestCase):
    def test_unauthenticated_rejected(self):
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 401)

    def test_staff_forbidden_on_list_and_create(self):
        auth_client(self.client, make_user(Role.STAFF))

        self.assertEqual(self.client.get(URL).status_code, 403)
        resp = self.client.post(URL, {
            'payment_method': Payment.MOBILE_PAYMENT,
            'phone': '4141234567', 'bank': 'BDV', 'document_id': 'V1',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_create_and_created_by_is_auto_set(self):
        manager = make_user(Role.MANAGER)
        auth_client(self.client, manager)

        resp = self.client.post(URL, {
            'label': 'Main store', 'payment_method': Payment.MOBILE_PAYMENT,
            'phone': '4141234567', 'bank': 'BDV', 'document_id': 'V12345678',
        }, format='json')

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['created_by'], manager.pk)
        self.assertTrue(resp.data['is_active'])

    def test_duplicate_combo_returns_400(self):
        make_profile()
        auth_client(self.client, make_user(Role.ADMIN))

        resp = self.client.post(URL, {
            'label': 'Duplicate', 'payment_method': Payment.MOBILE_PAYMENT,
            'phone': '4141234567', 'bank': 'BDV', 'document_id': 'V12345678',
        }, format='json')

        self.assertEqual(resp.status_code, 400)

    def test_missing_required_field_returns_400(self):
        auth_client(self.client, make_user(Role.ADMIN))

        resp = self.client.post(URL, {
            'payment_method': Payment.MOBILE_PAYMENT,
            'phone': '', 'bank': 'BDV', 'document_id': 'V1',
        }, format='json')

        self.assertEqual(resp.status_code, 400)
        self.assertIn('phone', resp.data['details'])

    def test_manager_can_list_update_and_delete(self):
        profile = make_profile()
        auth_client(self.client, make_user(Role.MANAGER))

        list_resp = self.client.get(URL)
        self.assertEqual(list_resp.status_code, 200)

        update_resp = self.client.patch(f'{URL}{profile.pk}/', {'is_active': False}, format='json')
        self.assertEqual(update_resp.status_code, 200)
        self.assertFalse(update_resp.data['is_active'])

        delete_resp = self.client.delete(f'{URL}{profile.pk}/')
        self.assertEqual(delete_resp.status_code, 204)
        self.assertFalse(RecipientProfile.objects.filter(pk=profile.pk).exists())
