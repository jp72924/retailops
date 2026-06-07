import os
from pathlib import Path

try:
    import dj_database_url
except ImportError:  # Keeps the default SQLite profile usable before deps install.
    dj_database_url = None

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(env, name, default=False):
    raw_value = env.get(name)
    if raw_value is None:
        return default

    value = str(raw_value).strip().lower()
    if value in {'1', 'true', 'yes', 'on'}:
        return True
    if value in {'0', 'false', 'no', 'off'}:
        return False
    raise RuntimeError(f'{name} must be a boolean value.')


def _env_int(env, name, default, minimum=None, maximum=None):
    raw_value = env.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'{name} must be an integer.') from exc

    if minimum is not None and value < minimum:
        raise RuntimeError(f'{name} must be greater than or equal to {minimum}.')
    if maximum is not None and value > maximum:
        raise RuntimeError(f'{name} must be less than or equal to {maximum}.')
    return value


def _csv_env(env, name):
    return [
        value.strip()
        for value in (env.get(name) or '').split(',')
        if value.strip()
    ]


def _secure_proxy_ssl_header(env, default_enabled):
    if not _env_bool(env, 'DJANGO_SECURE_PROXY_SSL_HEADER', default_enabled):
        return None
    return ('HTTP_X_FORWARDED_PROTO', 'https')

# ── Security settings ─────────────────────────────────────────────────────────
# In production set these via environment variables. Never hard-code real values.
#
#   SECRET_KEY   — required in production; no default accepted when DEBUG=False
#   DEBUG        — defaults True for local development; set to "False" in prod
#   ALLOWED_HOSTS — comma-separated list, e.g. "retailops.example.com,www.retailops.example.com"

_SECRET_KEY_DEFAULT = 'django-insecure-change-me-before-deploying-to-production'
DEBUG = os.environ.get('DJANGO_DEBUG', 'True') == 'True'

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', _SECRET_KEY_DEFAULT)
if not DEBUG and SECRET_KEY == _SECRET_KEY_DEFAULT:
    raise RuntimeError(
        'DJANGO_SECRET_KEY environment variable must be set to a strong random '
        'value before running with DEBUG=False.'
    )

_allowed_hosts_env = os.environ.get('DJANGO_ALLOWED_HOSTS', '')
ALLOWED_HOSTS = (
    [h.strip() for h in _allowed_hosts_env.split(',') if h.strip()]
    if _allowed_hosts_env
    else (['localhost', '127.0.0.1'] if DEBUG else [])
)

CSRF_TRUSTED_ORIGINS = _csv_env(os.environ, 'DJANGO_CSRF_TRUSTED_ORIGINS')
SECURE_PROXY_SSL_HEADER = _secure_proxy_ssl_header(os.environ, not DEBUG)
SECURE_SSL_REDIRECT = _env_bool(os.environ, 'DJANGO_SECURE_SSL_REDIRECT', not DEBUG)
SESSION_COOKIE_SECURE = _env_bool(os.environ, 'DJANGO_SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = _env_bool(os.environ, 'DJANGO_CSRF_COOKIE_SECURE', not DEBUG)
SECURE_HSTS_SECONDS = _env_int(
    os.environ,
    'DJANGO_SECURE_HSTS_SECONDS',
    0,
    minimum=0,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool(
    os.environ,
    'DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS',
    False,
)
SECURE_HSTS_PRELOAD = _env_bool(os.environ, 'DJANGO_SECURE_HSTS_PRELOAD', False)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'django_filters',
    'drf_spectacular',
    'core',
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'core.middleware.KioskCORSMiddleware',  # REVIEW
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',  # language from cookie/Accept-Language (anonymous + pre-auth)
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.RegionalMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'retailops.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.i18n',
                'core.context_processors.system_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'retailops.wsgi.application'


def _db_conn_max_age(env):
    raw_value = env.get('DB_CONN_MAX_AGE', '60')
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError('DB_CONN_MAX_AGE must be a non-negative integer.') from exc
    if value < 0:
        raise RuntimeError('DB_CONN_MAX_AGE must be a non-negative integer.')
    return value


def _sqlite_database_config(env):
    db_name = (env.get('DB_NAME') or '').strip()
    db_path = Path(db_name) if db_name else BASE_DIR / 'db.sqlite3'
    if db_name and not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': db_path,
        'ATOMIC_REQUESTS': False,
    }


def _postgres_database_config(env):
    required = ('DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST')
    missing = [name for name in required if not (env.get(name) or '').strip()]
    if missing:
        raise RuntimeError(
            'DB_ENGINE=postgres requires these environment variables: '
            f'{", ".join(missing)}.'
        )

    config = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env['DB_NAME'].strip(),
        'USER': env['DB_USER'].strip(),
        'PASSWORD': env['DB_PASSWORD'],
        'HOST': env['DB_HOST'].strip(),
        'PORT': (env.get('DB_PORT') or '5432').strip(),
        'CONN_MAX_AGE': _db_conn_max_age(env),
        'ATOMIC_REQUESTS': False,
    }
    _apply_postgres_sslmode(config, env)
    return config


