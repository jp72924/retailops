from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Customer,
    Payment,
    Product,
    ProductCategory,
    Role,
    SalesOrder,
    SalesOrderItem,
    User,
)


class PaymentCreateReferenceNumberTests(TestCase):
    """
    The back-office payment form requires a reference number for bank
    transfer, card and check payments. That rule is the origin of the one
    since ported to PaymentSerializer, and it had no test of its own.
    """

    def setUp(self):
        role = Role.objects.create(name=Role.STAFF)
        self.user = User.objects.create_user(
            email='staff@example.com',
            password='StaffPass123!',
            first_name='Sam',
            last_name='Staff',
            role=role,
        )
        self.client.force_login(self.user)

        customer = Customer.objects.create(
            first_name='Jane', last_name='Customer', email='jane@example.com',
        )
        category = ProductCategory.objects.create(name='Widgets')
        product = Product.objects.create(
            sku='BO-001', name='Widget', category=category,
            unit_price=Decimal('10.00'),
            external_image_url='https://cdn.example.com/widget.png',
        )
        self.order = SalesOrder.objects.create(
            customer=customer, status=SalesOrder.CONFIRMED, created_by=self.user,
        )
        SalesOrderItem.objects.create(
            sales_order=self.order, product=product, quantity=1,
            unit_price=Decimal('10.00'), line_total=Decimal('10.00'),
        )
        self.order.subtotal = Decimal('10.00')
        self.order.total_amount = Decimal('10.00')
        self.order.save()

        self.url = reverse('payment-create')

    def _post(self, payment_method, reference_number=''):
        return self.client.post(self.url, {
            'sales_order': self.order.pk,
            'amount': '10.00',
            'payment_method': payment_method,
            'reference_number': reference_number,
        })

    def _messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_reference_number_required_for_bank_transfer_card_and_check(self):
        for method in (Payment.BANK_TRANSFER, Payment.CARD, Payment.CHECK):
            for label, reference in (('omitted', ''), ('whitespace', '   ')):
                with self.subTest(method=method, reference=label):
                    response = self._post(method, reference)

                    self.assertRedirects(
                        response,
                        reverse('order-detail', args=[self.order.pk]),
                        fetch_redirect_response=False,
                    )
                    self.assertIn(
                        'A reference number is required for bank transfer, card, and check payments.',
                        self._messages(response),
                    )
                    self.assertFalse(Payment.objects.exists())

    def test_reference_number_accepted_for_bank_transfer_card_and_check(self):
        for method in (Payment.BANK_TRANSFER, Payment.CARD, Payment.CHECK):
            with self.subTest(method=method):
                Payment.objects.all().delete()
                SalesOrder.objects.filter(pk=self.order.pk).update(
                    status=SalesOrder.CONFIRMED, paid_at=None,
                )

                response = self._post(method, f'{method}-REF-001')

                self.assertRedirects(
                    response,
                    reverse('order-detail', args=[self.order.pk]),
                    fetch_redirect_response=False,
                )
                payment = Payment.objects.get()
                self.assertEqual(payment.reference_number, f'{method}-REF-001')

    def test_cash_does_not_require_a_reference_number(self):
        """Guard against over-broadening the rule beyond the three methods."""
        response = self._post(Payment.CASH)

        self.assertRedirects(
            response,
            reverse('order-detail', args=[self.order.pk]),
            fetch_redirect_response=False,
        )
        payment = Payment.objects.get()
        self.assertEqual(payment.payment_method, Payment.CASH)
        self.assertEqual(payment.reference_number, '')
