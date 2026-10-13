"""
Service accounts and financial records where staff work: the back-office user
pages, the sign-in form and the Django admin.

What matters: a kiosk or channel service account never signs in, user
management and the admin cannot create, give a password to or edit one, and
the admin can no longer rewrite a payment or an order by hand (their status,
money and stock move only through the services that lock and apply them).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.tests.helpers import (
    make_category,
    make_channel,
    make_channel_order,
    make_listing,
    make_payment,
    make_product,
    make_role,
    make_user,
    set_currency,
)
from core.models import Payment, Role, SalesOrder

User = get_user_model()


class ServiceUserFixture(TestCase):
    def setUp(self):
        set_currency()
        self.admin = make_user(Role.ADMIN, is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)
        self.channel, _key = make_channel('shop', admin=self.admin)
        self.channel_user = self.channel.service_user
        self.kiosk_user = User.objects.create_user(
            email='kiosk-1@example.com', password=None, first_name='Kiosk', last_name='One',
            role=make_role(Role.KIOSK),
        )
        self.staff_role = make_role(Role.STAFF)


class SignInTests(ServiceUserFixture):
    def setUp(self):
        super().setUp()
        self.client.logout()
        self.channel_user.set_password('Sneaky-pass-1!')  # the dangerous case
        self.channel_user.save()

    def test_a_service_account_cannot_sign_in_even_with_its_password(self):
        response = self.client.post(reverse('login'), {
            'email': self.channel_user.email, 'password': 'Sneaky-pass-1!',
        })

        self.assertEqual(response.status_code, 200)  # the form again, not a redirect
        self.assertContains(response, 'Invalid email or password.')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_a_person_still_signs_in(self):
        response = self.client.post(reverse('login'), {'email': self.admin.email, 'password': 'Pass12345!'})

        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)


class UserPagesTests(ServiceUserFixture):
    def test_the_role_choices_leave_out_the_service_roles(self):
        for url in (reverse('user-list'), reverse('user-edit', args=[make_user(Role.STAFF).pk])):
            names = [role.name for role in self.client.get(url).context['roles']]

            self.assertNotIn(Role.KIOSK, names, url)
            self.assertNotIn(Role.CHANNEL, names, url)
            self.assertIn(Role.STAFF, names, url)

    def test_the_list_shows_a_service_account_without_edit_controls(self):
        response = self.client.get(reverse('user-list'))

        self.assertContains(response, self.channel_user.email)
        self.assertContains(response, 'Service account')
        self.assertNotContains(response, reverse('user-edit', args=[self.channel_user.pk]))
        self.assertNotContains(response, reverse('user-deactivate', args=[self.channel_user.pk]))
        person = make_user(Role.STAFF, email='person@example.com')
        self.assertContains(self.client.get(reverse('user-list')), reverse('user-edit', args=[person.pk]))

    def test_a_service_role_cannot_be_given_to_a_new_user(self):
        for role in (make_role(Role.KIOSK), make_role(Role.CHANNEL)):
            response = self.client.post(reverse('user-invite'), {
                'first_name': 'New', 'last_name': 'Person', 'email': f'{role.name}@example.com',
                'role': role.pk, 'password': 'Pass12345!',
            })

            self.assertEqual(response.status_code, 404, role.name)
            self.assertFalse(User.objects.filter(email=f'{role.name}@example.com').exists())

    def test_a_person_can_still_be_invited(self):
        response = self.client.post(reverse('user-invite'), {
            'first_name': 'New', 'last_name': 'Person', 'email': 'new@example.com',
            'role': self.staff_role.pk, 'password': 'Pass12345!',
        })

        self.assertRedirects(response, reverse('user-list'))
        self.assertTrue(User.objects.filter(email='new@example.com').exists())

    def test_a_person_cannot_be_turned_into_a_service_account(self):
        person = make_user(Role.STAFF, email='person@example.com')

        response = self.client.post(reverse('user-edit', args=[person.pk]), {
            'first_name': 'P', 'last_name': 'P', 'email': 'person@example.com',
            'role': make_role(Role.CHANNEL).pk, 'is_active': '1',
        })

        self.assertEqual(response.status_code, 404)
        person.refresh_from_db()
        self.assertEqual(person.role_name, Role.STAFF)

    def test_a_service_account_cannot_be_edited_or_given_a_password(self):
        url = reverse('user-edit', args=[self.channel_user.pk])

        got = self.client.get(url)
        profile = self.client.post(url, {
            'first_name': 'Changed', 'last_name': 'X', 'email': 'x@example.com',
            'role': self.staff_role.pk, 'is_active': '1',
        })
        password = self.client.post(url, {
            'action': 'change_password', 'new_password': 'Brand-new-1!', 'confirm_password': 'Brand-new-1!',
        })

        for response in (got, profile, password):
            self.assertRedirects(response, reverse('user-list'), fetch_redirect_response=False)
        self.channel_user.refresh_from_db()
        self.assertEqual(self.channel_user.role_name, Role.CHANNEL)
        self.assertNotEqual(self.channel_user.first_name, 'Changed')
        self.assertFalse(self.channel_user.has_usable_password())

    def test_a_service_account_is_not_deactivated_here(self):
        response = self.client.post(reverse('user-deactivate', args=[self.kiosk_user.pk]))

        self.assertRedirects(response, reverse('user-list'), fetch_redirect_response=False)
        self.kiosk_user.refresh_from_db()
        self.assertTrue(self.kiosk_user.is_active)

    def test_a_service_account_that_was_switched_off_is_not_reactivated_here(self):
        User.objects.filter(pk=self.kiosk_user.pk).update(is_active=False)

        response = self.client.post(reverse('user-reactivate', args=[self.kiosk_user.pk]))

        self.assertRedirects(response, reverse('user-list'), fetch_redirect_response=False)
        self.kiosk_user.refresh_from_db()
        self.assertFalse(self.kiosk_user.is_active)


class UserAdminTests(ServiceUserFixture):
    def test_the_add_form_offers_no_service_role(self):
        response = self.client.get(reverse('admin:core_user_add'))

        names = [role.name for role in response.context['adminform'].form.fields['role'].queryset]
        self.assertNotIn(Role.KIOSK, names)
        self.assertNotIn(Role.CHANNEL, names)
        self.assertIn(Role.STAFF, names)

    def test_a_service_account_is_shown_read_only(self):
        url = reverse('admin:core_user_change', args=[self.channel_user.pk])

        response = self.client.get(url)
        form = response.context['adminform'].form

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(form.fields), [])  # nothing about it is editable here

    def test_a_service_account_cannot_be_changed_through_the_admin(self):
        url = reverse('admin:core_user_change', args=[self.channel_user.pk])

        self.client.post(url, {
            'email': 'hijack@example.com', 'first_name': 'Hijack', 'last_name': 'X',
            'role': self.staff_role.pk, 'is_active': 'on', 'is_staff': 'on',
        })

        self.channel_user.refresh_from_db()
        self.assertNotEqual(self.channel_user.email, 'hijack@example.com')
        self.assertEqual(self.channel_user.role_name, Role.CHANNEL)
        self.assertFalse(self.channel_user.is_staff)

    def test_a_service_account_cannot_be_given_a_password_through_the_admin(self):
        url = reverse('admin:auth_user_password_change', args=[self.channel_user.pk])

        response = self.client.post(url, {'password1': 'Brand-new-1!', 'password2': 'Brand-new-1!'})

        self.assertRedirects(
            response, reverse('admin:core_user_change', args=[self.channel_user.pk]),
            fetch_redirect_response=False,
        )
        self.channel_user.refresh_from_db()
        self.assertFalse(self.channel_user.has_usable_password())

    def test_a_person_can_still_be_edited_and_given_a_password_in_the_admin(self):
        person = make_user(Role.STAFF, email='person@example.com')

        change = self.client.get(reverse('admin:core_user_change', args=[person.pk]))
        password = self.client.post(reverse('admin:auth_user_password_change', args=[person.pk]), {
            'password1': 'Brand-new-1!', 'password2': 'Brand-new-1!',
        })

        self.assertEqual(change.status_code, 200)
        self.assertIn('role', change.context['adminform'].form.fields)
        self.assertEqual(password.status_code, 302)
        person.refresh_from_db()
        self.assertTrue(person.check_password('Brand-new-1!'))


class FinancialRecordsInTheAdminTests(ServiceUserFixture):
    def setUp(self):
        super().setUp()
        self.product = make_product(sku='BOX-1', stock=10, category=make_category('Software'))
        make_listing(self.product, slug='box')
        self.order = make_channel_order(self.channel, self.product, reference='WEB-1').sales_order
        self.payment = make_payment(self.order, status=Payment.PENDING_REVIEW, payment_method=Payment.MOBILE_PAYMENT)

    def change(self, model, obj):
        return reverse(f'admin:core_{model}_change', args=[obj.pk])

    def test_a_payments_status_and_money_cannot_be_edited_by_hand(self):
        form = self.client.get(self.change('payment', self.payment)).context['adminform'].form

        for field in ('status', 'amount', 'sales_order', 'transaction_key', 'ocr_receipt_data', 'verified_at'):
            self.assertNotIn(field, form.fields, field)

        self.client.post(self.change('payment', self.payment), {
            'status': Payment.CONFIRMED, 'amount': '0.01', 'notes': 'checked by hand',
        })
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.PENDING_REVIEW)
        self.assertNotEqual(str(self.payment.amount), '0.01')

    def test_a_payments_notes_can_still_be_annotated(self):
        self.assertIn('notes', self.client.get(self.change('payment', self.payment)).context['adminform'].form.fields)

        self.client.post(self.change('payment', self.payment), {'notes': 'called the customer'})

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.notes, 'called the customer')

    def test_payments_cannot_be_added_or_deleted_in_the_admin(self):
        self.assertEqual(self.client.get(reverse('admin:core_payment_add')).status_code, 403)
        delete = self.client.post(reverse('admin:core_payment_delete', args=[self.payment.pk]), {'post': 'yes'})

        self.assertEqual(delete.status_code, 403)
        self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())

    def test_an_orders_status_and_totals_cannot_be_edited_by_hand(self):
        form = self.client.get(self.change('salesorder', self.order)).context['adminform'].form

        for field in ('status', 'subtotal', 'discount_amount', 'total_amount', 'customer', 'paid_at'):
            self.assertNotIn(field, form.fields, field)

        self.client.post(self.change('salesorder', self.order), {
            'status': SalesOrder.PAID, 'total_amount': '0.01', 'notes': 'hand note',
        })
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.CONFIRMED)
        self.assertEqual(str(self.order.total_amount), '10.00')

    def test_an_orders_lines_cannot_be_edited_in_the_admin(self):
        response = self.client.get(self.change('salesorder', self.order))

        inline = response.context['inline_admin_formsets'][0]
        self.assertFalse(inline.opts.has_add_permission(response.wsgi_request))
        self.assertFalse(inline.opts.has_change_permission(response.wsgi_request))
        self.assertFalse(inline.opts.has_delete_permission(response.wsgi_request))

    def test_orders_cannot_be_added_or_deleted_in_the_admin(self):
        self.assertEqual(self.client.get(reverse('admin:core_salesorder_add')).status_code, 403)
        delete = self.client.post(reverse('admin:core_salesorder_delete', args=[self.order.pk]), {'post': 'yes'})

        self.assertEqual(delete.status_code, 403)
        self.assertTrue(SalesOrder.objects.filter(pk=self.order.pk).exists())