def _apply_postgres_sslmode(config, env):
    sslmode = (env.get('DB_SSLMODE') or '').strip()
    if sslmode:
        config.setdefault('OPTIONS', {})
        config['OPTIONS'].setdefault('sslmode', sslmode)


def _database_config_from_env(env):
    """
    Build the default database config.

    Default local profile: SQLite at BASE_DIR/db.sqlite3.
    Preferred remote profile: DATABASE_URL for PostgreSQL.
    Fallback remote profile: DB_ENGINE=postgres plus DB_* variables.
    """
    database_url = (env.get('DATABASE_URL') or '').strip()
    if database_url:
        if dj_database_url is None:
            raise RuntimeError(
                'DATABASE_URL is set, but dj-database-url is not installed. '
                'Run `pip install -r requirements.txt` first.'
            )
        config = dj_database_url.parse(
            database_url,
            conn_max_age=_db_conn_max_age(env),
        )
        engine = config.get('ENGINE')
        if engine not in {
            'django.db.backends.postgresql',
            'django.db.backends.sqlite3',
        }:
            raise RuntimeError(
                'DATABASE_URL uses an unsupported database backend. '
                'RetailOps currently supports SQLite and PostgreSQL.'
            )
        config.setdefault('ATOMIC_REQUESTS', False)
        if engine == 'django.db.backends.postgresql':
            _apply_postgres_sslmode(config, env)
        return config

    db_engine = (env.get('DB_ENGINE') or 'sqlite').strip().lower()
    if db_engine in {'sqlite', 'sqlite3'}:
        return _sqlite_database_config(env)
    if db_engine in {'postgres', 'postgresql', 'pgsql'}:
        return _postgres_database_config(env)

    raise RuntimeError(
        f'Unsupported DB_ENGINE={db_engine!r}. '
        'RetailOps currently supports sqlite and postgres.'
    )


DATABASES = {
    'default': _database_config_from_env(os.environ),
}

AUTH_USER_MODEL = 'core.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# UI languages offered by the back-office. The per-user choice lives on
# User.language and is activated by core.middleware.RegionalMiddleware; anonymous
# visitors (e.g. the login page) get the cookie/Accept-Language value via
# Django's LocaleMiddleware. Keep this list in sync with User.language choices
# and the language switcher in templates/core/base.html.
LANGUAGES = [
    ('en', 'English'),
    ('es', 'Español'),
]

LOCALE_PATHS = [BASE_DIR / 'locale']

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = os.environ.get('MEDIA_URL', 'media/')
_MEDIA_ROOT_ENV = (os.environ.get('MEDIA_ROOT') or '').strip()
MEDIA_ROOT = Path(_MEDIA_ROOT_ENV) if _MEDIA_ROOT_ENV else BASE_DIR / 'media'
if _MEDIA_ROOT_ENV and not MEDIA_ROOT.is_absolute():
    MEDIA_ROOT = BASE_DIR / MEDIA_ROOT

def _first_env(env, *names, default=None):
    for name in names:
        value = (env.get(name) or '').strip()
        if value:
            return value
    return default


def _staticfiles_storage_config():
    return {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    }


def _local_media_storage_config():
    return {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    }


def _normalized_media_storage_backend(env):
    backend = (env.get('MEDIA_STORAGE_BACKEND') or 'local').strip().lower()
    if backend in {'local', 'filesystem', 'file', 'fs'}:
        return 'local'
    if backend in {'gcs', 'google_cloud_storage', 'google-cloud-storage'}:
        return 'gcs'
    if backend in {'s3', 's3-compatible', 's3_compatible'}:
        return 's3'
    return backend


