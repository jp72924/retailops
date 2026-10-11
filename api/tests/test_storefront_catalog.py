"""
The catalogue a sales channel reads.

What matters here: only published listings with something on sale are visible,
the response never carries a stock number, prices come from the product,
and a listing page costs the same number of queries however many listings it
holds.
"""
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Listing, ListingVariant, Product, ProductCategory, RecipientProfile, Role
from api.tests.helpers import (
    channel_auth,
    make_category,
    make_channel,
    make_listing,
    make_product,
    make_user,
    set_currency,
)

LISTINGS = '/api/v1/storefront/catalog/listings/'
CATEGORIES = '/api/v1/storefront/catalog/categories/'
INSTRUCTIONS = '/api/v1/storefront/payment-instructions/'

LISTING_KEYS = {
    'slug', 'title', 'summary', 'badge', 'display_type', 'category', 'image_url',
    'from_price', 'availability', 'variants',
}
VARIANT_KEYS = {'sku', 'label', 'kind', 'price', 'compare_at_price', 'availability'}


def _all_keys(value):
    """Every object key anywhere in a decoded JSON document."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _all_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _all_keys(inner)


class CatalogFixture(APITestCase):
    def setUp(self):
        cache.clear()
        set_currency()
        _channel, raw_key = make_channel()
        self.auth = channel_auth(raw_key)
        self.software = make_category('Software')

    def get(self, path, **params):
        return self.client.get(path, params, **self.auth)

    def slugs(self, **params):
        response = self.get(LISTINGS, **params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return [row['slug'] for row in response.data['results']]

    def product(self, sku, **attrs):
        attrs.setdefault('category', self.software)
        attrs.setdefault('stock', 10)
        return make_product(sku=sku, **attrs)


class VisibilityTests(CatalogFixture):
    def test_an_unpublished_listing_is_neither_listed_nor_retrievable(self):
        make_listing(self.product('A-1'), slug='draft', published=False)
        make_listing(self.product('B-1'), slug='live')

        self.assertEqual(self.slugs(), ['live'])
        self.assertEqual(self.get(f'{LISTINGS}draft/').status_code, 404)
        self.assertEqual(self.get(f'{LISTINGS}live/').status_code, 200)

    def test_a_listing_with_no_variants_is_hidden(self):
        make_listing(self.product('A-1'), slug='live')
        Listing.objects.create(slug='empty', title='Empty', category=self.software, is_published=True)

        self.assertEqual(self.slugs(), ['live'])
        self.assertEqual(self.get(f'{LISTINGS}empty/').status_code, 404)

    def test_a_listing_whose_products_are_all_inactive_is_hidden(self):
        make_listing(self.product('A-1', is_active=False), slug='withdrawn')

        self.assertEqual(self.slugs(), [])
        self.assertEqual(self.get(f'{LISTINGS}withdrawn/').status_code, 404)

    def test_an_inactive_variant_is_left_out_not_shown_as_sold_out(self):
        live = self.product('A-12M', unit_price='100.00')
        withdrawn = self.product('A-24M', unit_price='10.00', is_active=False)
        make_listing(live, withdrawn, slug='license')

        row = self.get(f'{LISTINGS}license/').data
        self.assertEqual([v['sku'] for v in row['variants']], ['A-12M'])
        self.assertEqual(row['from_price'], '100.00')  # not the withdrawn 10.00


class ContractTests(CatalogFixture):
    def test_the_fields_are_exactly_the_documented_ones(self):
        make_listing(self.product('A-1'), slug='one')

        listed = self.get(LISTINGS).data['results'][0]
        detail = self.get(f'{LISTINGS}one/').data

        self.assertEqual(set(listed), LISTING_KEYS)
        self.assertEqual(set(listed['variants'][0]), VARIANT_KEYS)
        self.assertEqual(set(listed['category']), {'id', 'name'})
        self.assertEqual(set(detail), LISTING_KEYS | {'description', 'features', 'attributes', 'currency'})

    def test_no_response_carries_a_stock_figure(self):
        product = self.product('A-1', stock=1234, low_stock_threshold=5)
        unlimited = self.product('B-1', stock=4321, track_inventory=False)
        make_listing(product, unlimited, slug='one')

        for path in (LISTINGS, f'{LISTINGS}one/', CATEGORIES):
            response = self.get(path)
            self.assertNotIn('1234', response.content.decode(), path)
            self.assertNotIn('4321', response.content.decode(), path)
            self.assertEqual([key for key in _all_keys(response.json()) if 'stock' in key], [], path)

    def test_availability_is_a_label_per_variant_and_the_best_for_the_listing(self):
        plenty = self.product('PLENTY', stock=10, low_stock_threshold=2)
        low = self.product('LOW', stock=2, low_stock_threshold=2)
        out = self.product('OUT', stock=0)
        unlimited = self.product('UNLIM', stock=0, track_inventory=False, kind=Product.DIGITAL)
        make_listing(plenty, low, out, unlimited, slug='mixed')
        low_only = self.product('LOW-ONLY', stock=1, low_stock_threshold=2)
        out_only = self.product('OUT-ONLY', stock=0)
        make_listing(low_only, out_only, slug='scarce')
        make_listing(self.product('GONE', stock=0), slug='gone')

        mixed = self.get(f'{LISTINGS}mixed/').data
        by_sku = {v['sku']: v['availability'] for v in mixed['variants']}
        self.assertEqual(by_sku, {
            'PLENTY': 'in_stock', 'LOW': 'low_stock', 'OUT': 'out_of_stock', 'UNLIM': 'unlimited',
        })
        self.assertEqual(mixed['availability'], 'unlimited')
        self.assertEqual(self.get(f'{LISTINGS}scarce/').data['availability'], 'low_stock')
        self.assertEqual(self.get(f'{LISTINGS}gone/').data['availability'], 'out_of_stock')

    def test_the_price_comes_from_the_product(self):
        product = self.product('A-1', unit_price='49.00')
        make_listing(product, slug='one')
        self.assertEqual(self.get(f'{LISTINGS}one/').data['variants'][0]['price'], '49.00')

        Product.objects.filter(pk=product.pk).update(unit_price='55.50')
        row = self.get(f'{LISTINGS}one/').data
        self.assertEqual(row['variants'][0]['price'], '55.50')
        self.assertEqual(row['from_price'], '55.50')

    def test_compare_at_price_is_shown_only_while_it_is_higher(self):
        product = self.product('A-1', unit_price='49.00')
        make_listing(product, slug='one')
        variant = ListingVariant.objects.get(product=product)

        variant.compare_at_price = '79.00'
        variant.save()
        self.assertEqual(self.get(f'{LISTINGS}one/').data['variants'][0]['compare_at_price'], '79.00')

        # The price rises past it: a struck-through price below the price is nonsense.
        Product.objects.filter(pk=product.pk).update(unit_price='79.00')
        self.assertIsNone(self.get(f'{LISTINGS}one/').data['variants'][0]['compare_at_price'])

    def test_a_detail_carries_its_extras_and_orders_variants_by_position(self):
        first = self.product('A-12M', unit_price='60.00')
        second = self.product('A-24M', unit_price='100.00')
        make_listing(
            second, first, slug='one', features=['Fast', 'Safe'],
            attributes={'platform': 'Windows', 'devices': 3}, badge='Popular',
            display_type='license', summary='Short.',
        )

        data = self.get(f'{LISTINGS}one/').data

        self.assertEqual([v['sku'] for v in data['variants']], ['A-24M', 'A-12M'])
        self.assertEqual(data['features'], ['Fast', 'Safe'])
        self.assertEqual(data['attributes'], {'platform': 'Windows', 'devices': 3})
        self.assertEqual((data['badge'], data['display_type'], data['summary']), ('Popular', 'license', 'Short.'))
        self.assertEqual(data['from_price'], '60.00')

    def test_an_empty_description_and_image_fall_back_to_the_product(self):
        product = self.product('A-1', description='From the product.')
        make_listing(product, slug='bare')
        make_listing(
            self.product('B-1', description='Ignored.'), slug='own',
            description='Own copy.', external_image_url='https://cdn.example.com/own.png',
        )

        bare = self.get(f'{LISTINGS}bare/').data
        own = self.get(f'{LISTINGS}own/').data

        self.assertEqual(bare['description'], 'From the product.')
        self.assertEqual(bare['image_url'], 'https://cdn.example.com/product.png')
        self.assertEqual(own['description'], 'Own copy.')
        self.assertEqual(own['image_url'], 'https://cdn.example.com/own.png')

    def test_an_uploaded_image_is_served_as_an_absolute_url(self):
        listing = make_listing(self.product('A-1'), slug='pic')
        Listing.objects.filter(pk=listing.pk).update(image='listings/2026/10/pic.png')

        url = self.get(f'{LISTINGS}pic/').data['image_url']

        self.assertTrue(url.startswith('http://testserver/'), url)
        self.assertTrue(url.endswith('listings/2026/10/pic.png'), url)


class CurrencyAndCachingTests(CatalogFixture):
    def test_every_response_carries_the_live_currency(self):
        make_listing(self.product('A-1'), slug='one')

        for path in (LISTINGS, f'{LISTINGS}one/', CATEGORIES, INSTRUCTIONS):
            currency = self.get(path).data['currency']
            self.assertEqual(currency['code'], 'USD', path)
            self.assertEqual(currency['secondary']['code'], 'VES', path)
            self.assertEqual(currency['secondary']['rate'], '50.00000000', path)

    def test_a_rate_change_shows_at_once(self):
        make_listing(self.product('A-1'), slug='one')
        self.get(LISTINGS)

        set_currency(secondary_exchange_rate='61.5')

        self.assertEqual(self.get(LISTINGS).data['currency']['secondary']['rate'], '61.50000000')

    def test_no_secondary_currency_is_reported_as_null(self):
        set_currency(secondary_currency_enabled=False)
        self.assertIsNone(self.get(CATEGORIES).data['currency']['secondary'])

    def test_the_catalogue_may_be_cached_for_a_minute_and_payment_instructions_not_at_all(self):
        make_listing(self.product('A-1'), slug='one')

        for path in (LISTINGS, f'{LISTINGS}one/', CATEGORIES):
            self.assertEqual(self.get(path)['Cache-Control'], 'max-age=60', path)
        self.assertEqual(self.get(INSTRUCTIONS)['Cache-Control'], 'no-store')

    def test_every_response_varies_on_the_credential(self):
        make_listing(self.product('A-1'), slug='one')

        for path in (LISTINGS, f'{LISTINGS}one/', CATEGORIES, INSTRUCTIONS, f'{LISTINGS}nothing/'):
            self.assertIn('Authorization', self.get(path)['Vary'], path)
        # An unauthenticated refusal says so too, so it is never reused for a keyed call.
        self.assertIn('Authorization', self.client.get(LISTINGS)['Vary'])

    def test_failures_are_not_cached(self):
        self.assertNotIn('max-age', self.get(f'{LISTINGS}nothing/').get('Cache-Control', ''))
        self.assertNotIn('max-age', self.client.get(LISTINGS).get('Cache-Control', ''))


class QueryingTests(CatalogFixture):
    def setUp(self):
        super().setUp()
        self.antivirus = ProductCategory.objects.create(name='Antivirus', parent_category=self.software)
        self.games = make_category('Games')
        make_listing(self.product('P-1', category=self.software, unit_price='60.00'), slug='suite',
                     title='Office Suite',
                     display_type='license', sort=2)
        make_listing(self.product('P-2', category=self.antivirus, unit_price='30.00'), slug='shield',
                     title='Shield Antivirus', display_type='Subscription', sort=1)
        make_listing(self.product('P-3', category=self.games, unit_price='5.00'), slug='arcade',
                     title='Arcade Pass', display_type='subscription', sort=3)

    def test_the_default_order_is_sort_then_creation(self):
        self.assertEqual(self.slugs(), ['shield', 'suite', 'arcade'])

    def test_a_category_includes_its_descendants(self):
        self.assertEqual(sorted(self.slugs(category=self.software.pk)), ['shield', 'suite'])
        self.assertEqual(self.slugs(category=self.antivirus.pk), ['shield'])
        self.assertEqual(self.slugs(category=self.games.pk), ['arcade'])

    def test_display_type_ignores_case(self):
        self.assertEqual(sorted(self.slugs(display_type='SUBSCRIPTION')), ['arcade', 'shield'])
        self.assertEqual(self.slugs(display_type='license'), ['suite'])

    def test_search_matches_the_title_and_the_variant_product_name(self):
        Product.objects.filter(sku='P-3').update(name='Hidden Gem')

        self.assertEqual(self.slugs(search='office'), ['suite'])
        self.assertEqual(self.slugs(search='hidden gem'), ['arcade'])
        self.assertEqual(self.slugs(search='no such thing'), [])

    def test_search_does_not_repeat_a_listing_with_several_matching_variants(self):
        listing = Listing.objects.get(slug='suite')
        extra = self.product('P-4')
        Product.objects.filter(pk=extra.pk).update(name='Office Suite 24M')
        ListingVariant.objects.create(listing=listing, product=extra, position=1)

        self.assertEqual(self.slugs(search='office'), ['suite'])

    def test_ordering_by_price_and_title(self):
        self.assertEqual(self.slugs(ordering='from_price'), ['arcade', 'shield', 'suite'])
        self.assertEqual(self.slugs(ordering='-from_price'), ['suite', 'shield', 'arcade'])
        self.assertEqual(self.slugs(ordering='title'), ['arcade', 'suite', 'shield'])

    def test_pages_report_the_total_and_cap_their_size(self):
        page = self.get(LISTINGS, page_size=2).data
        self.assertEqual((page['count'], len(page['results'])), (3, 2))
        self.assertIsNotNone(page['next'])

        for index in range(120):
            make_listing(self.product(f'BULK-{index}', stock=0), slug=f'bulk-{index}')
        capped = self.get(LISTINGS, page_size=1000).data
        self.assertEqual(len(capped['results']), 100)


class QueryCostTests(CatalogFixture):
    def _listings(self, count):
        for index in range(count):
            make_listing(
                self.product(f'Q-{index}-A', category=self.software, stock=0),
                self.product(f'Q-{index}-B', category=self.software, stock=0),
                slug=f'q-{index}',
            )

    def _cost(self, path, **params):
        with CaptureQueriesContext(connection) as queries:
            response = self.get(path, **params)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_a_page_costs_the_same_however_many_listings_it_holds(self):
        self._listings(12)

        small = self._cost(LISTINGS, page_size=2)
        large = self._cost(LISTINGS, page_size=12)

        self.assertEqual(small, large)
        # authentication, count, page, variants, currency
        self.assertLessEqual(large, 6)

    def test_the_category_tree_costs_the_same_however_large(self):
        self._listings(2)
        small = self._cost(CATEGORIES)
        for index in range(6):
            child = ProductCategory.objects.create(name=f'Child {index}', parent_category=self.software)
            make_listing(self.product(f'T-{index}', category=child, stock=0), slug=f't-{index}')

        self.assertEqual(self._cost(CATEGORIES), small)


class CategoryTreeTests(CatalogFixture):
    def test_only_categories_that_lead_to_a_visible_listing_appear_with_counts(self):
        antivirus = ProductCategory.objects.create(name='Antivirus', parent_category=self.software)
        backup = ProductCategory.objects.create(name='Backup', parent_category=self.software)
        games = make_category('Games')
        make_category('Empty')
        make_listing(self.product('S-1', category=self.software), slug='suite')
        make_listing(self.product('A-1', category=antivirus), slug='shield')
        make_listing(self.product('A-2', category=antivirus), slug='shield-2')
        make_listing(self.product('B-1', category=backup), slug='hidden-backup', published=False)
        make_listing(self.product('G-1', category=games, is_active=False), slug='withdrawn')

        tree = self.get(CATEGORIES).data['results']

        self.assertEqual([node['name'] for node in tree], ['Software'])
        software = tree[0]
        self.assertEqual(software['listing_count'], 3)  # one own, two beneath
        self.assertEqual([(c['name'], c['listing_count']) for c in software['children']], [('Antivirus', 2)])
        self.assertEqual(set(software), {'id', 'name', 'description', 'listing_count', 'children'})

    def test_an_empty_catalogue_is_an_empty_tree(self):
        self.assertEqual(self.get(CATEGORIES).data['results'], [])


class PaymentInstructionsTests(CatalogFixture):
    def setUp(self):
        super().setUp()
        self.admin = make_user(Role.ADMIN)

    def _profile(self, **attrs):
        values = dict(
            payment_method=RecipientProfile.MOBILE_PAYMENT, phone='0414-1234567',
            bank='Banco de Venezuela', document_id='J-12345678-9', created_by=self.admin,
        )
        values.update(attrs)
        return RecipientProfile.objects.create(**values)

    def test_each_method_shows_its_primary_active_profile_and_only_the_safe_fields(self):
        self._profile(is_primary=True)
        self._profile(phone='0412-7654321', bank='Other Bank', is_primary=False)
        bank = self._profile(
            payment_method=RecipientProfile.BANK_TRANSFER, phone='',
            account_number='01020000000000000001', is_primary=True,
        )

        results = self.get(INSTRUCTIONS).data['results']

        self.assertEqual([r['payment_method'] for r in results], ['bank_transfer', 'mobile_payment'])
        self.assertEqual(set(results[0]), {
            'payment_method', 'payment_method_display', 'bank', 'phone', 'account_number', 'document_id',
        })
        self.assertEqual(results[0]['account_number'], bank.account_number)
        self.assertEqual(results[1]['phone'], '0414-1234567')

    def test_a_deactivated_primary_profile_is_never_shown(self):
        profile = self._profile(is_primary=True)
        RecipientProfile.objects.filter(pk=profile.pk).update(is_active=False)

        self.assertEqual(self.get(INSTRUCTIONS).data['results'], [])

    def test_the_kiosk_and_the_storefront_show_the_same_profiles(self):
        from api.kiosk.serializers import KioskRecipientProfileSerializer

        self._profile(is_primary=True)
        expected = KioskRecipientProfileSerializer(
            RecipientProfile.primary_for_customers(), many=True,
        ).data

        self.assertEqual(self.get(INSTRUCTIONS).data['results'], list(expected))
