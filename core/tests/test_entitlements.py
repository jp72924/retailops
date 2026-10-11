"""
What a customer is given when they pay: creation, encryption, supply, reveal,
revocation.

What matters: nothing is owed until an order is paid and a refund takes it
back, a licence key is never stored or shown in clear except by the one reveal,
and the first reveal is recorded as evidence and never overwritten.
"""
import importlib
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core import checks
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from api.tests.helpers import (
    make_category,
    make_fulfillment,
    make_order,
    make_payment,
    make_product,
    make_user,
    pay_order,
    set_currency,
)
from core.models import Entitlement, Product, ProductFulfillment, Role, SalesOrder
from core.services import entitlements as service
from core.services.entitlements import (
    EncryptionNotConfigured,
    EntitlementDecryptionError,
    EntitlementStateError,
    InvalidEntitlementValue,
    create_entitlements,
    delivery_complete,
    reveal,
    revoke,
    rotate_keys,
    supply_value,
)
from core.services.orders import cancel_order, refund_order
from core.services.payments import apply_confirmed_payments, approve_payment

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
LICENCE = 'ABCD-1234-EFGH-5678'
LINK = 'https://downloads.example.com/setup'


class DeliveryFixture(TestCase):
    def setUp(self):
        set_currency()
        self.staff = make_user(Role.STAFF)
        self.manager = make_user(Role.MANAGER)
        self.category = make_category('Software')

    def product(self, sku='LIC-12M', **attrs):
        attrs.setdefault('stock', 0)
        attrs.setdefault('kind', Product.DIGITAL)
        attrs.setdefault('track_inventory', False)
        attrs.setdefault('unit_price', '29.00')
        return make_product(sku=sku, category=self.category, **attrs)

    def paid_order(self, product, quantity=1):
        order = make_order(product=product, status=SalesOrder.CONFIRMED, quantity=quantity)
        pay_order(order)
        return SalesOrder.objects.get(pk=order.pk)

    def entitlements(self, order):
        return list(Entitlement.objects.filter(order_item__sales_order=order).order_by('pk'))


class FulfillmentConfigTests(DeliveryFixture):
    def build(self, **attrs):
        sku = f'CFG-{Product.objects.count()}'
        values = dict(product=self.product(sku), mode='template', entitlement_kind='link', template=LINK)
        values.update(attrs)
        return ProductFulfillment(**values)

    def test_fixed_text_can_be_a_link_or_instructions(self):
        self.build().full_clean()
        self.build(product=self.product('B-1'), entitlement_kind='instructions',
                   template='Open the app and sign in.').full_clean()

    def test_a_licence_key_cannot_be_fixed_text(self):
        with self.assertRaises(ValidationError) as caught:
            self.build(entitlement_kind='key', template='SAME-KEY-FOR-ALL').full_clean()
        self.assertIn('entitlement_kind', caught.exception.message_dict)

    def test_fixed_text_needs_text_and_a_link_must_be_http(self):
        for bad in ('', '   ', 'not a url', 'javascript:alert(1)', 'ftp://files.example.com/x'):
            with self.assertRaises(ValidationError, msg=repr(bad)) as caught:
                self.build(template=bad).full_clean()
            self.assertIn('template', caught.exception.message_dict)

    def test_a_supplied_delivery_needs_no_text(self):
        self.build(mode='supplied', entitlement_kind='key', template='').full_clean()

    def test_a_term_is_at_least_a_day(self):
        with self.assertRaises(ValidationError):
            self.build(duration_days=0).full_clean()
        self.build(duration_days=365).full_clean()