def _gcs_media_storage_options(env):
    generic_bucket = _first_env(env, 'MEDIA_GCS_BUCKET_NAME', 'GS_BUCKET_NAME')
    default_bucket = _first_env(
        env,
        'MEDIA_GCS_DEFAULT_BUCKET_NAME',
        default=generic_bucket,
    )
    product_bucket = _first_env(
        env,
        'MEDIA_GCS_PRODUCT_BUCKET_NAME',
        'MEDIA_GCS_PUBLIC_BUCKET_NAME',
        default=generic_bucket,
    )
    receipt_bucket = _first_env(
        env,
        'MEDIA_GCS_RECEIPT_BUCKET_NAME',
        'MEDIA_GCS_PRIVATE_BUCKET_NAME',
        default=generic_bucket,
    )

    if not product_bucket or not receipt_bucket:
        raise RuntimeError(
            'MEDIA_STORAGE_BACKEND=gcs requires MEDIA_GCS_BUCKET_NAME or both '
            'MEDIA_GCS_PRODUCT_BUCKET_NAME and MEDIA_GCS_RECEIPT_BUCKET_NAME.'
        )

    return {
        'project_id': _first_env(env, 'MEDIA_GCS_PROJECT_ID', 'GS_PROJECT_ID'),
        'default_bucket_name': default_bucket or product_bucket,
        'product_bucket_name': product_bucket,
        'receipt_bucket_name': receipt_bucket,
        'default_querystring_auth': _env_bool(env, 'MEDIA_GCS_DEFAULT_SIGNED_URLS', True),
        'product_querystring_auth': not _env_bool(env, 'MEDIA_GCS_PRODUCT_PUBLIC', True),
        'receipt_querystring_auth': _env_bool(env, 'MEDIA_GCS_RECEIPT_SIGNED_URLS', True),
        'file_overwrite': _env_bool(env, 'MEDIA_GCS_FILE_OVERWRITE', False),
        'signed_url_expiration': _env_int(
            env,
            'MEDIA_GCS_SIGNED_URL_EXPIRATION',
            900,
            minimum=1,
            maximum=604800,
        ),
        'iam_sign_blob': _env_bool(env, 'MEDIA_GCS_IAM_SIGN_BLOB', False),
        'sa_email': _first_env(
            env,
            'MEDIA_GCS_SERVICE_ACCOUNT_EMAIL',
            'MEDIA_GCS_SA_EMAIL',
            'GS_SA_EMAIL',
        ),
        'default_custom_endpoint': _first_env(
            env,
            'MEDIA_GCS_DEFAULT_CUSTOM_ENDPOINT',
            'MEDIA_GCS_CUSTOM_ENDPOINT',
            'GS_CUSTOM_ENDPOINT',
        ),
        'product_custom_endpoint': _first_env(
            env,
            'MEDIA_GCS_PRODUCT_CUSTOM_ENDPOINT',
            'MEDIA_GCS_PUBLIC_CUSTOM_ENDPOINT',
        ),
        'receipt_custom_endpoint': _first_env(
            env,
            'MEDIA_GCS_RECEIPT_CUSTOM_ENDPOINT',
            'MEDIA_GCS_PRIVATE_CUSTOM_ENDPOINT',
        ),
        'default_acl': _first_env(env, 'MEDIA_GCS_DEFAULT_ACL', 'GS_DEFAULT_ACL'),
        'product_default_acl': _first_env(env, 'MEDIA_GCS_PRODUCT_DEFAULT_ACL'),
        'receipt_default_acl': _first_env(env, 'MEDIA_GCS_RECEIPT_DEFAULT_ACL'),
        'product_cache_control': _first_env(
            env,
            'MEDIA_GCS_PRODUCT_CACHE_CONTROL',
            default='public, max-age=31536000, immutable',
        ),
        'receipt_cache_control': _first_env(
            env,
            'MEDIA_GCS_RECEIPT_CACHE_CONTROL',
            default='private, max-age=0, no-store',
        ),
    }


