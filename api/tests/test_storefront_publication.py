"""
Which channel sees which listing.

What matters: a listing that names no channel is visible to every channel (so
nothing that exists today changes), one that names some is visible only to
those, the restriction holds everywhere a channel looks (the list, the detail,
the category tree) and where it acts (ordering a SKU), and a listing that names
several channels is still counted once.
"""
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.tests.helpers import (
    channel_auth,
    make_category,
    make_channel,
    make_listing,
    make_product,
    make_user,
    set_currency,
)
from core.models import ChannelOrder, Listing, Role, SalesOrder
from core.services.listings import orderable_products, published_listings, visible_to

LISTINGS = '/api/v1/storefront/catalog/listings/'
CATEGORIES = '/api/v1/storefront/catalog/categories/'
ORDERS = '/api/v1/storefront/orders/'


class PublicationFixture(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        set_currency()
        admin = make_user(Role.ADMIN, is_staff=True)
        self.shop_a, key_a = make_channel('shop-a', admin=admin)
        self.shop_b, key_b = make_channel('shop-b', admin=admin)
        self.auth_a = channel_auth(key_a)
        self.auth_b = channel_auth(key_b)
        self.software = make_category('Software')
        self.hardware = make_category('Hardware')

    def listing(self, sku, slug, *, channels=(), category=None, **attrs):
        product = make_product(sku=sku, category=category or self.software, stock=10)
        listing = make_listing(product, slug=slug, **attrs)
        listing.channels.set(channels)
        return listing

    def slugs(self, auth, **params):
        response = self.client.get(LISTINGS, params, **auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return [row['slug'] for row in response.data['results']]

    def order(self, auth, sku, reference='WEB-1', email='ana@example.com'):
        return self.client.post(ORDERS, {
            'client_reference': reference,
            'customer': {
                'external_ref': 'user-1', 'email': email,
                'first_name': 'Ana', 'last_name': 'Perez',
            },
            'items': [{'sku': sku, 'quantity': 1}],
            'fulfillment': 'pickup',
        }, format='json', **auth)


class VisibilityTests(PublicationFixture):
    def test_a_listing_that_names_no_channel_is_visible_to_every_channel(self):
        self.listing('A-1', 'everyone')

        self.assertEqual(self.slugs(self.auth_a), ['everyone'])
        self.assertEqual(self.slugs(self.auth_b), ['everyone'])
        for auth in (self.auth_a, self.auth_b):
            self.assertEqual(self.client.get(f'{LISTINGS}everyone/', **auth).status_code, 200)

    def test_a_listing_that_names_a_channel_is_visible_only_to_it(self):
        self.listing('A-1', 'only-a', channels=[self.shop_a])
        self.listing('B-1', 'only-b', channels=[self.shop_b])
        self.listing('C-1', 'everyone')

        self.assertEqual(sorted(self.slugs(self.auth_a)), ['everyone', 'only-a'])
        self.assertEqual(sorted(self.slugs(self.auth_b)), ['everyone', 'only-b'])

    def test_another_channels_listing_is_not_found_not_forbidden(self):
        self.listing('A-1', 'only-a', channels=[self.shop_a])

        hidden = self.client.get(f'{LISTINGS}only-a/', **self.auth_b)
        missing = self.client.get(f'{LISTINGS}nothing-here/', **self.auth_b)

        self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(hidden.data, missing.data)  # indistinguishable from one that does not exist

    def test_a_listing_can_name_several_channels_and_is_counted_once(self):
        self.listing('A-1', 'shared', channels=[self.shop_a, self.shop_b])
        self.listing('B-1', 'only-b', channels=[self.shop_b])

        page = self.client.get(LISTINGS, **self.auth_b).data

        self.assertEqual(page['count'], 2)
        self.assertEqual(sorted(row['slug'] for row in page['results']), ['only-b', 'shared'])
        self.assertEqual(self.slugs(self.auth_a), ['shared'])

    def test_naming_a_channel_does_not_publish_an_unpublished_listing(self):
        self.listing('A-1', 'draft', channels=[self.shop_a], published=False)

        self.assertEqual(self.slugs(self.auth_a), [])

    def test_restricting_a_listing_takes_effect_at_once(self):
        listing = self.listing('A-1', 'open')
        self.assertEqual(self.slugs(self.auth_b), ['open'])

        listing.channels.set([self.shop_a])

        self.assertEqual(self.slugs(self.auth_b), [])
        self.assertEqual(self.slugs(self.auth_a), ['open'])

    def test_a_new_channel_sees_the_unrestricted_catalogue_not_nothing(self):
        self.listing('A-1', 'open')
        _late, key = make_channel('late-shop')

        self.assertEqual(self.slugs(channel_auth(key)), ['open'])

    def test_search_filters_and_ordering_keep_to_the_channels_listings(self):
        self.listing('A-1', 'only-a', channels=[self.shop_a], title='Antivirus')
        self.listing('B-1', 'only-b', channels=[self.shop_b], title='Antivirus Plus')

        self.assertEqual(self.slugs(self.auth_a, search='Antivirus'), ['only-a'])
        self.assertEqual(self.slugs(self.auth_b, search='Antivirus', ordering='-title'), ['only-b'])


class CategoryTests(PublicationFixture):
    def test_the_category_tree_follows_the_channel(self):
        self.listing('A-1', 'soft-a', channels=[self.shop_a], category=self.software)
        self.listing('B-1', 'hard-b', channels=[self.shop_b], category=self.hardware)
        self.listing('C-1', 'soft-all', category=self.software)

        def tree(auth):
            rows = self.client.get(CATEGORIES, **auth).data['results']
            return {row['name']: row['listing_count'] for row in rows}

        self.assertEqual(tree(self.auth_a), {'Software': 2})
        self.assertEqual(tree(self.auth_b), {'Hardware': 1, 'Software': 1})

    def test_a_category_only_another_channel_uses_is_left_out(self):
        self.listing('B-1', 'hard-b', channels=[self.shop_b], category=self.hardware)

        names = [row['name'] for row in self.client.get(CATEGORIES, **self.auth_a).data['results']]

        self.assertEqual(names, [])


class OrderingTests(PublicationFixture):
    def test_a_channel_cannot_order_a_sku_that_belongs_to_another_channel(self):
        self.listing('A-ONLY', 'only-a', channels=[self.shop_a])

        response = self.order(self.auth_b, 'A-ONLY')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'invalid_product')
        self.assertEqual(SalesOrder.objects.count(), 0)
        self.assertEqual(ChannelOrder.objects.count(), 0)

    def test_the_channel_a_listing_names_can_order_it(self):
        self.listing('A-ONLY', 'only-a', channels=[self.shop_a])

        response = self.order(self.auth_a, 'A-ONLY')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

    def test_an_unrestricted_listing_can_be_ordered_by_any_channel(self):
        self.listing('FREE-1', 'open')

        self.assertEqual(self.order(self.auth_a, 'FREE-1', 'A-1').status_code, status.HTTP_201_CREATED)
        # A different email: the same one on a second channel is a customer that channel
        # cannot claim (409 customer_email_conflict), which is not what this test is about.
        second = self.order(self.auth_b, 'FREE-1', 'B-1', email='luis@example.com')
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.content)

    def test_the_refusal_does_not_reveal_that_the_sku_exists_elsewhere(self):
        self.listing('A-ONLY', 'only-a', channels=[self.shop_a])

        elsewhere = self.order(self.auth_b, 'A-ONLY')
        nowhere = self.order(self.auth_b, 'NOT-A-SKU', 'WEB-2')

        self.assertEqual(elsewhere.data['code'], nowhere.data['code'])
        self.assertEqual(
            elsewhere.data['error'].replace('A-ONLY', 'X'), nowhere.data['error'].replace('NOT-A-SKU', 'X'),
        )


