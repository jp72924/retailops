"""
Settings for the local storefront demonstration. Never for a real shop.

    DJANGO_SETTINGS_MODULE=retailops.demo_settings python manage.py runserver 127.0.0.1:8010

It is the normal settings plus the one thing a demonstration needs that a shop does not: the
per-channel storefront limits raised, because an automated run (three storefronts, every scenario)
makes far more calls in a minute than any shop would, and a SQLite that waits its turn for its write
lock instead of failing when several processes share the file. Everything else, including every
security setting, is untouched.
"""
from .settings import *  # noqa: F401,F403
from .settings import DATABASES as _DATABASES
from .settings import REST_FRAMEWORK

# The storefronts run on this machine, so their webhooks go to http://127.0.0.1:<port>.
CHANNEL_WEBHOOK_ALLOW_INSECURE = True

DEMO_STOREFRONT_RATES = {
    'storefront_read': '6000/min',
    'storefront_order': '600/min',
    'storefront_payment': '600/min',
    'storefront_entitlement': '600/min',
}

# A demonstration on SQLite has several processes writing to one file; see retailops.demo_db.
_default = _DATABASES['default']
if _default['ENGINE'] == 'django.db.backends.sqlite3':
    DATABASES = {
        'default': {
            **_default,
            'ENGINE': 'retailops.demo_db',
            'OPTIONS': {**_default.get('OPTIONS', {}), 'timeout': 30},
        },
    }

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_THROTTLE_RATES': {**REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'], **DEMO_STOREFRONT_RATES},
}