class CreationTests(DeliveryFixture):
    def test_fixed_text_is_delivered_the_moment_the_order_is_paid(self):
        product = self.product()
        make_fulfillment(product, duration_days=30)

        order = self.paid_order(product)

        [entitlement] = self.entitlements(order)
        self.assertEqual((entitlement.status, entitlement.kind), ('active', 'link'))
        self.assertEqual(entitlement.value, LINK)
        self.assertEqual(entitlement.activated_at, order.paid_at)
        self.assertEqual(entitlement.expires_at, order.paid_at + timedelta(days=30))

    def test_a_supplied_delivery_waits_for_its_value(self):
        product = self.product()
        make_fulfillment(product, mode='supplied', kind='key')

        [entitlement] = self.entitlements(self.paid_order(product))

        self.assertEqual(entitlement.status, 'pending')
        self.assertEqual((entitlement.value, entitlement.hint), ('', ''))
        self.assertIsNone(entitlement.activated_at)
        self.assertIsNone(entitlement.expires_at)

    def test_one_entitlement_covers_the_line_unless_it_is_per_unit(self):
        whole = self.product('WHOLE')
        each = self.product('EACH')
        make_fulfillment(whole, mode='supplied', kind='key')
        make_fulfillment(each, mode='supplied', kind='key', per_unit=True)

        covering = self.entitlements(self.paid_order(whole, quantity=3))
        separate = self.entitlements(self.paid_order(each, quantity=3))

        self.assertEqual([e.seats for e in covering], [3])
        self.assertEqual([e.seats for e in separate], [1, 1, 1])

    def test_a_product_with_no_fulfillment_delivers_nothing(self):
        self.assertEqual(self.entitlements(self.paid_order(self.product())), [])

    def test_only_the_lines_that_deliver_get_one(self):
        digital = self.product('DIGITAL')
        physical = make_product(sku='BOX', stock=5, category=self.category)
        make_fulfillment(digital)
        order = make_order(product=digital, status=SalesOrder.CONFIRMED)
        order.items.create(product=physical, quantity=1, unit_price=physical.unit_price,
                           line_total=physical.unit_price)
        order.total_amount += physical.unit_price
        order.save()

        pay_order(order)

        self.assertEqual([e.order_item.product.sku for e in self.entitlements(order)], ['DIGITAL'])

    def test_nothing_is_owed_before_the_order_is_paid(self):
        product = self.product()
        make_fulfillment(product)
        order = make_order(product=product, status=SalesOrder.CONFIRMED)

        make_payment(order=order, amount='10.00')  # partial
        apply_confirmed_payments(order)

        self.assertEqual(order.status, SalesOrder.CONFIRMED)
        self.assertEqual(self.entitlements(order), [])

    def test_a_cancelled_order_never_owes_anything(self):
        product = self.product()
        make_fulfillment(product)
        order = make_order(product=product, status=SalesOrder.CONFIRMED)

        cancel_order(order, self.manager)

        self.assertEqual(self.entitlements(order), [])

    def test_applying_payments_again_creates_nothing_more(self):
        product = self.product()
        make_fulfillment(product, mode='supplied', kind='key')
        order = self.paid_order(product)

        apply_confirmed_payments(order)
        create_entitlements(order)

        self.assertEqual(len(self.entitlements(order)), 1)

    def test_a_payment_approved_by_staff_delivers_too(self):
        product = self.product()
        make_fulfillment(product)
        order = make_order(product=product, status=SalesOrder.CONFIRMED)
        payment = make_payment(order=order, amount=order.total_amount, status='pending_review')

        approve_payment(payment, user=self.manager, transaction_key='bank-key')

        self.assertEqual(len(self.entitlements(order)), 1)

    def test_a_failure_while_delivering_leaves_the_order_unpaid(self):
        product = self.product()
        make_fulfillment(product)
        order = make_order(product=product, status=SalesOrder.CONFIRMED)
        make_payment(order=order, amount=order.total_amount)

        with patch('core.services.entitlements.create_entitlements', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                apply_confirmed_payments(order)

        self.assertEqual(SalesOrder.objects.get(pk=order.pk).status, SalesOrder.CONFIRMED)


class KeyCustodyTests(DeliveryFixture):
    def setUp(self):
        super().setUp()
        self.override = override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY_A])
        self.override.enable()
        self.addCleanup(self.override.disable)
        product = self.product()
        make_fulfillment(product, mode='supplied', kind='key')
        self.order = self.paid_order(product)
        [self.entitlement] = self.entitlements(self.order)

    def stored_values(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT value FROM core_entitlement')
            return [row[0] for row in cursor.fetchall()]

    def test_a_key_is_stored_encrypted(self):
        supply_value(self.entitlement.pk, LICENCE)

        [stored] = self.stored_values()
        self.assertNotIn(LICENCE, stored)
        self.assertNotIn('5678', stored)
        self.assertEqual(service.unseal(stored), LICENCE)

    def test_it_is_shown_masked_from_a_hint_without_decrypting(self):
        supply_value(self.entitlement.pk, LICENCE)
        entitlement = Entitlement.objects.get(pk=self.entitlement.pk)

        with patch.object(service, 'unseal', side_effect=AssertionError('listing must never decrypt')):
            shown = entitlement.display_value

        self.assertEqual(shown, '•' * 8 + '5678')
        self.assertNotIn('ABCD', shown)

    def test_a_short_key_gets_no_hint_at_all(self):
        supply_value(self.entitlement.pk, 'SHORT12')

        entitlement = Entitlement.objects.get(pk=self.entitlement.pk)
        self.assertEqual((entitlement.hint, entitlement.display_value), ('', '•' * 8))

    def test_nothing_that_names_an_entitlement_contains_its_key(self):
        supply_value(self.entitlement.pk, LICENCE)
        entitlement = Entitlement.objects.get(pk=self.entitlement.pk)

        for text in (str(entitlement), repr(entitlement), entitlement.display_value, entitlement.hint):
            self.assertNotIn(LICENCE, text)
            self.assertNotIn('ABCD', text)

    def test_links_and_instructions_are_not_secret_and_are_kept_as_given(self):
        link = self.product('LNK')
        make_fulfillment(link, mode='supplied', kind='link')
        [pending] = self.entitlements(self.paid_order(link))

        supply_value(pending.pk, LINK)

        entitlement = Entitlement.objects.get(pk=pending.pk)
        self.assertEqual((entitlement.value, entitlement.hint, entitlement.display_value), (LINK, '', LINK))

    def test_without_an_encryption_key_a_key_is_refused_and_nothing_is_written(self):
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[]):
            with self.assertRaises(EncryptionNotConfigured):
                supply_value(self.entitlement.pk, LICENCE)

        entitlement = Entitlement.objects.get(pk=self.entitlement.pk)
        self.assertEqual((entitlement.status, entitlement.value, entitlement.hint), ('pending', '', ''))

    def test_without_an_encryption_key_links_still_work(self):
        link = self.product('LNK')
        make_fulfillment(link, mode='supplied', kind='link')
        [pending] = self.entitlements(self.paid_order(link))

        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[]):
            supply_value(pending.pk, LINK)

        self.assertEqual(Entitlement.objects.get(pk=pending.pk).status, 'active')

    def test_a_key_sealed_with_a_key_that_is_gone_cannot_be_read(self):
        supply_value(self.entitlement.pk, LICENCE)

        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY_B]):
            with self.assertRaises(EntitlementDecryptionError):
                reveal(self.entitlement.pk)

        self.assertIsNone(Entitlement.objects.get(pk=self.entitlement.pk).revealed_at)

    def test_rotating_moves_every_key_to_the_new_primary_and_is_repeatable(self):
        supply_value(self.entitlement.pk, LICENCE)

        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY_B, KEY_A]):
            self.assertEqual(reveal(self.entitlement.pk)[1], LICENCE)  # old key still reads
            self.assertEqual(rotate_keys(), 1)
            self.assertEqual(rotate_keys(), 0)

        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY_B]):
            self.assertEqual(reveal(self.entitlement.pk)[1], LICENCE)  # the old key is no longer needed

    def test_rotation_refuses_a_key_it_cannot_read(self):
        supply_value(self.entitlement.pk, LICENCE)

        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY_B]):
            with self.assertRaises(EntitlementDecryptionError):
                rotate_keys()

    def test_rotating_leaves_links_alone(self):
        link = self.product('LNK')
        make_fulfillment(link)
        self.paid_order(link)

        self.assertEqual(rotate_keys(), 0)


