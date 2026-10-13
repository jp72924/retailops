"""
Loading a storefront's catalogue from a file.

What matters: running it twice changes nothing the second time, a listing is published only to the
channel its file names (so stores with different assortments can share one RetailOps), the key it
issues is shown or written once and never discarded by a re-run, a bad file creates nothing, and
what it imports is genuinely sellable through the storefront API.
"""
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase
from rest_framework.test import APIClient

from api.tests.helpers import make_user, set_currency
from core.models import (
    InventoryMovement,
    Listing,
    ListingVariant,
    Product,
    ProductFulfillment,
    Role,
    SalesChannel,
)
from core.services.listings import published_listings
from api.storefront.provisioning import create_channel

LISTINGS = '/api/v1/storefront/catalog/listings/'
ORDERS = '/api/v1/storefront/orders/'


def catalog(**overrides):
    document = {
        'version': 1,
        'channel': {
            'slug': 'editorial', 'name': 'Trend Store Editorial',
            'trusts_email_verification': True, 'order_expiry_minutes': 15,
        },
        'listings': [
            {
                'slug': 'nova-productivity-pro', 'title': 'Nova Productivity Pro',
                'summary': 'A focused toolkit.', 'description': 'Long text.', 'badge': 'BEST SELLER',
                'display_type': 'license', 'category': 'Productivity',
                'features': ['Desktop + web apps', '12 months of updates'],
                'attributes': {'platform': 'Windows & macOS', 'activation': 'License key'},
                'variants': [
                    {
                        'sku': 'ED-NOVA-12M', 'label': '12 months', 'price': '79.00', 'compare_at_price': '99.00',
                        'kind': 'digital', 'track_inventory': False,
                        'fulfillment': {'mode': 'supplied', 'kind': 'key', 'duration_days': 365},
                    },
                    {
                        'sku': 'ED-NOVA-24M', 'label': '24 months', 'price': '129.00',
                        'kind': 'digital', 'track_inventory': False,
                        'fulfillment': {'mode': 'supplied', 'kind': 'key', 'duration_days': 730},
                    },
                ],
            },
            {
                'slug': 'orbit-remote-assist', 'title': 'Orbit Remote Assist', 'category': 'Services',
                'badge': 'LIMITED', 'display_type': 'service',
                'variants': [{
                    'sku': 'ED-ORBIT', 'price': '45.00', 'kind': 'service', 'track_inventory': True, 'stock': 5,
                    'fulfillment': {'mode': 'template', 'kind': 'instructions', 'template': 'We will contact you.'},
                }],
            },
        ],
    }
    document.update(overrides)
    return document


class ImportFixture(TestCase):
    def setUp(self):
        set_currency()
        self.admin = make_user(Role.ADMIN, is_staff=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, document, name='catalog.json'):
        path = self.dir / name
        path.write_text(json.dumps(document), encoding='utf-8')
        return path

    def run_import(self, document=None, **options):
        out = StringIO()
        path = options.pop('catalog', None) or self.write(document if document is not None else catalog())
        call_command('import_storefront_catalog', catalog=str(path), stdout=out, **options)
        return out.getvalue()

    def key_from(self, dev_vars):
        for line in Path(dev_vars).read_text(encoding='utf-8').splitlines():
            if line.startswith('RETAILOPS_CHANNEL_KEY='):
                return line.split('=', 1)[1]
        return None


