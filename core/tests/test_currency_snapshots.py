"""
Payments keep the currency and exchange rate they were recorded with.

Covers the value object and its fingerprint, snapshot interning and
immutability, freezing on payment, the derived order figures, rate
provenance on SystemSettings, the primary currency lock, and the backfill
migration.
"""
import importlib
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase

from core.models import CurrencySnapshot, Payment, Role, SalesOrder, SystemSettings
from core.services.bcv import update_secondary_exchange_rate
from core.services.currency import (
    BASIS_LIVE,
    BASIS_RECORDED,
    RATE_SOURCE_BACKFILLED,
    RATE_SOURCE_FETCHED,
    RATE_SOURCE_MANUAL,
    RATE_SOURCE_UNKNOWN,
    CurrencyContext,
    order_amounts,
    secondary_amount,
)
from api.tests.helpers import make_order, make_payment, make_user
from api.tests.helpers import set_currency as configure

backfill_migration = importlib.import_module(
    'core.migrations.0022_backfill_payment_currency_snapshots'
)


def context(**overrides):
    fields = {
        'currency_code': 'USD',
        'currency_symbol': '$',
        'decimal_places': 2,
        'secondary_currency_enabled': True,
        'secondary_currency_code': 'VES',
        'secondary_currency_symbol': 'Bs.',
        'secondary_decimal_places': 2,
        'secondary_exchange_rate': Decimal('50'),
        'rate_as_of': None,
        'rate_source': RATE_SOURCE_MANUAL,
    }
    fields.update(overrides)
    return CurrencyContext.build(**fields)


class CurrencyContextTests(TestCase):
    def test_equal_rates_in_different_forms_hash_the_same(self):
        # An in-memory Decimal from a form and the stored 8-decimal value
        # must intern to one row.
        self.assertEqual(
            context(secondary_exchange_rate=Decimal('36.5')).fingerprint(),
            context(secondary_exchange_rate='36.50000000').fingerprint(),
        )

    def test_every_stored_field_changes_the_fingerprint(self):
        base = context().fingerprint()
        variants = {
            'currency_code': 'EUR',
            'currency_symbol': 'US$',
            'decimal_places': 0,
            'secondary_currency_enabled': False,
            'secondary_currency_code': 'VED',
            'secondary_currency_symbol': 'VES',
            'secondary_decimal_places': 4,
            'secondary_exchange_rate': Decimal('50.00000001'),
            'rate_as_of': datetime(2026, 9, 15, 20, 5, tzinfo=dt_timezone.utc),
            'rate_source': RATE_SOURCE_FETCHED,
            'conversion_rule': 'half_up_v2',
        }
        for field, value in variants.items():
            with self.subTest(field=field):
                self.assertNotEqual(context(**{field: value}).fingerprint(), base)

    def test_codes_are_case_insensitive_but_symbols_keep_their_spacing(self):
        self.assertEqual(
            context(currency_code='usd').fingerprint(),
            context(currency_code='USD').fingerprint(),
        )
        # The display concatenates symbol and amount, so a trailing space is
        # meaningful and must not be normalized away.
        self.assertNotEqual(
            context(secondary_currency_symbol='Bs. ').fingerprint(),
            context(secondary_currency_symbol='Bs.').fingerprint(),
        )

    def test_rate_as_of_is_compared_as_an_instant(self):
        utc = datetime(2026, 9, 15, 20, 0, tzinfo=dt_timezone.utc)
        caracas = utc.astimezone(dt_timezone(timedelta(hours=-4)))
        self.assertEqual(
            context(rate_as_of=utc).fingerprint(),
            context(rate_as_of=caracas).fingerprint(),
        )

    def test_secondary_amount_rounds_half_up_to_the_secondary_decimals(self):
        ctx = context(secondary_exchange_rate=Decimal('0.5'))
        self.assertEqual(secondary_amount(Decimal('0.03'), ctx), Decimal('0.02'))
        self.assertEqual(
            secondary_amount(Decimal('10.00'), context(secondary_decimal_places=0,
                                                       secondary_exchange_rate=Decimal('842.215'))),
            Decimal('8422'),
        )

    def test_secondary_amount_is_none_without_a_secondary_currency(self):
        self.assertIsNone(secondary_amount(Decimal('10'), context(secondary_currency_enabled=False)))

    def test_backfill_migration_hashes_like_the_service(self):
        # The migration inlines its own copy of the canonical form. If the two
        # drift, backfilled rows stop interning with identical live ones.
        rows = [
            None,
            SimpleNamespace(
                currency_code='usd', currency_symbol='$', decimal_places=2,
                secondary_currency_enabled=True, secondary_currency_code='ves',
                secondary_currency_symbol='Bs.', secondary_decimal_places=2,
                secondary_exchange_rate=Decimal('36.5'),
            ),
        ]
        for row in rows:
            with self.subTest(row=row):
                fields = backfill_migration.backfilled_fields(row)
                self.assertEqual(
                    backfill_migration.fingerprint_v1(fields),
                    CurrencyContext.build(**fields).fingerprint(),
                )


