"""Private storage boundaries and safe failures without real cloud requests."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from bobaops.core.photos import PhotoStore, PhotoStorageError, PhotoNotFound

REMOTE = {'RECEIPT_STORAGE': 'supabase', 'SUPABASE_URL': 'https://test-project.supabase.co',
          'SUPABASE_SECRET_KEY': 'sb_secret_test_only', 'SUPABASE_STORAGE_BUCKET': 'receipts'}
ORIGINAL_CLIENT = httpx.Client


class FakeStorage:
    def __init__(self):
        self.objects = {}
        self.public = False
        self.requests = []

    def handle(self, request):
        import json
        self.requests.append(request)
        assert request.headers['apikey'] == REMOTE['SUPABASE_SECRET_KEY']
        assert 'authorization' not in request.headers
        path = request.url.path
        if path == '/storage/v1/bucket/receipts':
            return httpx.Response(200, json={'id': 'receipts', 'public': self.public})
        if request.method == 'POST':
            assert request.headers['x-upsert'] == 'false'
            key = path.rsplit('/', 1)[1]
            if key in self.objects:
                return httpx.Response(409, json={'message': 'Already exists'})
            self.objects[key] = request.content
            return httpx.Response(200, json={'Key': key})
        if request.method == 'GET':
            key = path.rsplit('/', 1)[1]
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
        if request.method == 'DELETE':
            for key in json.loads(request.content)['prefixes']:
                self.objects.pop(key, None)
            return httpx.Response(200, json=[])
        return httpx.Response(500)


def remote_storage(case):
    fake = FakeStorage()
    for target, kwargs in [('bobaops.core.photos.environment', {'return_value': REMOTE}),
                           ('bobaops.core.photos.httpx.Client', {'side_effect': lambda **kw: ORIGINAL_CLIENT(
                               **kw, transport=httpx.MockTransport(fake.handle))})]:
        mocked = patch(target, **kwargs)
        mocked.start()
        case.addCleanup(mocked.stop)
    return fake


class PhotoStorageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_local_storage_is_exclusive_and_checks_paths(self):
        with patch('bobaops.core.photos.environment', return_value={'RECEIPT_STORAGE': 'local'}):
            store = PhotoStore(self.root)
        reference = store.write('one.png', b'photo')
        with self.assertRaises(FileExistsError):
            store.write('one.png', b'replacement')
        self.assertEqual((self.root / reference).read_bytes(), b'photo')
        for bad in ('../one.png', '/one.png', 'one.png?x=1', 'evil.html', 'one/../../one.png'):
            with self.assertRaises(PhotoNotFound):
                store.response(bad)
        store.delete(reference)
        self.assertFalse((self.root / reference).exists())

    def test_remote_cycle_uses_private_routes_and_does_not_write_local_files(self):
        fake = remote_storage(self)
        store = PhotoStore(self.root)
        reference = store.write('one.png', b'photo')
        self.assertEqual(reference, 'supabase:receipts/one.png')
        self.assertEqual(store.response(reference).body, b'photo')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertTrue(any('/object/authenticated/receipts/' in str(r.url) for r in fake.requests))
        store.delete(reference)
        self.assertFalse(fake.objects)
        with self.assertRaises(PhotoNotFound):
            store.response(reference)

    def test_public_bucket_is_rejected_before_upload(self):
        fake = remote_storage(self)
        fake.public = True
        with self.assertRaises(PhotoStorageError):
            PhotoStore(self.root).write('one.png', b'private')
        self.assertFalse(fake.objects)

    def test_provider_error_does_not_expose_key_or_response(self):
        with patch('bobaops.core.photos.environment', return_value=REMOTE), \
                patch('bobaops.core.photos.httpx.Client') as client:
            client.return_value.__enter__.return_value.request.return_value = httpx.Response(
                403, json={'detail': REMOTE['SUPABASE_SECRET_KEY']})
            with self.assertRaises(PhotoStorageError) as error:
                PhotoStore(self.root).check_private_bucket()
        self.assertNotIn(REMOTE['SUPABASE_SECRET_KEY'], str(error.exception))

    def test_missing_config_and_cross_bucket_paths_are_rejected(self):
        with patch('bobaops.core.photos.environment', return_value={'RECEIPT_STORAGE': 'supabase'}):
            with self.assertRaises(PhotoStorageError):
                PhotoStore(self.root).check_private_bucket()
        with patch('bobaops.core.photos.environment', return_value=REMOTE):
            with self.assertRaises(PhotoNotFound):
                PhotoStore(self.root).response('supabase:another-bucket/one.png')
