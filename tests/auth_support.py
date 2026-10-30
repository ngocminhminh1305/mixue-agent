"""Test-only session storage; production always uses PostgreSQL."""
from datetime import datetime, timedelta, timezone
import secrets
from threading import Lock
from unittest.mock import patch

from fastapi.testclient import TestClient

from bobaops.auth import security, store

TEST_PASSWORD = 'Only-for-local-tests-1234'
TEST_SETTINGS = security.Settings('test-owner', security.PASSWORDS.hash(TEST_PASSWORD), 'test-secret-' * 5)


class MemoryStore:
    def __init__(self):
        self.sessions = {}
        self.attempts = {}
        self.lock = Lock()

    def create(self, sid, settings):
        with self.lock:
            self.sessions[sid] = (settings.credential_tag, datetime.now(timezone.utc) + timedelta(seconds=settings.ttl))

    def valid(self, sid, settings):
        with self.lock:
            tag, expiry = self.sessions.get(sid, ('', datetime.min.replace(tzinfo=timezone.utc)))
            return tag == settings.credential_tag and expiry > datetime.now(timezone.utc)

    def revoke(self, sid):
        with self.lock:
            self.sessions.pop(sid, None)

    def reserve_attempt(self, key):
        with self.lock:
            count = self.attempts.get(key, 0)
            if count >= 5:
                return 900
            self.attempts[key] = count + 1
            return 0

    def clear_attempts(self, key):
        with self.lock:
            self.attempts.pop(key, None)


def configure_auth(case, settings=TEST_SETTINGS):
    database = MemoryStore()
    for target, value in ((security, ('load_settings', settings)), (store, ('get_store', database))):
        name, result = value
        mocked = patch.object(target, name, return_value=result)
        mocked.start()
        case.addCleanup(mocked.stop)
    return database


def authenticated_client(case, application):
    database = configure_auth(case)
    sid = secrets.token_urlsafe(32)
    database.create(sid, TEST_SETTINGS)
    client = TestClient(application)
    client.cookies.set(security.COOKIE, TEST_SETTINGS.sign(sid, 'session'))
    client.headers['X-CSRF-Token'] = TEST_SETTINGS.digest(sid, 'csrf')
    case.addCleanup(client.close)
    return client
