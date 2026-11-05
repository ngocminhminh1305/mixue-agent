"""Private receipt storage: local for development, Supabase for ephemeral hosts."""
import re
from urllib.parse import urlsplit

import httpx
from fastapi.responses import FileResponse, Response

from bobaops.core.database import environment


class PhotoStorageError(Exception):
    """Never include provider responses, credentials or connection URLs in this error."""


class PhotoNotFound(PhotoStorageError):
    pass


class PhotoStore:
    def __init__(self, root):
        self.root = root
        env = environment()
        self.backend = env.get('RECEIPT_STORAGE') or 'local'
        self.url = (env.get('SUPABASE_URL') or '').rstrip('/')
        self.key = env.get('SUPABASE_SECRET_KEY') or ''
        self.bucket = env.get('SUPABASE_STORAGE_BUCKET') or 'receipts'
        if self.backend not in {'local', 'supabase'}:
            raise PhotoStorageError('Receipt storage is not configured correctly.')

    def remote_settings(self):
        try:
            parsed = urlsplit(self.url)
        except ValueError:
            raise PhotoStorageError('Configure the Supabase project URL.') from None
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {'', '/'} or not self.key
                or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', self.bucket)):
            raise PhotoStorageError('Configure the Supabase URL, server secret key and private receipt bucket.')

    def request(self, method, path, **kwargs):
        self.remote_settings()
        headers = {'apikey': self.key}
        # Legacy service_role JWTs additionally use Bearer authentication.
        if not self.key.startswith('sb_secret_'):
            headers['Authorization'] = 'Bearer ' + self.key
        headers.update(kwargs.pop('headers', {}))
        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False) as client:
                return client.request(method, self.url + '/storage/v1/' + path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise PhotoStorageError('Receipt storage could not be reached. Try again manually.') from None

    @staticmethod
    def checked_name(name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.(png|jpg|jpeg|webp)', name):
            raise PhotoNotFound('Receipt photo not found.')
        return name

    def remote_name(self, reference):
        prefix = 'supabase:' + self.bucket + '/'
        if not reference.startswith(prefix):
            raise PhotoNotFound('Receipt photo not found.')
        return self.checked_name(reference[len(prefix):])

    @staticmethod
    def mime(name):
        return {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}[name.rsplit('.', 1)[1]]

    def write(self, name, data):
        self.checked_name(name)
        if self.backend == 'local':
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / name
            try:
                with path.open('xb') as file:
                    file.write(data)
            except FileExistsError:
                raise
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return name
        self.check_private_bucket()
        response = self.request('POST', 'object/' + self.bucket + '/' + name, content=data,
                                headers={'Content-Type': self.mime(name), 'x-upsert': 'false'})
        if not response.is_success:
            raise PhotoStorageError('Receipt photo could not be saved. Check the private bucket and storage quota.')
        return 'supabase:' + self.bucket + '/' + name

    def response(self, reference):
        if reference.startswith('supabase:'):
            name = self.remote_name(reference)
            response = self.request('GET', 'object/authenticated/' + self.bucket + '/' + name)
            if response.status_code == 404:
                raise PhotoNotFound('Receipt photo not found.')
            if not response.is_success:
                raise PhotoStorageError('Receipt photo could not be loaded from storage.')
            return Response(response.content, media_type=self.mime(name), headers={'Cache-Control': 'no-store'})
        name = self.checked_name(reference)
        path = (self.root / name).resolve()
        if path.parent != self.root.resolve() or not path.is_file():
            raise PhotoNotFound('Receipt photo not found. Include receipt photos in your backups.')
        return FileResponse(path, headers={'Cache-Control': 'no-store'})

    def delete(self, reference):
        if reference.startswith('supabase:'):
            name = self.remote_name(reference)
            response = self.request('DELETE', 'object/' + self.bucket, json={'prefixes': [name]})
            if not response.is_success and response.status_code != 404:
                raise PhotoStorageError('Receipt photo cleanup is pending.')
        else:
            path = (self.root / self.checked_name(reference)).resolve()
            if path.parent != self.root.resolve():
                raise PhotoNotFound('Receipt photo not found.')
            path.unlink(missing_ok=True)

    def check_private_bucket(self):
        response = self.request('GET', 'bucket/' + self.bucket)
        try:
            valid = response.is_success and response.json().get('public') is False
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            raise PhotoStorageError('Configure an accessible private receipt bucket.')
