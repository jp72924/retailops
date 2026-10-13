"""
The guard that keeps demonstration commands off the development database.

The commands' own tests patch it, so it is tested here directly: it must say yes for the development
SQLite file and no for anything else, including PostgreSQL and a scratch SQLite file.
"""
from pathlib import Path

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from core.management.scratch import refuses_default_database

SQLITE = 'django.db.backends.sqlite3'


def database(**overrides):
    return {'default': {'ENGINE': SQLITE, 'NAME': Path(settings.BASE_DIR) / 'db.sqlite3', **overrides}}


class RefusesDefaultDatabaseTests(SimpleTestCase):
    @override_settings(DATABASES=database())
    def test_the_development_sqlite_file_is_refused(self):
        self.assertTrue(refuses_default_database())

    @override_settings(DATABASES=database(NAME=str(Path(settings.BASE_DIR) / 'db.sqlite3')))
    def test_it_does_not_matter_how_the_path_is_spelled(self):
        self.assertTrue(refuses_default_database())

    @override_settings(DATABASES=database(NAME=str(Path(settings.BASE_DIR) / '.demo' / '..' / 'db.sqlite3')))
    def test_a_roundabout_path_to_the_same_file_is_refused(self):
        self.assertTrue(refuses_default_database())

    @override_settings(DATABASES=database(NAME=str(Path(settings.BASE_DIR) / '.demo' / 'demo.sqlite3')))
    def test_a_scratch_sqlite_file_is_allowed(self):
        self.assertFalse(refuses_default_database())

    @override_settings(DATABASES=database(NAME=str(Path(settings.BASE_DIR).parent / 'db.sqlite3')))
    def test_a_file_of_the_same_name_elsewhere_is_allowed(self):
        self.assertFalse(refuses_default_database())

    @override_settings(DATABASES=database(ENGINE='django.db.backends.postgresql', NAME='db.sqlite3'))
    def test_a_server_database_is_never_refused_by_this_guard(self):
        self.assertFalse(refuses_default_database())

    @override_settings(DATABASES=database())
    def test_both_commands_stop_before_touching_anything(self):
        for command, options in (
            ('import_storefront_catalog', {'catalog': 'nothing.json'}),
            ('configure_demo_site', {}),
        ):
            with self.assertRaises(CommandError) as raised:
                call_command(command, **options)

            self.assertIn('db.sqlite3', str(raised.exception), command)