def _s3_media_storage_options(env):
    generic_bucket = _first_env(env, 'MEDIA_S3_BUCKET_NAME', 'AWS_STORAGE_BUCKET_NAME')
    default_bucket = _first_env(
        env,
        'MEDIA_S3_DEFAULT_BUCKET_NAME',
        default=generic_bucket,
    )
    product_bucket = _first_env(
        env,
        'MEDIA_S3_PRODUCT_BUCKET_NAME',
        'MEDIA_S3_PUBLIC_BUCKET_NAME',
        default=generic_bucket,
    )
    receipt_bucket = _first_env(
        env,
        'MEDIA_S3_RECEIPT_BUCKET_NAME',
        'MEDIA_S3_PRIVATE_BUCKET_NAME',
        default=generic_bucket,
    )
    endpoint_url = _first_env(env, 'MEDIA_S3_ENDPOINT_URL', 'AWS_S3_ENDPOINT_URL')
    access_key = _first_env(env, 'MEDIA_S3_ACCESS_KEY_ID', 'AWS_S3_ACCESS_KEY_ID')
    secret_key = _first_env(env, 'MEDIA_S3_SECRET_ACCESS_KEY', 'AWS_S3_SECRET_ACCESS_KEY')

    missing = []
    if not endpoint_url:
        missing.append('MEDIA_S3_ENDPOINT_URL')
    if not product_bucket or not receipt_bucket:
        missing.append('MEDIA_S3_BUCKET_NAME or both MEDIA_S3_PRODUCT_BUCKET_NAME and MEDIA_S3_RECEIPT_BUCKET_NAME')
    if not access_key:
        missing.append('MEDIA_S3_ACCESS_KEY_ID')
    if not secret_key:
        missing.append('MEDIA_S3_SECRET_ACCESS_KEY')
    if missing:
        raise RuntimeError(
            'MEDIA_STORAGE_BACKEND=s3 requires these environment variables: '
            f'{", ".join(missing)}.'
        )

    return {
        'endpoint_url': endpoint_url,
        'access_key': access_key,
        'secret_key': secret_key,
        'region_name': _first_env(env, 'MEDIA_S3_REGION_NAME', default='us-east-1'),
        'default_bucket_name': default_bucket or product_bucket,
        'product_bucket_name': product_bucket,
        'receipt_bucket_name': receipt_bucket,
        'default_querystring_auth': _env_bool(env, 'MEDIA_S3_DEFAULT_SIGNED_URLS', True),
        'product_querystring_auth': not _env_bool(env, 'MEDIA_S3_PRODUCT_PUBLIC', True),
        'receipt_querystring_auth': _env_bool(env, 'MEDIA_S3_RECEIPT_SIGNED_URLS', True),
        'file_overwrite': _env_bool(env, 'MEDIA_S3_FILE_OVERWRITE', False),
        'signed_url_expiration': _env_int(
            env,
            'MEDIA_S3_SIGNED_URL_EXPIRATION',
            900,
            minimum=1,
            maximum=604800,
        ),
        'addressing_style': _first_env(env, 'MEDIA_S3_ADDRESSING_STYLE', default='path'),
        'signature_version': _first_env(env, 'MEDIA_S3_SIGNATURE_VERSION', default='s3v4'),
        'default_acl': _first_env(env, 'MEDIA_S3_DEFAULT_ACL', 'AWS_DEFAULT_ACL'),
        'product_default_acl': _first_env(env, 'MEDIA_S3_PRODUCT_DEFAULT_ACL'),
        'receipt_default_acl': _first_env(env, 'MEDIA_S3_RECEIPT_DEFAULT_ACL'),
        'product_cache_control': _first_env(
            env,
            'MEDIA_S3_PRODUCT_CACHE_CONTROL',
            default='public, max-age=31536000, immutable',
        ),
        'receipt_cache_control': _first_env(
            env,
            'MEDIA_S3_RECEIPT_CACHE_CONTROL',
            default='private, max-age=0, no-store',
        ),
    }


def _media_storage_config_from_env(env):
    """
    Build Django's STORAGES setting.

    Default profile: local filesystem under MEDIA_ROOT.
    Optional media-platform profiles: Google Cloud Storage or S3-compatible
    storage routed by path prefix.
    """
    backend = _normalized_media_storage_backend(env)

    if backend == 'local':
        return {
            'default': _local_media_storage_config(),
            'staticfiles': _staticfiles_storage_config(),
        }

    if backend == 'gcs':
        return {
            'default': {
                'BACKEND': 'retailops.storage.RoutedGoogleCloudStorage',
                'OPTIONS': _gcs_media_storage_options(env),
            },
            'staticfiles': _staticfiles_storage_config(),
        }

    if backend == 's3':
        return {
            'default': {
                'BACKEND': 'retailops.storage.RoutedS3Storage',
                'OPTIONS': _s3_media_storage_options(env),
            },
            'staticfiles': _staticfiles_storage_config(),
        }

    raise RuntimeError(
        f'Unsupported MEDIA_STORAGE_BACKEND={backend!r}. '
        'RetailOps currently supports local, gcs, and s3.'
    )