class CurrencySnapshotModelTests(TestCase):
    def test_interning_the_same_context_returns_the_same_row(self):
        first = CurrencySnapshot.objects.intern(context())
        second = CurrencySnapshot.objects.intern(context(secondary_exchange_rate='50.00000000'))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CurrencySnapshot.objects.count(), 1)

    def test_a_changed_rate_creates_a_new_row(self):
        first = CurrencySnapshot.objects.intern(context())
        second = CurrencySnapshot.objects.intern(context(secondary_exchange_rate=Decimal('51')))
        self.assertNotEqual(first.pk, second.pk)

    def test_snapshots_are_immutable(self):
        snapshot = CurrencySnapshot.objects.intern(context())
        snapshot.secondary_exchange_rate = Decimal('99')
        with self.assertRaises(ValueError):
            snapshot.save()
        with self.assertRaises(ValueError):
            snapshot.delete()

    def test_a_referenced_snapshot_cannot_be_deleted(self):
        configure()
        payment = make_payment()
        with self.assertRaises(ProtectedError):
            CurrencySnapshot.objects.filter(pk=payment.currency_snapshot_id).delete()

    def test_round_trip_through_the_database_preserves_the_fingerprint(self):
        snapshot = CurrencySnapshot.objects.intern(context(
            rate_as_of=datetime(2026, 9, 15, 20, 5, 11, 123456, tzinfo=dt_timezone.utc),
        ))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.as_context().fingerprint(), snapshot.fingerprint)


class PaymentFreezeTests(TestCase):
    def setUp(self):
        configure()

    def test_a_payment_records_the_live_configuration(self):
        payment = make_payment(amount='10.00')
        snapshot = payment.currency_snapshot
        self.assertEqual(snapshot.currency_code, 'USD')
        self.assertEqual(snapshot.secondary_currency_code, 'VES')
        self.assertEqual(snapshot.secondary_exchange_rate, Decimal('50'))

    def test_a_recorded_payment_ignores_later_settings_changes(self):
        payment = make_payment(amount='10.00')

        configure(
            currency_symbol='US$',
            secondary_currency_symbol='VES',
            secondary_decimal_places=0,
            secondary_exchange_rate=Decimal('80'),
        )

        payment = Payment.objects.select_related('currency_snapshot').get(pk=payment.pk)
        snapshot = payment.currency_snapshot
        self.assertEqual(snapshot.currency_symbol, '$')
        self.assertEqual(snapshot.secondary_currency_symbol, 'Bs.')
        self.assertEqual(snapshot.secondary_decimal_places, 2)
        self.assertEqual(secondary_amount(payment.amount, snapshot), Decimal('500.00'))

    def test_payments_under_one_configuration_share_a_snapshot(self):
        first = make_payment()
        second = make_payment()
        self.assertEqual(first.currency_snapshot_id, second.currency_snapshot_id)
        self.assertEqual(CurrencySnapshot.objects.count(), 1)

    def test_an_explicit_snapshot_is_kept(self):
        chosen = CurrencySnapshot.objects.intern(context(secondary_exchange_rate=Decimal('42')))
        payment = make_payment(currency_snapshot=chosen)
        self.assertEqual(payment.currency_snapshot_id, chosen.pk)

    def test_the_database_refuses_a_payment_without_a_snapshot(self):
        payment = make_payment()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.filter(pk=payment.pk).update(currency_snapshot=None)