class ConfigurationTests(TestCase):
    def settings_module(self):
        return importlib.import_module('retailops.settings')

    def test_a_malformed_key_fails_at_boot(self):
        with self.assertRaises(RuntimeError):
            self.settings_module()._entitlement_keys({'ENTITLEMENT_ENCRYPTION_KEYS': 'not-a-fernet-key'})

    def test_valid_keys_are_kept_in_order(self):
        keys = self.settings_module()._entitlement_keys({'ENTITLEMENT_ENCRYPTION_KEYS': f'{KEY_A}, {KEY_B}'})
        self.assertEqual(keys, [KEY_A, KEY_B])

    def test_a_missing_setting_is_reported_by_the_system_check(self):
        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[]):
            found = checks.run_checks()
        self.assertIn('core.W001', [message.id for message in found])

        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY_A]):
            found = checks.run_checks()
        self.assertNotIn('core.W001', [message.id for message in found])


@override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY_A])
class SupplyTests(DeliveryFixture):
    def setUp(self):
        super().setUp()
        product = self.product()
        make_fulfillment(product, mode='supplied', kind='key', duration_days=30)
        [self.entitlement] = self.entitlements(self.paid_order(product))

    def test_supplying_activates_it_and_starts_its_term_now_not_at_payment(self):
        later = timezone.now() + timedelta(days=5)

        supply_value(self.entitlement.pk, LICENCE, now=later)

        entitlement = Entitlement.objects.get(pk=self.entitlement.pk)
        self.assertEqual(entitlement.status, 'active')
        self.assertEqual(entitlement.activated_at, later)
        self.assertEqual(entitlement.expires_at, later + timedelta(days=30))

    def test_an_active_one_is_not_overwritten_by_accident(self):
        supply_value(self.entitlement.pk, LICENCE)

        with self.assertRaises(EntitlementStateError) as caught:
            supply_value(self.entitlement.pk, 'ZZZZ-9999-YYYY-0000')

        self.assertEqual(caught.exception.code, 'entitlement_not_pending')
        self.assertEqual(service.unseal(Entitlement.objects.get(pk=self.entitlement.pk).value), LICENCE)

    def test_a_reissue_keeps_the_term_and_forgets_the_old_reveal(self):
        supply_value(self.entitlement.pk, LICENCE)
        activated = Entitlement.objects.get(pk=self.entitlement.pk)
        reveal(self.entitlement.pk)

        supply_value(self.entitlement.pk, 'ZZZZ-9999-YYYY-0000', replace=True)

        after = Entitlement.objects.get(pk=self.entitlement.pk)
        self.assertEqual((after.activated_at, after.expires_at), (activated.activated_at, activated.expires_at))
        self.assertIsNone(after.revealed_at)
        self.assertEqual(after.hint, '0000')
        self.assertEqual(reveal(self.entitlement.pk)[1], 'ZZZZ-9999-YYYY-0000')

    def test_a_revoked_one_is_never_changed_not_even_as_a_reissue(self):
        revoke(self.entitlement.pk)

        for replace in (False, True):
            with self.assertRaises(EntitlementStateError) as caught:
                supply_value(self.entitlement.pk, LICENCE, replace=replace)
            self.assertEqual(caught.exception.code, 'entitlement_revoked')

    def test_a_bad_value_is_refused_before_anything_is_written(self):
        for bad in ('', '   ', 'x' * 2001):
            with self.assertRaises(InvalidEntitlementValue, msg=repr(bad[:10])):
                supply_value(self.entitlement.pk, bad)

        entitlement = Entitlement.objects.get(pk=self.entitlement.pk)
        self.assertEqual((entitlement.status, entitlement.value), ('pending', ''))

    def test_a_link_must_be_a_url(self):
        link = self.product('LNK')
        make_fulfillment(link, mode='supplied', kind='link')
        [pending] = self.entitlements(self.paid_order(link))

        for bad in ('download it from our site', 'javascript:alert(1)', 'ftp://x.example.com/f'):
            with self.assertRaises(InvalidEntitlementValue, msg=bad):
                supply_value(pending.pk, bad)
        supply_value(pending.pk, '  https://downloads.example.com/a  ')
        self.assertEqual(Entitlement.objects.get(pk=pending.pk).value, 'https://downloads.example.com/a')


