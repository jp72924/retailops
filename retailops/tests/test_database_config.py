from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from retailops import settings as retailops_settings


class FakeDatabaseUrl:
    def __init__(self):
        self.calls = []

    def parse(self, url, conn_max_age):
        self.calls.append((url, conn_max_age))
        return {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'parsed-from-url',
        }


class DatabaseConfigTests(SimpleTestCase):
    def test_default_database_is_local_sqlite(self):
        config = retailops_settings._database_config_from_env({})

        self.assertEqual(config['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(config['NAME'], retailops_settings.BASE_DIR / 'db.sqlite3')
        self.assertFalse(config['ATOMIC_REQUESTS'])

    def test_sqlite_db_name_can_be_custom_relative_path(self):
        config = retailops_settings._database_config_from_env({
            'DB_ENGINE': 'sqlite',
            'DB_NAME': 'data/custom.sqlite3',
        })

        self.assertEqual(config['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(config['NAME'], retailops_settings.BASE_DIR / Path('data/custom.sqlite3'))

    def test_postgres_config_uses_separate_variables(self):
        config = retailops_settings._database_config_from_env({
            'DB_ENGINE': 'postgres',
            'DB_NAME': 'retailops',
            'DB_USER': 'retailops_user',
            'DB_PASSWORD': 'secret',
            'DB_HOST': 'db.example.com',
            'DB_SSLMODE': 'require',
        })

        self.assertEqual(config['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(config['NAME'], 'retailops')
        self.assertEqual(config['USER'], 'retailops_user')
        self.assertEqual(config['PASSWORD'], 'secret')
        self.assertEqual(config['HOST'], 'db.example.com')
        self.assertEqual(config['PORT'], '5432')
        self.assertEqual(config['CONN_MAX_AGE'], 60)
        self.assertEqual(config['OPTIONS']['sslmode'], 'require')
        self.assertFalse(config['ATOMIC_REQUESTS'])

    def test_postgres_config_rejects_missing_required_values(self):
        with self.assertRaisesRegex(RuntimeError, 'DB_PASSWORD'):
            retailops_settings._database_config_from_env({
                'DB_ENGINE': 'postgres',
                'DB_NAME': 'retailops',
                'DB_USER': 'retailops_user',
                'DB_HOST': 'db.example.com',
            })

    def test_database_url_has_priority(self):
        fake_parser = FakeDatabaseUrl()
        env = {
            'DATABASE_URL': 'postgresql://user:secret@db.example.com:5432/retailops',
            'DB_ENGINE': 'sqlite',
            'DB_CONN_MAX_AGE': '120',
            'DB_SSLMODE': 'require',
        }

        with patch.object(retailops_settings, 'dj_database_url', fake_parser):
            config = retailops_settings._database_config_from_env(env)

        self.assertEqual(fake_parser.calls, [(env['DATABASE_URL'], 120)])
        self.assertEqual(config['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(config['NAME'], 'parsed-from-url')
        self.assertEqual(config['OPTIONS']['sslmode'], 'require')
        self.assertFalse(config['ATOMIC_REQUESTS'])

    def test_unsupported_engine_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Unsupported DB_ENGINE'):
            retailops_settings._database_config_from_env({'DB_ENGINE': 'mysql'})
