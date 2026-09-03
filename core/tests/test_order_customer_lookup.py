from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.views import _order_form_context, _save_order_items, order_create, order_detail
from core.models import (
    Customer,
    Product,
    ProductCategory,
    Role,
    SalesOrder,
    SalesOrderItem,
    User,
)


def make_staff(email='staff@example.com', role_name=Role.STAFF):
    role, _ = Role.objects.get_or_create(name=role_name)
    return User.objects.create_user(
        email=email, password='StaffPass123!',
        first_name='Sam', last_name='Staff', role=role,
    )


class CustomerLookupTests(TestCase):
    """
    The order form finds customers by ID number only, so lookup has to be
    forgiving about how the ID was written down.
    """

    def setUp(self):
        self.client.force_login(make_staff())
        self.url = reverse('customer-lookup')

    def _customer(self, national_id, email='c@example.com', first='Ana', last='López'):
        return Customer.objects.create(
            first_name=first, last_name=last, email=email,
            phone='0414-1234567', national_id=national_id,
        )

    def test_exact_match(self):
        c = self._customer('V12345678')

        resp = self.client.get(self.url, {'national_id': 'V12345678'})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'found')
        self.assertEqual(data['customer']['id'], c.pk)
        self.assertEqual(data['customer']['full_name'], 'Ana López')
        self.assertEqual(data['customer']['phone'], '0414-1234567')
        self.assertIn('detail_url', data['customer'])

    def test_punctuation_and_case_are_ignored(self):
        c = self._customer('V12345678')

        for typed in ('v-12.345.678', 'V-12.345.678', ' v12345678 ', 'V 12 345 678'):
            with self.subTest(typed=typed):
                resp = self.client.get(self.url, {'national_id': typed})
                self.assertEqual(resp.json()['status'], 'found', typed)
                self.assertEqual(resp.json()['customer']['id'], c.pk)

    def test_finds_customer_whose_stored_id_carries_punctuation(self):
        """Normalizing only the input is not enough — the column may be dirty."""
        c = self._customer('V-12.345.678')

        resp = self.client.get(self.url, {'national_id': 'V12345678'})

        self.assertEqual(resp.json()['status'], 'found')
        self.assertEqual(resp.json()['customer']['id'], c.pk)

    def test_duplicates_that_normalize_alike_are_reported_not_guessed(self):
        # Uniqueness is on the raw string, so both of these can legally exist.
        self._customer('V123456', email='a@example.com')
        self._customer('V-123456', email='b@example.com')

        resp = self.client.get(self.url, {'national_id': 'V.123.456'})

        data = resp.json()
        self.assertEqual(data['status'], 'ambiguous')
        self.assertNotIn('customer', data)
        self.assertEqual(len(data['candidates']), 2)

    def test_exact_raw_match_wins_over_ambiguity(self):
        """Typing one of the raw values resolves it via the indexed fast path."""
        exact = self._customer('V123456', email='a@example.com')
        self._customer('V-123456', email='b@example.com')

        resp = self.client.get(self.url, {'national_id': 'V123456'})

        self.assertEqual(resp.json()['status'], 'found')
        self.assertEqual(resp.json()['customer']['id'], exact.pk)

    def test_no_match_is_not_an_error(self):
        resp = self.client.get(self.url, {'national_id': 'V99999999'})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'not_found')

    def test_blank_id_returns_400(self):
        resp = self.client.get(self.url, {'national_id': '  ...  '})

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()

        resp = self.client.get(self.url, {'national_id': 'V12345678'})

        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_kiosk_role_is_forbidden(self):
        self.client.force_login(make_staff(email='kiosk@example.com', role_name=Role.KIOSK))

        resp = self.client.get(self.url, {'national_id': 'V12345678'})

        self.assertEqual(resp.status_code, 403)


