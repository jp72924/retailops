"""
Publishing a product to the storefront catalogue, the listing models' own
validation, and the operator tools around sales channels (admin and command).
"""
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from api.tests.helpers import channel_auth, make_category, make_channel, make_listing, make_product, make_user
from core.models import ChannelKey, Listing, ListingVariant, Product, Role, SalesChannel, User
from core.services.listings import publish_product


class PublishProductTests(TestCase):
    def setUp(self):
        self.product = make_product(
            sku='LIC-12M', name='Licence 12 months', stock=0,
            kind=Product.DIGITAL, track_inventory=False,
        )

    def test_a_product_becomes_a_published_listing_with_itself_as_the_variant(self):
        listing, created = publish_product(self.product)

        self.assertTrue(created)
        self.assertEqual(listing.slug, 'lic-12m')
        self.assertEqual(listing.title, 'Licence 12 months')
        self.assertEqual(listing.category, self.product.category)
        self.assertEqual(listing.display_type, Product.DIGITAL)
        self.assertTrue(listing.is_published)
        self.assertEqual([v.product for v in listing.variants.all()], [self.product])

    def test_publishing_twice_changes_nothing(self):
        first, _ = publish_product(self.product)
        second, created = publish_product(self.product)

        self.assertFalse(created)
        self.assertEqual(second, first)
        self.assertEqual(Listing.objects.count(), 1)
        self.assertEqual(ListingVariant.objects.count(), 1)

    def test_it_never_republishes_a_listing_an_operator_unpublished(self):
        listing, _ = publish_product(self.product)
        Listing.objects.filter(pk=listing.pk).update(is_published=False)

        again, created = publish_product(self.product)

        self.assertFalse(created)
        self.assertFalse(Listing.objects.get(pk=again.pk).is_published)

    def test_a_product_already_in_a_listing_is_not_moved(self):
        other = make_product(sku='OTHER', stock=0)
        existing = make_listing(self.product, other, slug='bundle')

        listing, created = publish_product(self.product)

        self.assertFalse(created)
        self.assertEqual(listing, existing)
        self.assertEqual(Listing.objects.count(), 1)

    def test_slugs_stay_unique(self):
        Listing.objects.create(slug='lic-12m', title='Taken', category=self.product.category)
        clash = make_product(sku='LIC 12M', stock=0)  # slugifies to the same slug

        first, _ = publish_product(self.product)
        second, _ = publish_product(clash)

        self.assertEqual(first.slug, 'lic-12m-2')
        self.assertEqual(second.slug, 'lic-12m-3')


class DeletingAListedProductTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        from api.tests.helpers import auth_client

        self.client = APIClient()
        auth_client(self.client, make_user(Role.MANAGER))

    def test_an_unsold_product_can_still_be_deleted_and_leaves_its_listing(self):
        keep = make_product(sku='KEEP', stock=0)
        gone = make_product(sku='GONE', stock=0, kind=Product.DIGITAL, track_inventory=False)
        listing = make_listing(keep, gone, slug='pair')

        response = self.client.delete(f'/api/v1/products/{gone.pk}/')

        self.assertEqual(response.status_code, 204)
        self.assertEqual([v.product for v in listing.variants.all()], [keep])

    def test_a_product_with_stock_history_is_still_protected(self):
        stocked = make_product(sku='STOCKED', stock=5)
        make_listing(stocked, slug='one')

        response = self.client.delete(f'/api/v1/products/{stocked.pk}/')

        self.assertEqual(response.status_code, 409)
        self.assertTrue(ListingVariant.objects.filter(product=stocked).exists())


