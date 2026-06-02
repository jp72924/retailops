from django.test import SimpleTestCase

from retailops import settings as retailops_settings


class ProductionSettingsTests(SimpleTestCase):
    def test_csv_env_trims_and_ignores_empty_values(self):
        values = retailops_settings._csv_env({
            'DJANGO_ALLOWED_HOSTS': ' retailops.example.com, ,kiosk.example.com ',
        }, 'DJANGO_ALLOWED_HOSTS')

        self.assertEqual(values, ['retailops.example.com', 'kiosk.example.com'])

    def test_secure_proxy_ssl_header_can_be_enabled(self):
        header = retailops_settings._secure_proxy_ssl_header({
            'DJANGO_SECURE_PROXY_SSL_HEADER': 'true',
        }, default_enabled=False)

        self.assertEqual(header, ('HTTP_X_FORWARDED_PROTO', 'https'))

    def test_secure_proxy_ssl_header_can_be_disabled(self):
        header = retailops_settings._secure_proxy_ssl_header({
            'DJANGO_SECURE_PROXY_SSL_HEADER': 'false',
        }, default_enabled=True)

        self.assertIsNone(header)

    def test_hsts_seconds_rejects_negative_values(self):
        with self.assertRaisesRegex(RuntimeError, 'DJANGO_SECURE_HSTS_SECONDS'):
            retailops_settings._env_int(
                {'DJANGO_SECURE_HSTS_SECONDS': '-1'},
                'DJANGO_SECURE_HSTS_SECONDS',
                0,
                minimum=0,
            )