class CustomerQuickCreateTests(TestCase):
    def setUp(self):
        self.client.force_login(make_staff())
        self.url = reverse('customer-quick-create')
        self.payload = {
            'national_id': 'V87654321',
            'first_name': 'Nuevo',
            'last_name': 'Cliente',
            'email': 'nuevo@example.com',
            'phone': '0424-7654321',
        }

    def test_creates_customer_and_stores_normalized_id(self):
        resp = self.client.post(self.url, dict(self.payload, national_id='v-87.654.321'))

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        customer = Customer.objects.get(pk=data['customer']['id'])
        self.assertEqual(customer.national_id, 'V87654321')
        self.assertEqual(customer.email, 'nuevo@example.com')
        self.assertEqual(
            set(data['customer']),
            {'id', 'national_id', 'full_name', 'first_name', 'last_name',
             'email', 'phone', 'detail_url'},
        )

    def test_national_id_is_required_even_though_the_model_allows_null(self):
        """Without an ID the order form could never find this customer again."""
        resp = self.client.post(self.url, dict(self.payload, national_id=''))

        self.assertEqual(resp.status_code, 400)
        self.assertIn('national_id', resp.json()['errors'])
        self.assertFalse(Customer.objects.filter(email='nuevo@example.com').exists())

    def test_missing_name_and_email_are_reported_per_field(self):
        resp = self.client.post(self.url, {'national_id': 'V11111111'})

        errors = resp.json()['errors']
        self.assertEqual(resp.status_code, 400)
        self.assertIn('first_name', errors)
        self.assertIn('last_name', errors)
        self.assertIn('email', errors)

    def test_duplicate_email_rejected(self):
        Customer.objects.create(
            first_name='Old', last_name='One', email='nuevo@example.com',
            national_id='V00000001',
        )

        resp = self.client.post(self.url, self.payload)

        self.assertEqual(resp.status_code, 400)
        self.assertIn('email', resp.json()['errors'])

    def test_duplicate_id_written_differently_is_rejected(self):
        """Otherwise the modal could manufacture the ambiguous lookup state."""
        Customer.objects.create(
            first_name='Old', last_name='One', email='old@example.com',
            national_id='V87654321',
        )

        resp = self.client.post(self.url, dict(self.payload, national_id='V-87.654.321'))

        self.assertEqual(resp.status_code, 400)
        self.assertIn('national_id', resp.json()['errors'])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)