class WhatItCreatesTests(ImportFixture):
    def test_it_creates_the_channel_listings_variants_products_and_stock(self):
        self.run_import()

        channel = SalesChannel.objects.get(slug='editorial')
        self.assertEqual((channel.trusts_email_verification, channel.order_expiry_minutes), (True, 15))
        listing = Listing.objects.get(slug='nova-productivity-pro')
        self.assertEqual(
            (listing.title, listing.badge, listing.display_type, listing.is_published, listing.category.name),
            ('Nova Productivity Pro', 'BEST SELLER', 'license', True, 'Productivity'),
        )
        self.assertEqual(listing.features, ['Desktop + web apps', '12 months of updates'])
        self.assertEqual(listing.attributes['platform'], 'Windows & macOS')
        variants = list(listing.variants.order_by('position'))
        self.assertEqual([v.label for v in variants], ['12 months', '24 months'])
        self.assertEqual([str(v.product.unit_price) for v in variants], ['79.00', '129.00'])
        self.assertEqual(str(variants[0].compare_at_price), '99.00')
        self.assertIsNone(variants[1].compare_at_price)
        orbit = Product.objects.get(sku='ED-ORBIT')
        self.assertEqual((orbit.kind, orbit.track_inventory, orbit.current_stock), ('service', True, 5))
        self.assertIsNone(Product.objects.get(sku='ED-NOVA-12M').current_stock)  # untracked

    def test_it_creates_the_fulfilments_the_file_describes(self):
        self.run_import()

        key = ProductFulfillment.objects.get(product__sku='ED-NOVA-12M')
        self.assertEqual((key.mode, key.entitlement_kind, key.duration_days), ('supplied', 'key', 365))
        instructions = ProductFulfillment.objects.get(product__sku='ED-ORBIT')
        self.assertEqual((instructions.mode, instructions.template), ('template', 'We will contact you.'))

    def test_a_listing_is_published_to_the_files_channel_alone(self):
        self.run_import()
        other, _key = create_channel('Other shop', 'other-shop', self.admin)
        editorial = SalesChannel.objects.get(slug='editorial')

        self.assertEqual(
            sorted(published_listings(editorial).values_list('slug', flat=True)),
            ['nova-productivity-pro', 'orbit-remote-assist'],
        )
        self.assertEqual(list(published_listings(other)), [])

    def test_two_stores_can_share_a_category_and_stay_separate(self):
        self.run_import()
        second = catalog(
            channel={'slug': 'catalog', 'name': 'Trend Store Catalog'},
            listings=[{
                'slug': 'focus-office-pro', 'title': 'FOCUS OFFICE PRO', 'category': 'Productivity',
                'variants': [{'sku': 'CT-FOCUS', 'price': '89.00', 'kind': 'digital', 'track_inventory': False}],
            }],
        )
        self.run_import(second)

        editorial = SalesChannel.objects.get(slug='editorial')
        shop = SalesChannel.objects.get(slug='catalog')
        self.assertEqual(
            list(published_listings(shop).values_list('slug', flat=True)), ['focus-office-pro'],
        )
        self.assertNotIn('focus-office-pro', published_listings(editorial).values_list('slug', flat=True))
        self.assertEqual(Listing.objects.get(slug='focus-office-pro').category.name, 'Productivity')


class RunningItAgainTests(ImportFixture):
    def counts(self):
        return (
            SalesChannel.objects.count(), Listing.objects.count(), ListingVariant.objects.count(),
            Product.objects.count(), ProductFulfillment.objects.count(),
            InventoryMovement.objects.count(),
        )

    def test_a_second_run_creates_nothing(self):
        self.run_import()
        first = self.counts()

        self.run_import()

        self.assertEqual(self.counts(), first)

    def test_a_second_run_applies_what_changed(self):
        self.run_import()
        changed = catalog()
        changed['listings'][0]['title'] = 'Nova Productivity Pro 2'
        changed['listings'][0]['variants'][0]['price'] = '69.00'
        changed['listings'][1]['variants'][0]['stock'] = 8
        changed['channel']['order_expiry_minutes'] = 30

        self.run_import(changed)

        self.assertEqual(Listing.objects.get(slug='nova-productivity-pro').title, 'Nova Productivity Pro 2')
        self.assertEqual(str(Product.objects.get(sku='ED-NOVA-12M').unit_price), '69.00')
        self.assertEqual(Product.objects.get(sku='ED-ORBIT').current_stock, 8)
        self.assertEqual(SalesChannel.objects.get(slug='editorial').order_expiry_minutes, 30)

    def test_stock_is_set_to_the_files_level_in_either_direction(self):
        self.run_import()
        lower = catalog()
        lower['listings'][1]['variants'][0]['stock'] = 2

        self.run_import(lower)

        orbit = Product.objects.get(sku='ED-ORBIT')
        self.assertEqual(orbit.current_stock, 2)
        self.assertTrue(
            InventoryMovement.objects.filter(
                product=orbit, movement_type=InventoryMovement.ADJUSTMENT, quantity=-3,
            ).exists()
        )

    def test_a_listing_dropped_from_the_file_is_left_alone(self):
        self.run_import()
        trimmed = catalog()
        trimmed['listings'] = trimmed['listings'][:1]

        self.run_import(trimmed)

        self.assertTrue(Listing.objects.filter(slug='orbit-remote-assist', is_published=True).exists())


