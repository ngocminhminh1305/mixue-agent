"""Hosted connection configuration and public probe boundaries."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from bobaops.core import database, health
from bobaops.main import app
from auth_support import TEST_SETTINGS


class DeploymentTests(unittest.TestCase):
    def tearDown(self):
        database.get_engine.cache_clear()

    def test_render_origin_is_automatic_and_custom_override_wins(self):
        from bobaops.auth import security
        values = {'AUTH_USERNAME': TEST_SETTINGS.username, 'AUTH_PASSWORD_HASH': TEST_SETTINGS.password_hash,
                  'AUTH_SESSION_SECRET': TEST_SETTINGS.secret, 'RENDER_EXTERNAL_URL': 'https://shop.onrender.com'}
        with patch.object(security, 'environment', return_value=values):
            self.assertEqual(security.load_settings().origin, 'https://shop.onrender.com')
            values['AUTH_ALLOWED_ORIGIN'] = 'https://myshop.example'
            self.assertEqual(security.load_settings().origin, 'https://myshop.example')

    def test_hosted_url_keeps_credentials_and_tls_query_without_fallback_to_local(self):
        for scheme in ('postgres', 'postgresql', 'postgresql+psycopg'):
            with patch.object(database, 'environment', return_value={
                    'DATABASE_URL': scheme + '://owner:p%40ss@cloud.example:6543/shop?sslmode=require',
                    'POSTGRES_HOST': 'local-should-not-win'}):
                url = database.database_url()
            self.assertEqual(url.drivername, 'postgresql+psycopg')
            self.assertEqual(url.host, 'cloud.example')
            self.assertEqual(url.password, 'p@ss')
            self.assertEqual(url.port, 6543)
            self.assertEqual(url.query['sslmode'], 'require')

    def test_local_connection_and_tls_engine_options(self):
        database.get_engine.cache_clear()
        with patch.object(database, 'environment', return_value={
                'POSTGRES_HOST': 'db', 'POSTGRES_PORT': '5432', 'POSTGRES_SSLMODE': 'verify-full',
                'POSTGRES_SSLROOTCERT': '/certs/root.crt'}), patch.object(database, 'create_engine') as create:
            database.get_engine()
        args = create.call_args
        self.assertEqual(args.args[0].host, 'db')
        self.assertEqual(args.kwargs['connect_args']['sslmode'], 'verify-full')
        self.assertEqual(args.kwargs['connect_args']['sslrootcert'], '/certs/root.crt')
        self.assertLessEqual(args.kwargs['pool_size'] + args.kwargs['max_overflow'], 5)

    def test_non_postgres_url_is_rejected(self):
        with patch.object(database, 'environment', return_value={'DATABASE_URL': 'sqlite:///bad.db'}):
            with self.assertRaisesRegex(ValueError, 'PostgreSQL'):
                database.database_url()

    def test_public_liveness_is_minimal_and_never_touches_database(self):
        with patch.object(health, 'get_engine') as engine:
            response = TestClient(app).get('/health/live')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        engine.assert_not_called()

    def test_readiness_requires_auth_storage_and_current_migrations(self):
        expected = health.ScriptDirectory(str(health.ROOT / 'migrations')).get_current_head()
        engine = MagicMock()
        rows = engine.connect.return_value.__enter__.return_value.execute.return_value
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(health, 'PRIVATE_DATA_DIR', Path(directory)), \
                patch.object(health, 'load_settings', return_value=TEST_SETTINGS), \
                patch.object(health, 'storage_backend', return_value='postgres'), \
                patch.object(health, 'get_engine', return_value=engine):
            client = TestClient(app)
            rows.scalars.return_value = [expected]
            self.assertEqual(client.get('/health/ready').status_code, 200)
            rows.scalars.return_value = ['0001']
            self.assertEqual(client.get('/health/ready').status_code, 503)
            rows.scalars.return_value = [expected]
            with patch.object(health, 'storage_backend', return_value='json'):
                self.assertEqual(client.get('/health/ready').status_code, 503)
            with patch.object(health, 'load_settings') as settings:
                settings.return_value.configured = False
                self.assertEqual(client.get('/health/ready').status_code, 503)
            with patch.object(health.os, 'access', return_value=False):
                self.assertEqual(client.get('/health/ready').status_code, 503)
            # Cloud photo storage must not require a writable persistent filesystem.
            with patch.object(health, 'PhotoStore') as photos, patch.object(health.os, 'access', return_value=False):
                photos.return_value.backend = 'supabase'
                # Avoid the global os.access patch influencing Alembic's own path check.
                with patch.object(health, 'ScriptDirectory') as script:
                    script.return_value.get_current_head.return_value = expected
                    self.assertEqual(client.get('/health/ready').status_code, 200)
                    photos.return_value.check_private_bucket.assert_called_once()
            engine.connect.side_effect = OperationalError('private query', {}, Exception('secret password'))
            response = client.get('/health/ready')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {'status': 'unavailable'})
            self.assertNotIn('secret', response.text)