class OrderAmountsTests(TestCase):
    def setUp(self):
        configure()
        self.user = make_user(Role.STAFF)

    def _order(self, total='100.00'):
        return make_order(status=SalesOrder.CONFIRMED, total=Decimal(total), user=self.user)

    def _amounts(self, order):
        return order_amounts(SalesOrder.objects.get(pk=order.pk))

    def test_an_unpaid_order_follows_the_live_rate(self):
        order = self._order()
        self.assertEqual(self._amounts(order).basis, BASIS_LIVE)
        self.assertEqual(self._amounts(order).total_secondary, Decimal('5000.00'))

        configure(secondary_exchange_rate=Decimal('60'))
        self.assertEqual(self._amounts(order).total_secondary, Decimal('6000.00'))

    def test_the_paid_portion_is_frozen_while_the_balance_follows_the_rate(self):
        order = self._order()
        make_payment(order=order, user=self.user, amount='40.00')

        configure(secondary_exchange_rate=Decimal('60'))
        amounts = self._amounts(order)

        self.assertEqual(amounts.paid_secondary, Decimal('2000.00'))        # 40 x 50
        self.assertEqual(amounts.outstanding_secondary, Decimal('3600.00'))  # 60 x 60
        self.assertEqual(amounts.total_secondary, Decimal('5600.00'))
        self.assertEqual(amounts.basis, BASIS_RECORDED)

    def test_a_fully_paid_order_no_longer_moves(self):
        order = self._order()
        make_payment(order=order, user=self.user, amount='100.00')

        configure(secondary_exchange_rate=Decimal('80'), currency_symbol='US$')
        amounts = self._amounts(order)

        self.assertEqual(amounts.total_secondary, Decimal('5000.00'))
        self.assertEqual(amounts.currency.currency_symbol, '$')

    def test_each_payment_keeps_its_own_rate(self):
        order = self._order()
        make_payment(order=order, user=self.user, amount='40.00')
        configure(secondary_exchange_rate=Decimal('70'))
        make_payment(order=order, user=self.user, amount='60.00')

        amounts = self._amounts(order)
        self.assertEqual(amounts.paid_secondary, Decimal('6200.00'))  # 40x50 + 60x70
        self.assertEqual(amounts.total_secondary, Decimal('6200.00'))

    def test_payments_awaiting_review_are_not_paid(self):
        order = self._order()
        make_payment(order=order, user=self.user, amount='100.00', status=Payment.PENDING_REVIEW)
        amounts = self._amounts(order)
        self.assertEqual(amounts.paid, Decimal('0'))
        self.assertEqual(amounts.basis, BASIS_LIVE)

    def test_a_payment_without_a_secondary_currency_leaves_the_paid_value_unknown(self):
        configure(secondary_currency_enabled=False)
        order = self._order()
        make_payment(order=order, user=self.user, amount='100.00')

        configure(secondary_currency_enabled=True)
        amounts = self._amounts(order)
        self.assertIsNone(amounts.paid_secondary)
        self.assertIsNone(amounts.total_secondary)

    def test_payments_in_different_secondary_currencies_are_not_summed(self):
        order = self._order()
        make_payment(order=order, user=self.user, amount='40.00')
        configure(secondary_currency_code='VED')
        make_payment(order=order, user=self.user, amount='60.00')
        self.assertIsNone(self._amounts(order).paid_secondary)


class RateProvenanceTests(TestCase):
    def setUp(self):
        self.settings = configure()
        SystemSettings.objects.filter(pk=1).update(
            secondary_rate_updated_at=None, secondary_rate_source=RATE_SOURCE_UNKNOWN,
        )

    def _reload(self):
        return SystemSettings.objects.get(pk=1)

    def test_a_manual_rate_change_is_stamped(self):
        settings = self._reload()
        settings.secondary_exchange_rate = Decimal('60')
        settings.save()

        settings = self._reload()
        self.assertEqual(settings.secondary_rate_source, RATE_SOURCE_MANUAL)
        self.assertIsNotNone(settings.secondary_rate_updated_at)

    def test_a_partial_save_of_the_rate_is_stamped_too(self):
        # manage.py init saves with update_fields.
        settings = self._reload()
        settings.secondary_exchange_rate = Decimal('60')
        settings.save(update_fields=('secondary_exchange_rate',))

        settings = self._reload()
        self.assertEqual(settings.secondary_rate_source, RATE_SOURCE_MANUAL)
        self.assertIsNotNone(settings.secondary_rate_updated_at)

    def test_an_unchanged_rate_is_not_stamped(self):
        settings = self._reload()
        settings.secondary_exchange_rate = Decimal('50.00000000')
        settings.currency_symbol = 'US$'
        settings.save()

        settings = self._reload()
        self.assertEqual(settings.secondary_rate_source, RATE_SOURCE_UNKNOWN)
        self.assertIsNone(settings.secondary_rate_updated_at)

    def test_a_fetched_rate_is_marked_fetched(self):
        with patch('core.services.bcv.fetch_rate', return_value=Decimal('90')):
            update_secondary_exchange_rate(self._reload())

        settings = self._reload()
        self.assertEqual(settings.secondary_rate_source, RATE_SOURCE_FETCHED)
        self.assertEqual(settings.secondary_exchange_rate, Decimal('90'))

    def test_the_payment_records_where_its_rate_came_from(self):
        settings = self._reload()
        settings.secondary_exchange_rate = Decimal('60')
        settings.save()

        payment = make_payment()
        self.assertEqual(payment.currency_snapshot.rate_source, RATE_SOURCE_MANUAL)
        self.assertEqual(
            payment.currency_snapshot.rate_as_of, self._reload().secondary_rate_updated_at
        )


