"""Password hashing, signed cookies and session-bound CSRF tokens."""
from dataclasses import dataclass
import hashlib
import hmac
from urllib.parse import urlsplit

from itsdangerous import URLSafeTimedSerializer, BadSignature
from pwdlib import PasswordHash

from bobaops.core.database import environment

PASSWORDS = PasswordHash.recommended()
COOKIE = 'bobaops_session'
CHALLENGE_COOKIE = 'bobaops_login_challenge'
SETUP_MESSAGE = 'Chưa thiết lập đăng nhập. Chạy python3 -m scripts.setup_login trên máy chủ.'


@dataclass(frozen=True)
class Settings:
    username: str
    password_hash: str
    secret: str
    secure: bool = False
    ttl: int = 28800
    origin: str = ''

    @property
    def configured(self):
        try:
            return (bool(self.username) and len(self.username) <= 100
                    and len(self.secret) >= 32 and 300 <= self.ttl <= 86400
                    and PASSWORDS.current_hasher.identify(self.password_hash))
        except (ValueError, TypeError):
            return False

    def sign(self, value, salt):
        return URLSafeTimedSerializer(self.secret, salt=salt).dumps(value)

    def unsign(self, value, salt, max_age):
        try:
            return URLSafeTimedSerializer(self.secret, salt=salt).loads(value, max_age=max_age)
        except (BadSignature, TypeError, ValueError):
            return None

    def digest(self, value, purpose):
        return hmac.new(self.secret.encode(), (purpose + ':' + value).encode(), hashlib.sha256).hexdigest()

    @property
    def credential_tag(self):
        return self.digest(self.username + '\0' + self.password_hash, 'credentials')


def load_settings():
    env = environment()
    try:
        ttl = int(env.get('AUTH_SESSION_TTL_SECONDS', '28800'))
    except (ValueError, TypeError):
        ttl = 0
    return Settings(env.get('AUTH_USERNAME') or '', env.get('AUTH_PASSWORD_HASH') or '',
                    env.get('AUTH_SESSION_SECRET') or '',
                    (env.get('AUTH_COOKIE_SECURE') or 'false').lower() == 'true', ttl,
                    env.get('AUTH_ALLOWED_ORIGIN') or env.get('RENDER_EXTERNAL_URL') or '')


def session_digest(sid):
    return hashlib.sha256(sid.encode()).hexdigest()


def safe_next(value):
    if not isinstance(value, str) or '\\' in value or any(ord(c) < 32 for c in value):
        return '/'
    try:
        parts = urlsplit(value)
    except ValueError:
        return '/'
    if parts.scheme or parts.netloc or value.startswith('//'):
        return '/'
    if parts.path not in {'/', '/upload', '/sales', '/inventory', '/setup', '/chat', '/dashboard'}:
        return '/'
    return value


def same_origin(request, settings):
    if request.headers.get('sec-fetch-site') == 'cross-site':
        return False
    supplied = request.headers.get('origin')
    if not supplied:
        return True  # Non-browser clients still must send a valid CSRF token.
    expected = settings.origin.rstrip('/') or str(request.base_url).rstrip('/')
    return supplied == expected