class TheKeyTests(ImportFixture):
    def test_the_key_is_written_to_dev_vars_without_a_bom(self):
        dev_vars = self.dir / 'app' / '.dev.vars'

        self.run_import(dev_vars=str(dev_vars), base_url='http://127.0.0.1:8000')

        raw = dev_vars.read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\r', raw)
        text = raw.decode('utf-8')
        self.assertIn('RETAILOPS_BASE_URL=http://127.0.0.1:8000\n', text)
        self.assertTrue(self.key_from(dev_vars))

    def test_the_key_that_was_written_authenticates_the_storefront_api(self):
        dev_vars = self.dir / '.dev.vars'
        self.run_import(dev_vars=str(dev_vars))

        client = APIClient()
        response = client.get(LISTINGS, HTTP_AUTHORIZATION=f'ChannelKey {self.key_from(dev_vars)}')

        self.assertEqual(response.status_code, 200)
        slugs = [row['slug'] for row in response.data['results']]
        self.assertEqual(sorted(slugs), ['nova-productivity-pro', 'orbit-remote-assist'])
        first = response.data['results'][0]
        self.assertEqual(first['features'], ['Desktop + web apps', '12 months of updates'])

    def test_a_rerun_keeps_the_key_and_other_variables(self):
        dev_vars = self.dir / '.dev.vars'
        self.run_import(dev_vars=str(dev_vars))
        key = self.key_from(dev_vars)
        with dev_vars.open('a', encoding='utf-8') as handle:
            handle.write('WEBHOOK_SECRET=whsec_keep\n')

        self.run_import(dev_vars=str(dev_vars), base_url='http://127.0.0.1:9999')

        text = dev_vars.read_text(encoding='utf-8')
        self.assertEqual(self.key_from(dev_vars), key)
        self.assertIn('WEBHOOK_SECRET=whsec_keep', text)
        self.assertIn('RETAILOPS_BASE_URL=http://127.0.0.1:9999', text)

    def test_a_new_key_replaces_the_one_in_the_file_and_the_old_still_works(self):
        dev_vars = self.dir / '.dev.vars'
        self.run_import(dev_vars=str(dev_vars))
        old = self.key_from(dev_vars)

        self.run_import(dev_vars=str(dev_vars), new_key=True)

        new = self.key_from(dev_vars)
        self.assertNotEqual(new, old)
        client = APIClient()
        for key in (old, new):  # a channel may hold several active keys, so a store can rotate
            self.assertEqual(client.get(LISTINGS, HTTP_AUTHORIZATION=f'ChannelKey {key}').status_code, 200)

    def test_without_a_file_the_key_is_printed_once(self):
        output = self.run_import()

        self.assertIn('CHANNEL KEY', output)
        self.assertNotIn('CHANNEL KEY', self.run_import())  # the second run issues no key


class WhatItImportsIsSellableTests(ImportFixture):
    def test_an_imported_sku_can_be_ordered_through_the_storefront_api(self):
        dev_vars = self.dir / '.dev.vars'
        self.run_import(dev_vars=str(dev_vars))
        auth = {'HTTP_AUTHORIZATION': f'ChannelKey {self.key_from(dev_vars)}'}

        response = APIClient().post(ORDERS, {
            'client_reference': 'WEB-1',
            'customer': {'external_ref': 'u1', 'email': 'ana@example.com', 'first_name': 'Ana', 'last_name': 'P'},
            'items': [{'sku': 'ED-ORBIT', 'quantity': 2}, {'sku': 'ED-NOVA-12M', 'quantity': 1}],
            'fulfillment': 'digital',
        }, format='json', **auth)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Product.objects.get(sku='ED-ORBIT').current_stock, 3)  # the tracked one was reserved
        self.assertEqual(response.data['items'][1]['listing_slug'], 'nova-productivity-pro')


