"""Authentication boundaries, real password checks and revocation regressions."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy.exc import OperationalError

from bobaops.main import app
from bobaops.auth import security
from scripts.setup_login import configure
from auth_support import TEST_PASSWORD, TEST_SETTINGS, configure_auth


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.database = configure_auth(self)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def challenge(self, client=None):
        client = client or self.client
        response = client.get('/api/auth/login')
        self.assertEqual(response.status_code, 200)
        return {'X-CSRF-Token': response.json()['csrf_token']}

    def login(self, password=TEST_PASSWORD, username='test-owner', target='/', **kwargs):
        return self.client.post('/api/auth/login', headers=self.challenge(),
                                json={'username': username, 'password': password, 'next': target}, **kwargs)

    def signed_in(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        return self.client.get('/api/auth/session').json()['csrf_token']

    def test_every_private_page_and_api_is_protected_before_its_handler(self):
        for path in ('/', '/upload', '/sales', '/inventory', '/chat', '/setup', '/dashboard', '/docs', '/redoc', '/openapi.json'):
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 303, path)
            self.assertTrue(response.headers['location'].startswith('/login'))
            self.assertEqual(response.headers['cache-control'], 'no-store')
        for method, path in (('GET', '/api/shop-config'), ('GET', '/api/status'),
                             ('GET', '/api/sales/images/private-photo'), ('POST', '/api/extract'),
                             ('POST', '/api/assistant/chat'), ('POST', '/api/inventory/purchases'),
                             ('POST', '/api/future-endpoint')):
            self.assertEqual(self.client.request(method, path).status_code, 401, path)
        self.assertEqual(self.client.get('/login').status_code, 200)
        self.assertEqual(self.client.get('/assets/login.js').status_code, 200)

    def test_unconfigured_account_fails_closed(self):
        with patch.object(security, 'load_settings', return_value=replace(TEST_SETTINGS, password_hash='')):
            self.assertEqual(self.client.get('/api/status').status_code, 503)
            self.assertEqual(self.client.get('/api/auth/login').status_code, 503)
            response = self.client.get('/upload', follow_redirects=False)
            self.assertEqual(response.headers['location'], '/login')
        for settings in (replace(TEST_SETTINGS, secret='short'), replace(TEST_SETTINGS, ttl=0),
                         replace(TEST_SETTINGS, password_hash='not-a-hash')):
            self.assertFalse(settings.configured)

    def test_valid_login_cookie_flags_and_no_secrets_in_response(self):
        response = self.login(target='/sales?date=2026-10-31')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['next'], '/sales?date=2026-10-31')
        cookie = response.headers['set-cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=strict', cookie)
        self.assertIn('Max-Age=28800', cookie)
        self.assertNotIn('Domain=', cookie)
        session = self.client.get('/api/auth/session')
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()['username'], 'test-owner')
        self.assertEqual(self.client.get('/').status_code, 200)
        for secret in (TEST_PASSWORD, TEST_SETTINGS.password_hash, TEST_SETTINGS.secret):
            self.assertNotIn(secret, response.text + session.text)
        self.assertEqual(self.client.get('/api/auth/register').status_code, 404)

    def test_login_errors_are_generic_and_attempts_are_limited(self):
        a = self.login(password='wrong')
        b = self.login(username='someone-else')
        self.assertEqual(a.status_code, 401)
        self.assertEqual(a.json(), b.json())
        for _ in range(3):
            self.assertEqual(self.login(password='wrong').status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['retry-after'], '900')

    def test_login_requires_csrf_challenge_and_same_origin(self):
        body = {'username': 'test-owner', 'password': TEST_PASSWORD}
        self.assertEqual(self.client.post('/api/auth/login', json=body).status_code, 403)
        headers = self.challenge()
        headers['Origin'] = 'https://another-site.example'
        self.assertEqual(self.client.post('/api/auth/login', json=body, headers=headers).status_code, 403)
        other = TestClient(app)
        self.addCleanup(other.close)
        self.assertEqual(other.post('/api/auth/login', json=body, headers=self.challenge()).status_code, 403)

    def test_expired_challenge_cannot_log_in(self):
        with patch.object(TimestampSigner, 'get_timestamp', return_value=1):
            headers = self.challenge()
        self.assertEqual(self.client.post('/api/auth/login', headers=headers,
                                         json={'username': 'test-owner', 'password': TEST_PASSWORD}).status_code, 403)

    def test_all_writes_require_session_csrf_even_with_a_valid_cookie(self):
        csrf = self.signed_in()
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            self.assertEqual(self.client.request(method, '/api/future-endpoint', json={}).status_code, 403)
        # Even multipart receipt uploads are rejected before image/model processing.
        with patch('httpx.AsyncClient.post') as provider:
            self.assertEqual(self.client.post('/api/extract', files={'image': ('photo.png', b'bad')}).status_code, 403)
            provider.assert_not_called()
        response = self.client.post('/api/future-endpoint', headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 404)  # Passed auth, reached the router.
        for headers in ({'X-CSRF-Token': csrf, 'Origin': 'https://evil.example'},
                        {'X-CSRF-Token': csrf, 'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.client.post('/api/auth/logout', headers=headers).status_code, 403)

    def test_csrf_token_from_another_session_is_rejected(self):
        csrf = self.signed_in()
        other = TestClient(app)
        self.addCleanup(other.close)
        response = other.post('/api/auth/login', headers=self.challenge(other),
                              json={'username': 'test-owner', 'password': TEST_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(other.post('/api/auth/logout', headers={'X-CSRF-Token': csrf}).status_code, 403)

    def test_logout_revokes_copied_cookie(self):
        csrf = self.signed_in()
        stolen = self.client.cookies.get(security.COOKIE)
        self.assertEqual(self.client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf}).status_code, 200)
        self.client.cookies.set(security.COOKIE, stolen)
        self.assertEqual(self.client.get('/api/auth/session').status_code, 401)

    def test_new_login_rotates_previous_session(self):
        self.signed_in()
        old = self.client.cookies.get(security.COOKIE)
        self.assertEqual(self.login().status_code, 200)
        self.assertNotEqual(self.client.cookies.get(security.COOKIE), old)
        self.client.cookies.clear()
        self.client.cookies.set(security.COOKIE, old)
        self.assertEqual(self.client.get('/api/auth/session').status_code, 401)

    def test_tampered_expired_and_changed_credentials_fail(self):
        self.signed_in()
        saved = self.client.cookies.get(security.COOKIE)
        self.client.cookies.clear()
        self.client.cookies.set(security.COOKIE, saved + 'tampered')
        self.assertEqual(self.client.get('/api/auth/session').status_code, 401)
        sid = next(iter(self.database.sessions))
        with patch.object(TimestampSigner, 'get_timestamp', return_value=1):
            expired = TEST_SETTINGS.sign(sid, 'session')
        self.client.cookies.set(security.COOKIE, expired)
        self.assertEqual(self.client.get('/api/auth/session').status_code, 401)
        self.client.cookies.set(security.COOKIE, saved)
        for settings in (replace(TEST_SETTINGS, username='different'),
                         replace(TEST_SETTINGS, password_hash=security.PASSWORDS.hash('new-password-123')),
                         replace(TEST_SETTINGS, secret='new-secret-' * 5)):
            with patch.object(security, 'load_settings', return_value=settings):
                self.assertEqual(self.client.get('/api/auth/session').status_code, 401)

    def test_database_expiry_and_unavailability_fail_closed(self):
        self.signed_in()
        sid = next(iter(self.database.sessions))
        self.database.sessions[sid] = (TEST_SETTINGS.credential_tag, datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(self.client.get('/api/auth/session').status_code, 401)
        self.signed_in()
        with patch.object(self.database, 'valid', side_effect=OperationalError('secret-sql', {}, Exception('secret-db'))):
            response = self.client.get('/api/status')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('secret-', response.text)

    def test_redirects_stay_within_known_app_pages(self):
        for value in ('https://evil.example', '//evil.example', '/\\evil.example', '/login', '/unknown',
                      'http://[bad', '/sales\nLocation: evil', None):
            self.assertEqual(security.safe_next(value), '/')
        self.assertEqual(security.safe_next('/inventory?tab=history#end'), '/inventory?tab=history#end')
        self.assertEqual(self.login(target='https://evil.example').json()['next'], '/')

    def test_invalid_and_oversized_login_payload_never_echoes_password(self):
        headers = self.challenge()
        for body in (b'not-json', b'[]', b'null', b'{"password":"secret"}'):
            response = self.client.post('/api/auth/login', headers=headers, content=body)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('secret', response.text)
        self.assertEqual(self.client.post('/api/auth/login', headers=headers, content=b'x' * 16385).status_code, 413)

    def test_production_cookie_uses_secure_flag(self):
        with patch.object(security, 'load_settings', return_value=replace(TEST_SETTINGS, secure=True)):
            response = self.client.get('/api/auth/login')
        self.assertIn('Secure', response.headers['set-cookie'])


class AccountSetupTests(unittest.TestCase):
    def test_setup_preserves_other_env_values_and_stores_only_password_hash(self):
        from dotenv import dotenv_values
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('GEMINI_API_KEY=example-existing-value\nAUTH_COOKIE_SECURE=true\n')
            configure(path, 'shop-owner', TEST_PASSWORD)
            values = dotenv_values(path)
            self.assertEqual(values['GEMINI_API_KEY'], 'example-existing-value')
            self.assertEqual(values['AUTH_COOKIE_SECURE'], 'true')
            self.assertEqual(values['AUTH_USERNAME'], 'shop-owner')
            self.assertNotIn(TEST_PASSWORD, path.read_text())
            self.assertTrue(security.PASSWORDS.verify(TEST_PASSWORD, values['AUTH_PASSWORD_HASH']))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            previous_secret = values['AUTH_SESSION_SECRET']
            configure(path, 'shop-owner', TEST_PASSWORD)
            self.assertNotEqual(dotenv_values(path)['AUTH_SESSION_SECRET'], previous_secret)

    def test_invalid_password_does_not_modify_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            for user, password in (('', TEST_PASSWORD), ('owner', 'short')):
                with self.assertRaises(ValueError):
                    configure(path, user, password)
            self.assertFalse(path.exists())
