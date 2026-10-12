"""
Settings for the local storefront demonstration. Never for a real shop.

    DJANGO_SETTINGS_MODULE=retailops.demo_settings python manage.py runserver 127.0.0.1:8010

It is the normal settings plus the one thing a demonstration needs that a shop does not: the
per-channel storefront limits raised, because an automated run (three storefronts, every scenario)
makes far more calls in a minute than any shop would. Everything else, including every security
setting, is untouched.
"""
from .settings import *  # noqa: F401,F403
from .settings import REST_FRAMEWORK

DEMO_STOREFRONT_RATES = {
    'storefront_read': '6000/min',
    'storefront_order': '600/min',
    'storefront_payment': '600/min',
    'storefront_entitlement': '600/min',
}

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_THROTTLE_RATES': {**REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'], **DEMO_STOREFRONT_RATES},
}
