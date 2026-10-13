"""
The SQLite backend of the demonstration: a transaction asks for the write lock when it starts.

What this buys is shown with two connections to one file: the second writer waits for the first and
then goes on, where a plain ``BEGIN`` gives one of them "database is locked" for having read what the
other then changed.
"""
import tempfile
import threading
import time
from pathlib import Path

from django.db import connections, transaction
from django.db.backends.sqlite3.base import DatabaseWrapper as PlainWrapper
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from retailops.demo_db.base import DatabaseWrapper


def connect(path, wrapper=DatabaseWrapper, alias='demo'):
    connection = wrapper({
        'NAME': str(path), 'ENGINE': 'x', 'OPTIONS': {'timeout': 10}, 'TIME_ZONE': None, 'CONN_MAX_AGE': 0,
        'CONN_HEALTH_CHECKS': False, 'AUTOCOMMIT': True, 'ATOMIC_REQUESTS': False, 'USER': '', 'PASSWORD': '',
        'HOST': '', 'PORT': '', 'TEST': {},
    }, alias=alias)
    connections[alias] = connection  # so that transaction.atomic(using=alias) finds it
    return connection


def release(connection):
    connection.close()
    del connections[connection.alias]


class TheBackendTests(SimpleTestCase):
    databases = set()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'demo.sqlite3'
        first = connect(self.path)
        first.cursor().execute('create table counter (n integer)')
        first.cursor().execute('insert into counter values (0)')
        release(first)

    def test_a_transaction_asks_for_the_write_lock_when_it_begins(self):
        wrapper = connect(self.path)
        self.addCleanup(release, wrapper)

        with CaptureQueriesContext(wrapper) as queries, transaction.atomic(using=wrapper.alias):
            pass

        self.assertEqual([q['sql'] for q in queries.captured_queries][:1], ['BEGIN IMMEDIATE'])

    def test_the_ordinary_backend_begins_by_only_reading(self):
        wrapper = connect(self.path, PlainWrapper, alias='plain')
        self.addCleanup(release, wrapper)

        with CaptureQueriesContext(wrapper) as queries, transaction.atomic(using=wrapper.alias):
            pass

        self.assertEqual([q['sql'] for q in queries.captured_queries][:1], ['BEGIN'])

    def run_two_writers(self, wrapper):
        """Both read the counter and then add one to it; returns how each one fared."""
        outcomes = {}
        started = threading.Event()

        def writer(name, hold):
            connection = connect(self.path, wrapper, alias=name)
            try:
                with transaction.atomic(using=name):
                    cursor = connection.cursor()
                    (value,) = cursor.execute('select n from counter').fetchone()
                    started.set()
                    time.sleep(hold)
                    cursor.execute('update counter set n = %s', [value + 1])
                outcomes[name] = 'ok'
            except Exception as error:  # noqa: BLE001 - the point is what it was
                outcomes[name] = type(error).__name__
            finally:
                release(connection)

        first = threading.Thread(target=writer, args=('first', 0.6))
        second = threading.Thread(target=writer, args=('second', 0.0))
        first.start()
        started.wait(5)
        second.start()
        first.join()
        second.join()
        return outcomes

    def test_two_writers_that_read_first_both_get_through_one_after_the_other(self):
        self.assertEqual(self.run_two_writers(DatabaseWrapper), {'first': 'ok', 'second': 'ok'})

        check = connect(self.path)
        self.addCleanup(release, check)
        self.assertEqual(check.cursor().execute('select n from counter').fetchone(), (2,))

    def test_with_the_ordinary_backend_one_of_them_is_told_the_database_is_locked(self):
        # The one that read first and wrote last finds what it read already changed.
        outcomes = self.run_two_writers(PlainWrapper)

        self.assertEqual(sorted(outcomes.values()), ['OperationalError', 'ok'])
