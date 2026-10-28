import os
import runpy
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase, override_settings


class DeploymentTests(TestCase):
    def test_health_checks_database(self):
        self.assertEqual(self.client.get('/health/').json(), {'status': 'ok'})

    def test_health_failure_does_not_expose_database_error(self):
        with patch('learning.health.connections') as connections:
            connections.__getitem__.return_value.cursor.side_effect = RuntimeError('private credentials')
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'private credentials', status_code=503)

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r'^health/$'])
    def test_health_does_not_redirect_but_login_does(self):
        self.assertEqual(self.client.get('/health/').status_code, 200)
        self.assertEqual(self.client.get('/accounts/login/').status_code, 301)

    def test_railway_database_storage_and_proxy_settings(self):
        environment = {
            'DJANGO_SECRET_KEY': 'test-only-secret', 'DJANGO_DEBUG': '0',
            'DATABASE_URL': 'postgresql://reader:p%40ss@database.internal:5432/keel?sslmode=require',
            'RAILWAY_ENVIRONMENT_ID': 'test', 'RAILWAY_PUBLIC_DOMAIN': 'keel.up.railway.app',
            'DJANGO_TRUST_PROXY': '1', 'DJANGO_SECURE_COOKIES': '1',
            'AWS_STORAGE_BUCKET_NAME': 'private-test', 'AWS_ACCESS_KEY_ID': 'test-key',
            'AWS_SECRET_ACCESS_KEY': 'test-secret', 'AWS_S3_ENDPOINT_URL': 'https://objects.example.com',
        }
        settings_path = Path(__file__).resolve().parent.parent / 'config/settings.py'
        with patch.dict(os.environ, environment, clear=True), patch('dotenv.load_dotenv'):
            config = runpy.run_path(str(settings_path))
        db = config['DATABASES']['default']
        self.assertEqual(db['PASSWORD'], 'p@ss')
        self.assertEqual(db['HOST'], 'database.internal')
        self.assertEqual(db['OPTIONS'], {'sslmode': 'require'})
        self.assertIn('keel.up.railway.app', config['ALLOWED_HOSTS'])
        self.assertIn('healthcheck.railway.app', config['ALLOWED_HOSTS'])
        self.assertEqual(config['SECURE_PROXY_SSL_HEADER'], ('HTTP_X_FORWARDED_PROTO', 'https'))
        self.assertTrue(config['SESSION_COOKIE_SECURE'])
        storage = config['STORAGES']['default']
        self.assertTrue(storage['OPTIONS']['querystring_auth'])
        self.assertFalse(storage['OPTIONS']['file_overwrite'])
        from storages.backends.s3 import S3Storage
        bucket = S3Storage(**storage['OPTIONS'])
        self.assertEqual(bucket.bucket_name, 'private-test')