@override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY_A])
class RevealTests(DeliveryFixture):
    def setUp(self):
        super().setUp()
        product = self.product()
        make_fulfillment(product, mode='supplied', kind='key')
        [self.entitlement] = self.entitlements(self.paid_order(product))

    def test_the_whole_key_comes_back_and_the_first_reveal_is_recorded(self):
        supply_value(self.entitlement.pk, LICENCE)
        first = timezone.now() - timedelta(hours=2)

        entitlement, value = reveal(self.entitlement.pk, now=first)

        self.assertEqual(value, LICENCE)
        self.assertEqual(entitlement.revealed_at, first)

    def test_a_second_reveal_never_overwrites_the_evidence(self):
        supply_value(self.entitlement.pk, LICENCE)
        first = timezone.now() - timedelta(hours=2)
        reveal(self.entitlement.pk, now=first)

        entitlement, value = reveal(self.entitlement.pk, now=timezone.now())

        self.assertEqual(value, LICENCE)
        self.assertEqual(entitlement.revealed_at, first)

    def test_nothing_can_be_revealed_before_it_is_supplied_or_after_it_is_revoked(self):
        with self.assertRaises(EntitlementStateError) as pending:
            reveal(self.entitlement.pk)
        supply_value(self.entitlement.pk, LICENCE)
        revoke(self.entitlement.pk)
        with self.assertRaises(EntitlementStateError) as revoked:
            reveal(self.entitlement.pk)

        self.assertEqual(pending.exception.code, 'entitlement_not_active')
        self.assertEqual(revoked.exception.code, 'entitlement_revoked')
        self.assertIsNone(Entitlement.objects.get(pk=self.entitlement.pk).revealed_at)

    def test_an_expired_entitlement_can_still_be_read(self):
        supply_value(self.entitlement.pk, LICENCE)
        Entitlement.objects.filter(pk=self.entitlement.pk).update(expires_at=timezone.now() - timedelta(days=1))

        self.assertEqual(reveal(self.entitlement.pk)[1], LICENCE)

    def test_a_link_is_revealed_as_it_is(self):
        link = self.product('LNK')
        make_fulfillment(link)
        [entitlement] = self.entitlements(self.paid_order(link))

        self.assertEqual(reveal(entitlement.pk)[1], LINK)


