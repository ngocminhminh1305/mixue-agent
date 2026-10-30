"""Persistent session revocation and atomic login limits shared by all workers."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from bobaops.core.database import get_engine
from bobaops.auth.security import session_digest


class PostgresStore:
    def __init__(self, engine=None):
        self.engine = engine if engine is not None else get_engine()

    def create(self, sid, settings):
        with self.engine.begin() as conn:
            conn.execute(text('DELETE FROM auth_sessions WHERE expires_at <= CURRENT_TIMESTAMP'))
            conn.execute(text('''INSERT INTO auth_sessions
                (token_hash, username, credential_tag, expires_at)
                VALUES (:token, :username, :tag, :expires)'''),
                {'token': session_digest(sid), 'username': settings.username,
                 'tag': settings.credential_tag,
                 'expires': datetime.now(timezone.utc) + timedelta(seconds=settings.ttl)})

    def valid(self, sid, settings):
        with self.engine.connect() as conn:
            return bool(conn.scalar(text('''SELECT EXISTS(SELECT 1 FROM auth_sessions
                WHERE token_hash = :token AND username = :username AND credential_tag = :tag
                AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP)'''),
                {'token': session_digest(sid), 'username': settings.username, 'tag': settings.credential_tag}))

    def revoke(self, sid):
        with self.engine.begin() as conn:
            conn.execute(text('''UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP
                WHERE token_hash = :token AND revoked_at IS NULL'''), {'token': session_digest(sid)})

    def reserve_attempt(self, key):
        """Reserve before verifying a password; parallel requests cannot bypass the cap."""
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            conn.execute(text('''DELETE FROM auth_login_limits
                WHERE window_started_at < CURRENT_TIMESTAMP - INTERVAL '1 day' '''))
            conn.execute(text('''INSERT INTO auth_login_limits (client_hash, window_started_at, attempts)
                VALUES (:key, :now, 0) ON CONFLICT (client_hash) DO NOTHING'''), {'key': key, 'now': now})
            row = conn.execute(text('''SELECT window_started_at, attempts FROM auth_login_limits
                WHERE client_hash = :key FOR UPDATE'''), {'key': key}).one()
            elapsed = (now - row.window_started_at).total_seconds()
            count = row.attempts
            if elapsed >= 900:
                count = 0
                conn.execute(text('''UPDATE auth_login_limits SET window_started_at = :now
                    WHERE client_hash = :key'''), {'key': key, 'now': now})
            elif count >= 5:
                return max(1, int(900 - elapsed))
            conn.execute(text('UPDATE auth_login_limits SET attempts = :count WHERE client_hash = :key'),
                         {'key': key, 'count': count + 1})
        return 0

    def clear_attempts(self, key):
        with self.engine.begin() as conn:
            conn.execute(text('DELETE FROM auth_login_limits WHERE client_hash = :key'), {'key': key})


def get_store():
    return PostgresStore()
