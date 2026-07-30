from django.test import TestCase

from core.models import Payment, RecipientProfile, Role, User


def make_profile(creator, phone='4141234567', payment_method=Payment.MOBILE_PAYMENT,
                 account_number='', is_primary=False):
    return RecipientProfile.objects.create(
        label=f'Profile {phone or account_number}',
        payment_method=payment_method,
        phone=phone,
        account_number=account_number,
        bank='BDV',
        document_id='V12345678',
        is_primary=is_primary,
        created_by=creator,
    )


class DemoteOtherPrimariesTests(TestCase):
    def setUp(self):
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.user = User.objects.create_user(
            email='admin@example.com', password='AdminPass123!',
            first_name='Ada', last_name='Admin', role=role,
        )

    def test_clears_every_primary_when_no_pk_excluded(self):
        primary = make_profile(self.user, phone='4141111111', is_primary=True)

        RecipientProfile.demote_other_primaries(Payment.MOBILE_PAYMENT)

        primary.refresh_from_db()
        self.assertFalse(primary.is_primary)

    def test_leaves_the_excluded_profile_untouched(self):
        keeper = make_profile(self.user, phone='4141111111', is_primary=True)

        RecipientProfile.demote_other_primaries(Payment.MOBILE_PAYMENT, exclude_pk=keeper.pk)

        keeper.refresh_from_db()
        self.assertTrue(keeper.is_primary)

    def test_a_lone_new_profile_is_primary_without_being_asked(self):
        lone = make_profile(self.user, phone='4141111111')

        self.assertTrue(lone.is_primary)
        lone.refresh_from_db()
        self.assertTrue(lone.is_primary)

    def test_a_second_profile_does_not_become_primary(self):
        make_profile(self.user, phone='4141111111')

        second = make_profile(self.user, phone='4142222222')

        self.assertFalse(second.is_primary)

    def test_deleting_one_of_two_promotes_the_survivor(self):
        first = make_profile(self.user, phone='4141111111')
        survivor = make_profile(self.user, phone='4142222222')

        first.delete()

        survivor.refresh_from_db()
        self.assertTrue(survivor.is_primary)

    def test_moving_a_profile_to_another_method_promotes_the_one_left_behind(self):
        make_profile(self.user, phone='4141111111')
        mover = make_profile(self.user, phone='4142222222')
        left_behind = RecipientProfile.objects.get(phone='4141111111')

        mover.payment_method = Payment.BANK_TRANSFER
        mover.phone = ''
        mover.account_number = '0102123456789012'
        mover.save()

        left_behind.refresh_from_db()
        mover.refresh_from_db()
        self.assertTrue(left_behind.is_primary, 'the lone mobile_payment profile should be promoted')
        self.assertTrue(mover.is_primary, 'the lone bank_transfer profile should be promoted')

    def test_only_affects_the_given_payment_method(self):
        mobile = make_profile(self.user, phone='4141111111', is_primary=True)
        wire = make_profile(
            self.user, phone='', payment_method=Payment.BANK_TRANSFER,
            account_number='0102123456789012', is_primary=True,
        )

        RecipientProfile.demote_other_primaries(Payment.MOBILE_PAYMENT)

        mobile.refresh_from_db()
        wire.refresh_from_db()
        self.assertFalse(mobile.is_primary)
        self.assertTrue(wire.is_primary)
