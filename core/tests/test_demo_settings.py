"""
The settings the storefront demonstration runs with: the normal ones, plus higher storefront limits.

What matters is what does not change: every security-relevant setting must be exactly the normal one,
so a demo can never quietly be less safe than the real thing.
"""
from django.test import SimpleTestCase

from retailops import demo_settings, settings as normal


class DemoSettingsTests(SimpleTestCase):
    def test_only_the_storefront_limits_are_raised(self):
        normal_rates = normal.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        demo_rates = demo_settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']

        raised = {name for name in demo_rates if demo_rates[name] != normal_rates.get(name)}

        self.assertEqual(
            raised, {'storefront_read', 'storefront_order', 'storefront_payment', 'storefront_entitlement'},
        )

    def test_the_raised_limits_really_are_higher(self):
        def per_minute(value):
            return int(value.split('/')[0])

        for name, value in demo_settings.DEMO_STOREFRONT_RATES.items():
            self.assertGreater(per_minute(value), per_minute(normal.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'][name]), name)

    def test_every_other_setting_is_the_normal_one(self):
        # Star-import skips private names, which are not settings anyway.
        names = {name for name in dir(normal) if name.isupper() and not name.startswith('_')} - {
            'REST_FRAMEWORK', 'CHANNEL_WEBHOOK_ALLOW_INSECURE', 'DATABASES',
        }

        for name in names:
            self.assertEqual(getattr(demo_settings, name), getattr(normal, name), name)

    def test_a_webhook_may_go_to_this_machine_only_in_the_demonstration(self):
        # The storefronts run on 127.0.0.1; a real deployment sends only to public https addresses.
        self.assertFalse(normal.CHANNEL_WEBHOOK_ALLOW_INSECURE)
        self.assertIs(demo_settings.CHANNEL_WEBHOOK_ALLOW_INSECURE, True)

    def test_the_database_is_the_normal_one_except_that_it_waits_its_turn(self):
        normal_db, demo_db = normal.DATABASES['default'], demo_settings.DATABASES['default']

        if normal_db['ENGINE'] != 'django.db.backends.sqlite3':
            self.assertEqual(demo_db, normal_db)  # PostgreSQL is left exactly as it is
            return
        self.assertEqual(demo_db['ENGINE'], 'retailops.demo_db')
        self.assertEqual(demo_db['OPTIONS']['timeout'], 30)
        rest = lambda config: {k: v for k, v in config.items() if k not in ('ENGINE', 'OPTIONS')}
        self.assertEqual(rest(demo_db), rest(normal_db))

    def test_the_rest_of_the_api_configuration_is_untouched(self):
        for key, value in normal.REST_FRAMEWORK.items():
            if key != 'DEFAULT_THROTTLE_RATES':
                self.assertEqual(demo_settings.REST_FRAMEWORK[key], value, key)

    def test_the_authentication_and_login_limits_are_not_loosened(self):
        demo_rates = demo_settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']

        for name in ('login', 'password_reset', 'password_change', 'ocr_verify'):
            self.assertEqual(demo_rates[name], normal.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'][name], name)
