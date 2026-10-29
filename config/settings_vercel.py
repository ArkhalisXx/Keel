"""Serverless profile. Local Docker continues to use config.settings."""
import os
from urllib.parse import parse_qs, unquote, urlsplit

# Validate before importing local settings so a read-only function never tries
# to generate .local-secret-key or silently falls back to an ephemeral database.
_required = ('DJANGO_SECRET_KEY', 'DATABASE_URL', 'AWS_STORAGE_BUCKET_NAME',
             'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_S3_ENDPOINT_URL',
             'AWS_S3_REGION_NAME')
if any(not os.environ.get(name, '').strip() for name in _required):
    raise ValueError('Configure the Vercel secret, PostgreSQL URL, and private S3 storage variables.')

from .settings import *  # noqa: E402,F403
from botocore.config import Config  # noqa: E402

DEBUG = False
_url = urlsplit(os.environ['DATABASE_URL'])
if _url.scheme not in ('postgres', 'postgresql') or not _url.hostname or not _url.path.lstrip('/'):
    raise ValueError('DATABASE_URL must point to a persistent PostgreSQL database.')
_sslmode = parse_qs(_url.query).get('sslmode', ['require'])[0]
if _sslmode not in ('require', 'verify-ca', 'verify-full'):
    raise ValueError('Use a TLS-protected PostgreSQL connection with sslmode=require or stronger.')
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': unquote(_url.path.lstrip('/')),
    'USER': unquote(_url.username or ''), 'PASSWORD': unquote(_url.password or ''),
    'HOST': _url.hostname, 'PORT': _url.port or 5432,
    'CONN_MAX_AGE': 0, 'DISABLE_SERVER_SIDE_CURSORS': True,
    'OPTIONS': {'sslmode': _sslmode, 'connect_timeout': 5, 'prepare_threshold': None},
}}

ALLOWED_HOSTS = [host.strip() for host in ALLOWED_HOSTS if host.strip()]  # noqa: F405
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in CSRF_TRUSTED_ORIGINS if origin.strip()]  # noqa: F405
for _name in ('VERCEL_URL', 'VERCEL_PROJECT_PRODUCTION_URL'):
    _host = os.environ.get(_name, '').strip()
    if _host and '/' not in _host:
        ALLOWED_HOSTS.append(_host)
        CSRF_TRUSTED_ORIGINS.append('https://' + _host)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r'^health/$']
SECURE_HSTS_SECONDS = 3600

_endpoint = os.environ['AWS_S3_ENDPOINT_URL']
if urlsplit(_endpoint).scheme != 'https':
    raise ValueError('Private object storage must use an HTTPS endpoint.')
STORAGES = {
    'default': {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'bucket_name': os.environ['AWS_STORAGE_BUCKET_NAME'],
            'access_key': os.environ['AWS_ACCESS_KEY_ID'],
            'secret_key': os.environ['AWS_SECRET_ACCESS_KEY'],
            'endpoint_url': _endpoint,
            'region_name': os.environ['AWS_S3_REGION_NAME'],
            'default_acl': None, 'file_overwrite': False,
            'querystring_auth': True, 'querystring_expire': 60,
            'max_memory_size': 1024 * 1024,
            'client_config': Config(
                signature_version='s3v4', connect_timeout=5, read_timeout=10,
                retries={'total_max_attempts': 2}, s3={'addressing_style': 'path'},
                request_checksum_calculation='when_required',
                response_checksum_validation='when_required'),
        },
    },
    # Vercel's Django adapter runs collectstatic and serves these assets on its CDN.
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
STATIC_URL = '/static/'
FILE_UPLOAD_TEMP_DIR = '/tmp'
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 512 * 1024
SERVERLESS_JOBS = True
OCR_ENABLED = False
DOCUMENT_MAX_UPLOAD_MB = 3
DOCUMENT_MAX_PDF_PAGES = 100
DOCUMENT_MAX_TEXT_CHARS = 500000
DOCUMENT_PROCESSING_TIMEOUT = 40
# The serverless entrypoints execute one bounded batch directly, never a daemon.
CELERY_TASK_ALWAYS_EAGER = False
