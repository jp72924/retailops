"""
SQLite that takes its turn. For the local demonstration only (see retailops.demo_settings).

Django opens a transaction with a plain ``BEGIN``, which in SQLite means "I am only reading": the
write lock is asked for later, at the first write. When two transactions that both started by
reading try to write, the second can never be given the lock (the first changed what it had read)
and fails at once with "database is locked", whatever the timeout. A demonstration with three
storefronts, their webhooks and a delivery worker all calling one SQLite file hits that all the
time. ``BEGIN IMMEDIATE`` asks for the write lock up front, so a transaction waits its turn (up to
the connection's timeout) instead of failing. Django 5.1 offers this as an option; this is the same
thing for 4.2. A real deployment uses PostgreSQL and none of this.
"""
from django.db.backends.sqlite3 import base


class DatabaseWrapper(base.DatabaseWrapper):
    def _start_transaction_under_autocommit(self):
        self.cursor().execute('BEGIN IMMEDIATE')