class OrderFormCustomerSectionTests(TestCase):
    """
    Rendering assertions go through RequestFactory rather than self.client:
    the test client's template instrumentation crashes under Python 3.14 with
    Django 4.2 (Context.__copy__), which is the cause of this suite's known
    pre-existing failures. RequestFactory renders the same view without
    connecting that receiver.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.user = make_staff()
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            first_name='Ana', last_name='López', email='ana@example.com',
            national_id='V12345678',
        )
        category = ProductCategory.objects.create(name='Widgets')
        self.product = Product.objects.create(
            sku='BO-001', name='Widget', category=category,
            unit_price=Decimal('10.00'),
            external_image_url='https://cdn.example.com/widget.png',
        )

    def _render(self, method='get', data=None, **params):
        request = getattr(self.factory, method)(reverse('order-create'), data or params)
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return order_create(request)

    def test_context_no_longer_carries_the_full_customer_list(self):
        ctx = _order_form_context()

        self.assertNotIn('customers', ctx)
        self.assertIsNone(ctx['selected_customer'])

    def test_context_defaults_selected_customer_from_the_order(self):
        order = SalesOrder.objects.create(
            customer=self.customer, status=SalesOrder.DRAFT, created_by=self.user,
        )

        self.assertEqual(_order_form_context(order)['selected_customer'], self.customer)

    def test_form_renders_the_id_field_not_a_dropdown(self):
        html = self._render().content.decode()

        self.assertNotIn('<select id="id_customer"', html)
        self.assertIn('id="id_customer_nid"', html)
        self.assertIn('id="btn-new-customer"', html)
        self.assertIn('id="new-customer-modal"', html)

    def test_deep_link_prefills_the_id_and_the_hidden_pk(self):
        html = self._render(customer=self.customer.pk).content.decode()

        self.assertIn('value="V12345678"', html)
        self.assertIn(f'id="id_customer" name="customer"\n              value="{self.customer.pk}"', html)

    def test_stale_deep_link_renders_a_blank_form(self):
        response = self._render(customer='999999')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('value="V12345678"', response.content.decode())

    def test_posting_the_hidden_pk_still_creates_the_order(self):
        resp = self.client.post(reverse('order-create'), {
            'customer': str(self.customer.pk),
            'discount_amount': '0',
            'notes': '',
            'line_item_count': '1',
            'product_1': str(self.product.pk),
            'quantity_1': '2',
            'unit_price_1': '10.00',
        })

        order = SalesOrder.objects.get()
        # fetch_redirect_response=False: rendering the target through the test
        # client would trip the Python 3.14 instrumentation bug noted above.
        self.assertRedirects(resp, reverse('order-detail', args=[order.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(order.customer, self.customer)

    def test_missing_customer_error_is_now_visible(self):
        html = self._render('post', {
            'customer': '',
            'line_item_count': '1',
            'product_1': str(self.product.pk),
            'quantity_1': '1',
            'unit_price_1': '10.00',
        }).content.decode()

        self.assertIn('Please select a customer.', html)
        self.assertEqual(SalesOrder.objects.count(), 0)


class OrderDuplicateLineItemTests(TestCase):
    """A product may appear on an order once; quantity carries the count."""

    def setUp(self):
        self.user = make_staff()
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            first_name='Ana', last_name='López', email='ana@example.com',
            national_id='V12345678',
        )
        category = ProductCategory.objects.create(name='Widgets')
        self.widget = Product.objects.create(
            sku='BO-001', name='Widget', category=category,
            unit_price=Decimal('10.00'),
            external_image_url='https://cdn.example.com/widget.png',
        )
        self.gadget = Product.objects.create(
            sku='BO-002', name='Gadget', category=category,
            unit_price=Decimal('5.00'),
            external_image_url='https://cdn.example.com/gadget.png',
        )

    def _post(self, rows, **extra):
        data = {'customer': str(self.customer.pk), 'discount_amount': '0',
                'notes': '', 'line_item_count': str(len(rows))}
        for i, (product, qty) in enumerate(rows, start=1):
            data[f'product_{i}'] = str(product.pk)
            data[f'quantity_{i}'] = str(qty)
            data[f'unit_price_{i}'] = str(product.unit_price)
        data.update(extra)
        return data

    def test_duplicate_product_is_rejected_on_create(self):
        # RequestFactory, not self.client: re-rendering through the test client
        # trips the Python 3.14 instrumentation bug noted above.
        request = RequestFactory().post(
            reverse('order-create'),
            self._post([(self.widget, 1), (self.widget, 2)]),
        )
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        response = order_create(request)

        self.assertEqual(response.status_code, 200)  # re-rendered, not redirected
        self.assertIn('Widget', response.content.decode())
        self.assertEqual(SalesOrder.objects.count(), 0)

    def test_distinct_products_are_accepted(self):
        resp = self.client.post(
            reverse('order-create'),
            self._post([(self.widget, 1), (self.gadget, 3)]),
        )

        order = SalesOrder.objects.get()
        self.assertRedirects(resp, reverse('order-detail', args=[order.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(order.items.count(), 2)

    def test_duplicate_names_the_product_and_says_what_to_do(self):
        _, err = _save_order_items(
            SalesOrder.objects.create(customer=self.customer, status=SalesOrder.DRAFT,
                                      created_by=self.user),
            [(str(self.widget.pk), '1', '10.00'), (str(self.widget.pk), '2', '10.00')],
        )

        self.assertIn('Widget', err)
        self.assertIn('more than one line', err)

    def test_existing_items_survive_a_rejected_duplicate_edit(self):
        """
        The callers return from inside transaction.atomic() instead of raising,
        so validating after the delete would silently wipe the order's lines.
        """
        order = SalesOrder.objects.create(
            customer=self.customer, status=SalesOrder.DRAFT, created_by=self.user,
        )
        SalesOrderItem.objects.create(
            sales_order=order, product=self.widget, quantity=1,
            unit_price=Decimal('10.00'),
        )

        count, err = _save_order_items(
            order,
            [(str(self.gadget.pk), '1', '5.00'), (str(self.gadget.pk), '1', '5.00')],
        )

        self.assertEqual(count, 0)
        self.assertIsNotNone(err)
        order.refresh_from_db()
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.first().product, self.widget)


class OrderUnitPriceDisplayTests(TestCase):
    """
    Unit price is shown, not typed: it comes from the product. The hidden input
    still carries the stored value so editing a line never reprices it.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.user = make_staff()
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            first_name='Ana', last_name='López', email='ana@example.com',
            national_id='V12345678',
        )
        category = ProductCategory.objects.create(name='Widgets')
        self.product = Product.objects.create(
            sku='BO-001', name='Widget', category=category,
            unit_price=Decimal('10.00'),
            external_image_url='https://cdn.example.com/widget.png',
        )

    def _draft_with_item(self, unit_price):
        order = SalesOrder.objects.create(
            customer=self.customer, status=SalesOrder.DRAFT, created_by=self.user,
        )
        SalesOrderItem.objects.create(
            sales_order=order, product=self.product, quantity=1, unit_price=unit_price,
        )
        return order

    def test_editable_row_has_no_price_input_only_a_hidden_value(self):
        order = self._draft_with_item(Decimal('7.50'))
        request = self.factory.get(reverse('order-detail', args=[order.pk]))
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        html = order_detail(request, order.pk).content.decode()

        self.assertNotIn('type="number" name="unit_price_1"', html)
        self.assertIn('type="hidden" name="unit_price_1"', html)
        self.assertIn('id="unit-price-1"', html)

    def test_editing_a_line_keeps_the_stored_price_not_the_product_price(self):
        """The snapshot is the point: a $7.50 line stays $7.50 after an edit."""
        order = self._draft_with_item(Decimal('7.50'))

        self.client.post(reverse('order-detail', args=[order.pk]), {
            'customer': str(self.customer.pk),
            'discount_amount': '0', 'notes': '',
            'line_item_count': '1',
            'product_1': str(self.product.pk),
            'quantity_1': '4',
            'unit_price_1': '7.50',  # what the hidden input submits
        })

        item = order.items.get()
        self.assertEqual(item.quantity, 4)
        self.assertEqual(item.unit_price, Decimal('7.50'))
        self.assertEqual(item.line_total, Decimal('30.00'))


