"""Choose the single private account without putting passwords in shell history."""
import getpass
from pathlib import Path
import secrets

from dotenv import dotenv_values, set_key

from bobaops.auth.security import PASSWORDS
from bobaops.core.paths import ROOT


def configure(path: Path, username: str, password: str):
    username = username.strip()
    if not username or len(username) > 100 or any(ord(c) < 32 for c in username):
        raise ValueError('Username must contain 1–100 printable characters.')
    if not 12 <= len(password) <= 1024:
        raise ValueError('Use a password of at least 12 characters (maximum 1024).')
    hashed = PASSWORDS.hash(password)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    for name, value in {'AUTH_USERNAME': username, 'AUTH_PASSWORD_HASH': hashed,
                        'AUTH_SESSION_SECRET': secrets.token_urlsafe(48)}.items():
        set_key(path, name, value)
    current = dotenv_values(path)
    for name, value in {'AUTH_COOKIE_SECURE': 'false', 'AUTH_SESSION_TTL_SECONDS': '28800'}.items():
        if name not in current:
            set_key(path, name, value)
    path.chmod(0o600)


def main():
    path = ROOT / '.env'
    current = dotenv_values(path)
    default = current.get('AUTH_USERNAME') or 'admin'
    print('Set the private shop account. This also invalidates existing sessions.')
    username = input(f'Username [{default}]: ').strip() or default
    password = getpass.getpass('Password (at least 12 characters; hidden): ')
    confirmation = getpass.getpass('Confirm password: ')
    if password != confirmation:
        raise SystemExit('Passwords do not match. No settings changed.')
    try:
        configure(path, username, password)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print('Login configured in .env with a password hash. Restart the app, then open /login.')


if __name__ == '__main__':
    main()