MEDIA_STORAGE_BACKEND = _normalized_media_storage_backend(os.environ)
STORAGES = _media_storage_config_from_env(os.environ)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── DRF authentication / renderer classes — differ between dev and production ─
# SessionAuthentication and BrowsableAPIRenderer are dev conveniences only.
# They are stripped automatically when DEBUG=False so production serves pure JSON
# and no HTML renderer is ever exposed.
_DRF_AUTHENTICATION_CLASSES = ['rest_framework.authentication.TokenAuthentication']
_DRF_RENDERER_CLASSES = ['rest_framework.renderers.JSONRenderer']

if DEBUG:
    _DRF_AUTHENTICATION_CLASSES.append('rest_framework.authentication.SessionAuthentication')
    _DRF_RENDERER_CLASSES.append('rest_framework.renderers.BrowsableAPIRenderer')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': _DRF_AUTHENTICATION_CLASSES,
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'api.pagination.CappedPageNumberPagination',
    'PAGE_SIZE': 25,
    'DEFAULT_RENDERER_CLASSES': _DRF_RENDERER_CLASSES,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'NON_FIELD_ERRORS_KEY': 'errors',
    'EXCEPTION_HANDLER': 'api.exceptions.custom_exception_handler',
    # Global ceiling for all authenticated endpoints.
    # AnonRateThrottle is intentionally absent from the global list: the only
    # anonymous endpoint is POST /auth/token/, which is throttled directly via
    # LoginRateThrottle (scope='login') on ObtainTokenView.
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'user':             '600/min',  # global ceiling for all authenticated endpoints
        'login':            '20/min',   # IP-based; POST /auth/token/ only
        'password_reset':   '5/min',    # IP-based; password-reset request + confirm
        'password_change':  '10/min',   # per-user; change-password action
        'order_transition': '60/min',   # per-user; submit/confirm/ship/deliver/cancel/refund
        'inventory_adjust': '30/min',   # per-user; manual stock adjustments
        'ocr_verify':       '12/min',   # per-station/per-user; receipt OCR verification
        # Kiosk-specific (per-station, not per-user)
        'kiosk_identify':   '60/min',
        'kiosk_scan':       '120/min',
        'kiosk_checkout':   '30/min',
        'kiosk_poll':       '60/min',
    },
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# Default country for Customer model
DEFAULT_COUNTRY = 'United States'

# ── OpenAPI schema (drf-spectacular) ──────────────────────────────────────────
# Schema available at:
#   /api/v1/schema/           — raw OpenAPI 3 YAML/JSON
#   /api/v1/schema/swagger/   — Swagger UI
#   /api/v1/schema/redoc/     — ReDoc UI
SPECTACULAR_SETTINGS = {
    'TITLE': 'RetailOps API',
    'DESCRIPTION': (
        'Unified Retail & E-Commerce Order Management System. '
        'Internal API for managing customers, products, orders, payments, and inventory.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,  # exclude the schema endpoint itself from the schema
    'COMPONENT_SPLIT_REQUEST': True,  # separate request/response schemas for read/write splits
}

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# ── Email ─────────────────────────────────────────────────────────────────────
# Default: console backend prints reset links to the terminal in development.
# Override DJANGO_EMAIL_BACKEND with a real SMTP backend for production.
#
#   DJANGO_EMAIL_BACKEND  — dotted Python path (default: console)
#   DJANGO_EMAIL_HOST     — SMTP hostname
#   DJANGO_EMAIL_PORT     — SMTP port (default: 587)
#   DJANGO_EMAIL_USE_TLS  — 'True' or 'False' (default: True)
#   DJANGO_EMAIL_HOST_USER / DJANGO_EMAIL_HOST_PASSWORD
#   DJANGO_DEFAULT_FROM_EMAIL — e.g. "RetailOps <noreply@retailops.com>"

EMAIL_BACKEND = os.environ.get(
    'DJANGO_EMAIL_BACKEND',
    'retailops.email_backend.DecodedConsoleEmailBackend',
)
EMAIL_HOST          = os.environ.get('DJANGO_EMAIL_HOST',          'localhost')
EMAIL_PORT          = int(os.environ.get('DJANGO_EMAIL_PORT',      587))
EMAIL_USE_TLS       = os.environ.get('DJANGO_EMAIL_USE_TLS',       'True') == 'True'
EMAIL_HOST_USER     = os.environ.get('DJANGO_EMAIL_HOST_USER',     '')
EMAIL_HOST_PASSWORD = os.environ.get('DJANGO_EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL  = os.environ.get('DJANGO_DEFAULT_FROM_EMAIL',  'noreply@retailops.local')

