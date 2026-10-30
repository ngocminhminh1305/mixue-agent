import io
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

import app
from bobaops.extraction import routes as extraction


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)
        image = io.BytesIO()
        Image.new('RGB', (10, 10)).save(image, format='PNG')
        self.image = image.getvalue()

    def test_key_stays_on_server(self):
        with patch('bobaops.extraction.routes.settings', return_value=('secret-test-key', 'gemini-2.5-flash')):
            response = self.client.get('/api/status')
        self.assertTrue(response.json()['key_configured'])
        self.assertNotIn('secret-test-key', response.text)

    def test_missing_key(self):
        with patch('bobaops.extraction.routes.settings', return_value=('', 'gemini-2.5-flash')):
            response = self.client.post('/api/extract', files={'image': ('x.png', self.image)})
        self.assertEqual(response.status_code, 400)

    def test_invalid_image_never_calls_provider(self):
        with patch('bobaops.extraction.routes.settings', return_value=('test', 'gemini-2.5-flash')), patch('httpx.AsyncClient.post') as post:
            response = self.client.post('/api/extract', files={'image': ('x.png', b'not an image')})
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    def test_success_preserves_vietnamese_and_flags_duplicates(self):
        report = {'date': '2026-09-17', 'items': [
            {'item_name': 'Trà Đào (MT)', 'quantity': 21, 'issues': []},
            {'item_name': 'Trà Đào (MT)', 'quantity': None, 'issues': []},
        ], 'issues': []}
        import json
        reply = httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(report)}]}}]})
        with patch('bobaops.extraction.routes.settings', return_value=('test', 'gemini-2.5-flash')), patch('httpx.AsyncClient.post', return_value=reply) as post:
            response = self.client.post('/api/extract', files={'image': ('x.png', self.image)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'][0]['item_name'], 'Trà Đào (MT)')
        self.assertTrue(response.json()['items'][1]['issues'])
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs['headers']['x-goog-api-key'], 'test')

    def test_quota_error_is_safe_and_not_retried(self):
        reply = httpx.Response(429, json={'error': {'message': 'sensitive provider detail'}})
        with patch('bobaops.extraction.routes.settings', return_value=('test', 'gemini-2.5-flash')), patch('httpx.AsyncClient.post', return_value=reply) as post:
            response = self.client.post('/api/extract', files={'image': ('x.png', self.image)})
        self.assertEqual(response.status_code, 502)
        self.assertIn('quota', response.json()['detail'])
        self.assertNotIn('sensitive', response.text)
        post.assert_called_once()

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(ValueError):
            extraction.validate_report({'date': '2026-02-30', 'items': [], 'issues': []})

    def test_provider_error_explains_schema_failure_and_redacts_key(self):
        reply = httpx.Response(400, json={'error': {'message': 'Unsupported field responseJsonSchema secret-test-key'}})
        message = extraction.provider_error(reply, 'secret-test-key')
        self.assertIn('HTTP 400', message)
        self.assertIn('Unsupported field', message)
        self.assertNotIn('secret-test-key', message)

    def test_connection_timeout_has_network_guidance(self):
        with patch('bobaops.extraction.routes.settings', return_value=('test', 'gemini-3.5-flash-lite')), patch('httpx.AsyncClient.post', side_effect=httpx.ConnectTimeout('test')) as post:
            response = self.client.post('/api/extract', files={'image': ('x.png', self.image)})
        self.assertEqual(response.status_code, 504)
        self.assertIn('connection timeout', response.json()['detail'])
        post.assert_called_once()

    def test_response_timeout_is_distinct_from_connection_timeout(self):
        with patch('bobaops.extraction.routes.settings', return_value=('test', 'gemini-3.5-flash-lite')), patch('httpx.AsyncClient.post', side_effect=httpx.ReadTimeout('test')) as post:
            response = self.client.post('/api/extract', files={'image': ('x.png', self.image)})
        self.assertEqual(response.status_code, 504)
        self.assertIn('180 seconds', response.json()['detail'])
        post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
