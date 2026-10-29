import os
import runpy
from unittest.mock import patch
from django.test import TestCase


class VercelConfigurationTests(TestCase):
    def profile(self, **changes):
        values = {
            'DJANGO_SECRET_KEY': 'test-only-secret-not-used-on-a-live-site',
            'DATABASE_URL': 'postgresql://reader:p%40ss@pool.example.com:5432/keel?sslmode=require',
            'AWS_STORAGE_BUCKET_NAME': 'private-documents',
            'AWS_ACCESS_KEY_ID': 'test-access', 'AWS_SECRET_ACCESS_KEY': 'test-secret',
            'AWS_S3_ENDPOINT_URL': 'https://storage.example.com', 'AWS_S3_REGION_NAME': 'test-region',
            'VERCEL_URL': 'test-preview.vercel.app',
        }
        values.update(changes)
        with patch.dict(os.environ, values, clear=True):
            return runpy.run_module('config.settings_vercel')

    def test_requires_persistent_data_and_secret(self):
        for variable in ['DJANGO_SECRET_KEY', 'DATABASE_URL', 'AWS_STORAGE_BUCKET_NAME', 'AWS_SECRET_ACCESS_KEY']:
            with self.subTest(variable=variable), self.assertRaises(ValueError):
                self.profile(**{variable: ''})

    def test_tls_database_and_proxy_settings(self):
        config = self.profile()
        self.assertFalse(config['DEBUG'])
        database = config['DATABASES']['default']
        self.assertEqual(database['PASSWORD'], 'p@ss')
        self.assertEqual(database['CONN_MAX_AGE'], 0)
        self.assertEqual(database['OPTIONS']['sslmode'], 'require')
        self.assertIsNone(database['OPTIONS']['prepare_threshold'])
        self.assertIn('test-preview.vercel.app', config['ALLOWED_HOSTS'])
        self.assertIn('https://test-preview.vercel.app', config['CSRF_TRUSTED_ORIGINS'])
        self.assertTrue(config['SESSION_COOKIE_SECURE'])
        self.assertEqual(config['SECURE_PROXY_SSL_HEADER'], ('HTTP_X_FORWARDED_PROTO', 'https'))
        self.assertTrue(config['SERVERLESS_JOBS'])
        self.assertFalse(config['OCR_ENABLED'])
        self.assertEqual(config['DOCUMENT_MAX_UPLOAD_MB'], 3)

    def test_rejects_insecure_database_or_storage(self):
        with self.assertRaises(ValueError):
            self.profile(DATABASE_URL='sqlite:///tmp/local.sqlite3')
        with self.assertRaises(ValueError):
            self.profile(DATABASE_URL='postgresql://user:password@db.example.com/keel?sslmode=disable')
        with self.assertRaises(ValueError):
            self.profile(AWS_S3_ENDPOINT_URL='http://storage.example.com')

    def test_private_short_lived_storage_urls(self):
        config = self.profile()
        options = config['STORAGES']['default']['OPTIONS']
        from storages.backends.s3 import S3Storage
        storage = S3Storage(**options)
        self.assertTrue(storage.querystring_auth)
        self.assertEqual(storage.querystring_expire, 60)
        self.assertIsNone(storage.default_acl)
        self.assertFalse(storage.file_overwrite)
        self.assertEqual(storage.client_config.read_timeout, 10)

    def test_health_checks_database_and_does_not_cache(self):
        response = self.client.get('/health/')
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(response['Cache-Control'], 'no-store')
        with patch('learning.health.connection.cursor', side_effect=RuntimeError('private-details')):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'private-details', status_code=503)
