"""Guard for commands that must never touch the development database by accident."""
from pathlib import Path

from django.conf import settings


def refuses_default_database():
    """True when the default database is the development SQLite file everyone starts from."""
    database = settings.DATABASES['default']
    if 'sqlite' not in database['ENGINE']:
        return False
    return Path(str(database['NAME'])).resolve() == (Path(settings.BASE_DIR) / 'db.sqlite3').resolve()


SCRATCH_DB_ADVICE = (
    'This would write to the default development database (db.sqlite3). Point it at a scratch '
    'database with the DB_NAME environment variable, or pass --allow-default-db.'
)