class PrimaryCurrencyLockTests(TestCase):
    def setUp(self):
        configure()

    def test_the_code_can_change_before_any_transaction(self):
        settings = SystemSettings.get()
        settings.currency_code = 'EUR'
        settings.full_clean()

    def test_the_code_is_locked_once_an_order_exists(self):
        make_order()
        settings = SystemSettings.get()
        settings.currency_code = 'EUR'
        with self.assertRaises(ValidationError) as caught:
            settings.full_clean()
        self.assertIn('currency_code', caught.exception.message_dict)

    def test_symbol_and_case_changes_stay_allowed(self):
        make_order()
        settings = SystemSettings.get()
        settings.currency_code = 'usd'
        settings.currency_symbol = 'US$'
        settings.full_clean()


class BackfillMigrationTests(TransactionTestCase):
    """0022 gives every pre-existing payment an approximate snapshot."""

    before = [('core', '0021_currency_snapshot')]
    after = [('core', '0022_backfill_payment_currency_snapshots')]

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.before)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def _seed_payments(self, apps, count):
        User = apps.get_model('core', 'User')
        Customer = apps.get_model('core', 'Customer')
        SalesOrder = apps.get_model('core', 'SalesOrder')
        Payment = apps.get_model('core', 'Payment')
        SystemSettings = apps.get_model('core', 'SystemSettings')

        SystemSettings.objects.create(
            pk=1, currency_code='USD', currency_symbol='$', decimal_places=2,
            secondary_currency_enabled=True, secondary_currency_code='VES',
            secondary_currency_symbol='Bs.', secondary_decimal_places=2,
            secondary_exchange_rate=Decimal('36.5'),
        )
        user = User.objects.create(email='legacy@example.com', first_name='L', last_name='U')
        customer = Customer.objects.create(
            first_name='C', last_name='U', email='legacy-customer@example.com',
        )
        for index in range(count):
            order = SalesOrder.objects.create(
                order_number=f'SO-LEGACY-{index}', customer=customer, created_by=user,
                status='paid', total_amount=Decimal('10.00'),
            )
            Payment.objects.create(
                payment_number=f'PAY-LEGACY-{index}', sales_order=order,
                amount=Decimal('10.00'), payment_method='cash', recorded_by=user,
            )

    def test_existing_payments_get_one_approximate_snapshot(self):
        before_apps = self.executor.loader.project_state(self.before).apps
        self._seed_payments(before_apps, 3)

        apps = self._migrate(self.after)
        Payment = apps.get_model('core', 'Payment')
        CurrencySnapshot = apps.get_model('core', 'CurrencySnapshot')

        snapshot = CurrencySnapshot.objects.get()
        self.assertEqual(snapshot.rate_source, RATE_SOURCE_BACKFILLED)
        self.assertEqual(snapshot.secondary_exchange_rate, Decimal('36.5'))
        self.assertIsNone(snapshot.rate_as_of)
        self.assertFalse(Payment.objects.filter(currency_snapshot__isnull=True).exists())
        self.assertEqual(Payment.objects.filter(currency_snapshot=snapshot).count(), 3)

        # Re-running changes nothing.
        backfill_migration.backfill(apps, None)
        self.assertEqual(CurrencySnapshot.objects.count(), 1)

        # And it reverses cleanly.
        apps = self._migrate(self.before)
        self.assertTrue(
            apps.get_model('core', 'Payment').objects.filter(currency_snapshot__isnull=True).count() == 3
        )
        self.assertFalse(apps.get_model('core', 'CurrencySnapshot').objects.exists())

    def test_a_database_without_payments_gets_no_snapshot(self):
        apps = self._migrate(self.after)
        self.assertFalse(apps.get_model('core', 'CurrencySnapshot').objects.exists())