class RefundTests(DeliveryFixture):
    def setUp(self):
        super().setUp()
        self.product_ = self.product()
        make_fulfillment(self.product_, mode='supplied', kind='key', per_unit=True)
        self.order = self.paid_order(self.product_, quantity=2)

    def test_a_refund_revokes_everything_delivered_and_it_stays_revoked(self):
        for entitlement in self.entitlements(self.order):
            supply_value(entitlement.pk, f'KEY-{entitlement.pk}-ABCDEFGH')

        refund_order(self.order, self.manager)

        self.assertEqual(SalesOrder.objects.get(pk=self.order.pk).status, SalesOrder.REFUNDED)
        revoked = self.entitlements(self.order)
        self.assertEqual([e.status for e in revoked], ['revoked', 'revoked'])
        self.assertTrue(all(e.revoked_at for e in revoked))
        for entitlement in revoked:
            with self.assertRaises(EntitlementStateError):
                reveal(entitlement.pk)

    def test_the_revocation_and_the_refund_succeed_or_fail_together(self):
        with patch('core.services.orders.revoke_for_order', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                refund_order(self.order, self.manager)

        self.assertEqual(SalesOrder.objects.get(pk=self.order.pk).status, SalesOrder.PAID)
        self.assertTrue(all(e.status == 'pending' for e in self.entitlements(self.order)))

    def test_revoking_one_is_idempotent_and_leaves_the_others(self):
        first, second = self.entitlements(self.order)

        self.assertTrue(revoke(first.pk))
        self.assertFalse(revoke(first.pk))

        self.assertEqual([e.status for e in self.entitlements(self.order)], ['revoked', 'pending'])
        self.assertEqual(second.status, 'pending')


class DeliveryCompleteTests(DeliveryFixture):
    def test_an_order_with_nothing_to_deliver_is_not_complete(self):
        order = make_order(product=self.product(), status=SalesOrder.CONFIRMED)
        self.assertFalse(delivery_complete(order))

    def test_complete_only_when_every_line_has_an_active_delivery(self):
        keyed = self.product('KEYED')
        linked = self.product('LINKED')
        make_fulfillment(keyed, mode='supplied', kind='key')
        make_fulfillment(linked)
        order = make_order(product=keyed, status=SalesOrder.CONFIRMED)
        order.items.create(product=linked, quantity=1, unit_price=linked.unit_price, line_total=linked.unit_price)
        order.total_amount += linked.unit_price
        order.save()
        pay_order(order)

        self.assertFalse(delivery_complete(order))  # the key is still pending

        with override_settings(ENTITLEMENT_ENCRYPTION_KEYS=[KEY_A]):
            pending = Entitlement.objects.get(order_item__product=keyed)
            supply_value(pending.pk, LICENCE)
            self.assertTrue(delivery_complete(order))

            revoke(pending.pk)
            self.assertFalse(delivery_complete(order))  # something was withdrawn

    def test_a_line_that_delivers_nothing_keeps_it_incomplete(self):
        digital = self.product('DIGITAL')
        physical = make_product(sku='BOX', stock=5, category=self.category)
        make_fulfillment(digital)
        order = make_order(product=digital, status=SalesOrder.CONFIRMED)
        order.items.create(product=physical, quantity=1, unit_price=physical.unit_price,
                           line_total=physical.unit_price)
        order.total_amount += physical.unit_price
        order.save()
        pay_order(order)

        self.assertFalse(delivery_complete(order))