class ListingValidationTests(TestCase):
    def setUp(self):
        self.category = make_category()
        self.product = make_product(stock=0, unit_price='50.00')

    def _listing(self, **attrs):
        values = dict(slug='one', title='One', category=self.category)
        values.update(attrs)
        return Listing(**values)

    def test_features_must_be_a_short_list_of_text(self):
        for bad in ('text', [1, 2], ['ok', ''], ['x'] * 21, {'a': 1}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                self._listing(features=bad).full_clean()
        self._listing(features=['Fast', 'Safe']).full_clean()

    def test_attributes_must_be_an_object(self):
        for bad in (['a'], 'text', 3):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                self._listing(attributes=bad).full_clean()
        self._listing(attributes={'platform': 'Windows'}).full_clean()

    def test_compare_at_price_must_exceed_the_price(self):
        listing = self._listing()
        listing.save()
        for compare in ('50.00', '10.00'):
            variant = ListingVariant(listing=listing, product=self.product, compare_at_price=compare)
            with self.assertRaises(ValidationError, msg=compare):
                variant.full_clean()
        ListingVariant(listing=listing, product=self.product, compare_at_price='80.00').full_clean()

    def test_a_product_can_be_in_only_one_listing(self):
        first = self._listing()
        first.save()
        second = self._listing(slug='two', title='Two')
        second.save()
        ListingVariant.objects.create(listing=first, product=self.product)

        with self.assertRaises(ValidationError):
            ListingVariant(listing=second, product=self.product).full_clean()


class PublishViewTests(TestCase):
    def setUp(self):
        self.manager = make_user(Role.MANAGER)
        self.client.force_login(self.manager)
        self.product = make_product(sku='LIC-12M', stock=0, kind=Product.DIGITAL, track_inventory=False)
        self.url = reverse('product-publish', args=[self.product.pk])

    def _messages(self, response):
        return [str(m) for m in response.context['messages']]

    def test_a_manager_publishes_a_product(self):
        response = self.client.post(self.url, follow=True)

        self.assertRedirects(response, reverse('product-edit', args=[self.product.pk]))
        self.assertEqual(self._messages(response), ['LIC-12M was published to the online store.'])
        self.assertTrue(Listing.objects.get().is_published)

    def test_publishing_again_says_so(self):
        self.client.post(self.url, follow=True)  # shows (and so consumes) the first message
        response = self.client.post(self.url, follow=True)

        self.assertEqual(self._messages(response), ['LIC-12M is already in the online store catalogue.'])
        self.assertEqual(Listing.objects.count(), 1)

    def test_an_inactive_product_is_listed_with_a_warning(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=False)

        response = self.client.post(self.url, follow=True)

        self.assertIn('will not appear in the online store until the product is active',
                      self._messages(response)[0])
        self.assertEqual(Listing.objects.count(), 1)

    def test_staff_cannot_publish_and_a_get_is_refused(self):
        self.client.force_login(make_user(Role.STAFF))
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertFalse(Listing.objects.exists())

        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_the_product_page_offers_to_publish_then_shows_the_listing(self):
        edit = reverse('product-edit', args=[self.product.pk])

        before = self.client.get(edit)
        self.assertContains(before, self.url)
        self.assertContains(before, 'Publish to store')

        self.client.post(self.url)
        after = self.client.get(edit)
        self.assertNotContains(after, 'Publish to store')
        self.assertContains(after, 'Listed in the online store as')
        self.assertContains(after, 'lic-12m')

    def test_only_admin_users_are_sent_to_the_listing_admin(self):
        self.client.post(self.url)
        listing = Listing.objects.get()
        edit = reverse('product-edit', args=[self.product.pk])
        admin_url = reverse('admin:core_listing_change', args=[listing.pk])

        self.assertNotContains(self.client.get(edit), admin_url)

        self.client.force_login(make_user(Role.ADMIN, is_staff=True, is_superuser=True))
        self.assertContains(self.client.get(edit), admin_url)

    def test_an_unpublished_listing_is_flagged_on_the_product_page(self):
        self.client.post(self.url)
        Listing.objects.update(is_published=False)

        response = self.client.get(reverse('product-edit', args=[self.product.pk]))

        self.assertContains(response, 'Unpublished')


class ChannelAdminTests(TestCase):
    def setUp(self):
        self.admin = make_user(Role.ADMIN, is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)
        self.channel, self.raw_key = make_channel(admin=self.admin)

    def _action(self, name, *pks, model='saleschannel'):
        return self.client.post(
            reverse(f'admin:core_{model}_changelist'),
            {'action': name, '_selected_action': list(pks)},
            follow=True,
        )

    def test_issuing_a_key_shows_it_once_and_it_works_alongside_the_old_one(self):
        response = self._action('issue_key', self.channel.pk)

        message = next(str(m) for m in response.context['messages'] if 'new channel key' in str(m))
        new_key = message.rsplit(': ', 1)[1]
        self.assertEqual(len(new_key), 48)
        url = '/api/v1/storefront/catalog/categories/'
        self.assertEqual(self.client.get(url, **channel_auth(new_key)).status_code, 200)
        self.assertEqual(self.client.get(url, **channel_auth(self.raw_key)).status_code, 200)
        self.assertEqual(self.channel.keys.count(), 2)

    def test_revoking_all_keys_locks_the_channel_out(self):
        response = self._action('revoke_all_keys', self.channel.pk)

        self.assertIn('1 key(s) revoked.', [str(m) for m in response.context['messages']])
        self.assertEqual(
            self.client.get('/api/v1/storefront/catalog/categories/', **channel_auth(self.raw_key)).status_code,
            401,
        )

    def test_one_key_can_be_revoked_from_the_key_list(self):
        key = self.channel.keys.get()

        self._action('revoke_keys', key.pk, model='channelkey')

        self.assertIsNotNone(ChannelKey.objects.get(pk=key.pk).revoked_at)

    def test_a_channel_can_be_deactivated_and_reactivated(self):
        self._action('deactivate_channels', self.channel.pk)
        url = '/api/v1/storefront/catalog/categories/'
        self.assertEqual(self.client.get(url, **channel_auth(self.raw_key)).status_code, 401)

        self._action('activate_channels', self.channel.pk)
        self.assertEqual(self.client.get(url, **channel_auth(self.raw_key)).status_code, 200)

    def test_the_admin_lists_active_keys_and_offers_no_way_to_add_a_channel(self):
        response = self.client.get(reverse('admin:core_saleschannel_changelist'))
        self.assertContains(response, self.channel.name)
        self.assertEqual(self.client.get(reverse('admin:core_saleschannel_add')).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:core_channelkey_add')).status_code, 403)

    def test_the_listing_admin_loads_and_edits_in_place(self):
        listing = make_listing(make_product(stock=0), slug='one')

        self.assertEqual(self.client.get(reverse('admin:core_listing_changelist')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('admin:core_listing_change', args=[listing.pk])).status_code, 200,
        )
        self._action('unpublish_listings', listing.pk, model='listing')
        self.assertFalse(Listing.objects.get(pk=listing.pk).is_published)


class ProvisionChannelCommandTests(TestCase):
    def setUp(self):
        make_user(Role.ADMIN, is_staff=True)

    def _run(self, *args):
        out = StringIO()
        call_command('provision_channel', *args, stdout=out)
        return out.getvalue()

    def _key_in(self, output):
        return next(line.strip() for line in output.splitlines() if len(line.strip()) == 48)

    def test_it_creates_a_channel_and_prints_a_working_key_once(self):
        output = self._run('--slug', 'trend-store', '--name', 'Trend Store', '--label', 'prod')

        key = self._key_in(output)
        channel = SalesChannel.objects.get(slug='trend-store')
        self.assertEqual(channel.name, 'Trend Store')
        self.assertEqual(channel.keys.get().label, 'prod')
        self.assertEqual(
            self.client.get('/api/v1/storefront/catalog/categories/', **channel_auth(key)).status_code, 200,
        )
        self.assertEqual(output.count(key), 1)

    def test_new_key_adds_one_without_touching_the_others(self):
        self._run('--slug', 'trend-store', '--name', 'Trend Store')
        first = SalesChannel.objects.get().keys.get()

        second_key = self._key_in(self._run('--slug', 'trend-store', '--new-key', '--label', 'rotation'))

        channel = SalesChannel.objects.get()
        self.assertEqual(channel.keys.filter(revoked_at__isnull=True).count(), 2)
        self.assertTrue(channel.keys.filter(prefix=second_key[:8], label='rotation').exists())
        first.refresh_from_db()
        self.assertIsNone(first.revoked_at)

    def test_it_refuses_a_duplicate_a_missing_name_and_an_unknown_channel(self):
        self._run('--slug', 'trend-store', '--name', 'Trend Store')

        for args in (
            ('--slug', 'trend-store', '--name', 'Another'),   # slug taken: needs --new-key
            ('--slug', 'other'),                              # new channel without a name
            ('--slug', 'ghost', '--new-key'),                 # nothing to add a key to
            ('--slug', 'other', '--name', 'Trend Store'),     # name taken
        ):
            with self.assertRaises(CommandError, msg=str(args)):
                self._run(*args)
        self.assertEqual(SalesChannel.objects.count(), 1)

    def test_it_refuses_a_slug_or_name_the_model_would_not_accept(self):
        for args in (
            ('--slug', 'Trend Store!', '--name', 'Trend Store'),   # not a slug
            ('--slug', 'x' * 51, '--name', 'Trend Store'),         # too long
            ('--slug', 'ok', '--name', 'N' * 101),                 # name too long
        ):
            with self.assertRaises(CommandError, msg=str(args)):
                self._run(*args)
        self.assertFalse(SalesChannel.objects.exists())
        self.assertFalse(User.objects.filter(email__endswith='@channel.internal').exists())

    def test_it_needs_someone_to_attribute_the_channel_to(self):
        User.objects.all().delete()

        with self.assertRaises(CommandError):
            self._run('--slug', 'trend-store', '--name', 'Trend Store')