class ServiceTests(PublicationFixture):
    """The services take the channel; without one they do not restrict (the staff side, tests)."""

    def test_no_channel_means_no_restriction(self):
        self.listing('A-1', 'only-a', channels=[self.shop_a])

        self.assertEqual([l.slug for l in published_listings()], ['only-a'])
        self.assertEqual(list(orderable_products(['A-1'])), ['A-1'])
        self.assertEqual(list(orderable_products(['A-1'], self.shop_b)), [])
        self.assertEqual(list(orderable_products(['A-1'], self.shop_a)), ['A-1'])

    def test_visible_to_without_a_channel_matches_everything(self):
        self.listing('A-1', 'only-a', channels=[self.shop_a])

        self.assertEqual(Listing.objects.filter(visible_to(None)).count(), 1)
        self.assertEqual(Listing.objects.filter(visible_to(self.shop_b)).count(), 0)

    def test_a_listing_page_costs_the_same_however_many_channels_it_names(self):
        def cost():
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(LISTINGS, **self.auth_a)
            self.assertEqual(response.status_code, 200)
            return len(queries)

        self.listing('A-1', 'one', channels=[self.shop_a])
        small = cost()
        for index in range(2, 8):
            self.listing(f'A-{index}', f'many-{index}', channels=[self.shop_a, self.shop_b])

        self.assertEqual(cost(), small)