class DryRunAndProblemsTests(ImportFixture):
    def test_a_dry_run_changes_nothing_and_writes_no_file(self):
        dev_vars = self.dir / '.dev.vars'

        output = self.run_import(dry_run=True, dev_vars=str(dev_vars))

        self.assertIn('Would import', output)
        self.assertEqual((SalesChannel.objects.count(), Listing.objects.count(), Product.objects.count()), (0, 0, 0))
        self.assertFalse(dev_vars.exists())

    def test_a_bad_file_names_every_problem_and_creates_nothing(self):
        bad = catalog()
        bad['listings'][0]['slug'] = ''
        bad['listings'][0]['variants'][0]['price'] = 'free'
        bad['listings'][0]['variants'][1]['sku'] = 'ED-NOVA-12M'  # twice
        bad['listings'][1]['variants'][0]['kind'] = 'gadget'

        with self.assertRaises(CommandError) as raised:
            self.run_import(bad)

        message = str(raised.exception)
        for fragment in ('"slug" is required', 'not a price', 'appears twice', 'kind must be one of'):
            self.assertIn(fragment, message)
        self.assertEqual((SalesChannel.objects.count(), Listing.objects.count(), Product.objects.count()), (0, 0, 0))

    def test_a_licence_key_cannot_be_fixed_text(self):
        bad = catalog()
        bad['listings'][0]['variants'][0]['fulfillment'] = {
            'mode': 'template', 'kind': 'key', 'template': 'SAME-KEY-FOR-ALL',
        }

        with self.assertRaises(CommandError):
            self.run_import(bad)

        self.assertEqual((SalesChannel.objects.count(), Product.objects.count()), (0, 0))  # all rolled back

    def test_a_sku_that_belongs_to_another_stores_listing_is_refused(self):
        self.run_import()
        clash = catalog(
            channel={'slug': 'catalog', 'name': 'Trend Store Catalog'},
            listings=[{
                'slug': 'something-else', 'title': 'Something else', 'category': 'Productivity',
                'variants': [{'sku': 'ED-NOVA-12M', 'price': '10.00', 'kind': 'digital', 'track_inventory': False}],
            }],
        )

        with self.assertRaises(CommandError) as raised:
            self.run_import(clash)

        self.assertIn('already belongs to listing', str(raised.exception))
        self.assertFalse(SalesChannel.objects.filter(slug='catalog').exists())  # nothing of the second run stays
        self.assertFalse(Listing.objects.filter(slug='something-else').exists())

    def test_a_missing_or_malformed_file_is_a_clear_error(self):
        with self.assertRaises(CommandError):
            call_command('import_storefront_catalog', catalog=str(self.dir / 'nope.json'), stdout=StringIO())
        broken = self.dir / 'broken.json'
        broken.write_text('{not json', encoding='utf-8')
        with self.assertRaises(CommandError):
            call_command('import_storefront_catalog', catalog=str(broken), stdout=StringIO())


class TheDevelopmentDatabaseTests(ImportFixture):
    def test_it_refuses_to_write_to_the_default_development_database(self):
        with patch('core.management.commands.import_storefront_catalog.refuses_default_database', return_value=True):
            with self.assertRaises(CommandError) as raised:
                self.run_import()

        self.assertIn('db.sqlite3', str(raised.exception))
        self.assertEqual(SalesChannel.objects.count(), 0)

    def test_the_override_is_explicit(self):
        with patch('core.management.commands.import_storefront_catalog.refuses_default_database', return_value=True):
            self.run_import(allow_default_db=True)

        self.assertEqual(SalesChannel.objects.count(), 1)
